# scout/main.py (LLM-only moderation)
import os
import time
import asyncio
import json
import hashlib
import sqlite3
from typing import Optional, Dict

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
import feedparser
import httpx
import redis.asyncio as redis
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

from common.descope_auth import require_delegated_token
from common.audit import audit_insert

load_dotenv()

DB_PATH = os.getenv("SCOUT_DB", "./data/scout.db")
RSSHUB_BASE = os.getenv("RSSHUB_BASE", "http://rsshub:1200")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
POLL_INTERVAL = int(os.getenv("SCOUT_POLL_INTERVAL", "60"))
AUD_SCOUT = os.getenv("AUD_SCOUT")
CONCIERGE_DELEGATE_URL = os.getenv("CONCIERGE_DELEGATE_URL", "http://concierge:8080/delegate")
MODERATOR_URL = os.getenv("MODERATOR_URL", "http://moderator:8080/moderate")
MODERATOR_TOKEN_EXPIRES_IN = int(os.getenv("MODERATOR_TOKEN_EXPIRES_IN", "300"))
MODERATOR_TOKEN_CACHE_BUFFER = int(os.getenv("MODERATOR_TOKEN_CACHE_BUFFER", "10"))
MODERATOR_CALL_TIMEOUT = float(os.getenv("MODERATOR_CALL_TIMEOUT", "10.0"))

QUEUE_KEY = "veritas:scout:queue"
ETAG_KEY_FMT = "veritas:sub:{sub_id}:etag"
LM_KEY_FMT = "veritas:sub:{sub_id}:last_modified"
MOD_TOKEN_CACHE_KEY = "veritas:sub:{sub_id}:mod_token"

app = FastAPI(title="veritas-scout")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

# -------------------------
# DB initialization
# -------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            url TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            session_jwt TEXT
        );
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_user_source_url
        ON subscriptions(user_id, source, url);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER,
            source TEXT NOT NULL,
            source_id TEXT,
            title TEXT,
            content TEXT,
            url TEXT,
            fetch_time INTEGER,
            fingerprint TEXT,
            FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS items_subscription_idx ON items(subscription_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS items_fetch_time_idx ON items(fetch_time);")
    conn.commit()
    conn.close()

init_db()

# -------------------------
# Utils
# -------------------------
def fingerprint(content: str, url: str) -> str:
    return hashlib.sha256((content + "|" + url).encode("utf-8")).hexdigest()


async def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)


def normalize_arxiv_url(url: str) -> str:
    return url if url.startswith("http") else f"https://export.arxiv.org/rss/{url}"


def normalize_twitter_url(url: str) -> str:
    if url.startswith("http"):
        return url
    handle = url.lstrip("@")
    return f"{RSSHUB_BASE}/twitter/user/{handle}"


def required_scope_for_source(source: str) -> str:
    return "data:read:twitter" if source == "twitter" else "data:read:arxiv"


# -------------------------
# Models
# -------------------------
class SubscribeRequest(BaseModel):
    user_id: str
    source: str  # 'arxiv' | 'twitter'
    url: str
    user_session_jwt: Optional[str] = None


# -------------------------
# In-memory poller tasks
# -------------------------
poller_tasks: Dict[int, asyncio.Task] = {}
poller_lock = asyncio.Lock()


# -------------------------
# Moderator token helpers
# -------------------------
async def get_cached_mod_token(sub_id: int) -> Optional[str]:
    try:
        r = await get_redis()
        return await r.get(MOD_TOKEN_CACHE_KEY.format(sub_id=sub_id))
    except Exception:
        return None


async def cache_mod_token(sub_id: int, token: str, ttl: int):
    try:
        r = await get_redis()
        await r.set(MOD_TOKEN_CACHE_KEY.format(sub_id=sub_id), token, ex=ttl)
    except Exception:
        pass


async def request_mod_token_from_concierge(session_jwt: str, sub_id: int, expires_in: int = MODERATOR_TOKEN_EXPIRES_IN) -> Optional[str]:
    if not session_jwt:
        return None
    payload = {"target": "moderator", "scopes": ["moderation:perform"], "expires_in": expires_in}
    headers = {"Authorization": f"Bearer {session_jwt}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(CONCIERGE_DELEGATE_URL, json=payload, headers=headers)
        data = resp.json()
        token = data.get("access_token") or data.get("jwt") or (data.get("sessionToken") if isinstance(data.get("sessionToken"), str) else data.get("sessionToken", {}).get("jwt"))
        if token:
            await cache_mod_token(sub_id, token, max(1, expires_in - MODERATOR_TOKEN_CACHE_BUFFER))
        return token
    except Exception:
        return None


async def ensure_moderator_token(sub_id: int, session_jwt: Optional[str]) -> Optional[str]:
    tok = await get_cached_mod_token(sub_id)
    if tok:
        return tok
    if not session_jwt:
        return None
    return await request_mod_token_from_concierge(session_jwt, sub_id)


# -------------------------
# Poller
# -------------------------
async def _poller_loop(sub_id: int, source: str, url: str):
    r = await get_redis()
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                await _fetch_and_store_once(sub_id, source, url, client, r)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[scout] poller error for sub {sub_id}: {exc}")
            await asyncio.sleep(POLL_INTERVAL)


async def _fetch_and_store_once(sub_id: int, source: str, url: str, client: httpx.AsyncClient, r: redis.Redis):
    feed_url = normalize_arxiv_url(url) if source == "arxiv" else normalize_twitter_url(url)
    headers = {}
    try:
        etag = await r.get(ETAG_KEY_FMT.format(sub_id=sub_id))
        lm = await r.get(LM_KEY_FMT.format(sub_id=sub_id))
        if etag:
            headers["If-None-Match"] = etag
        if lm:
            headers["If-Modified-Since"] = lm
    except Exception:
        pass

    resp = await client.get(feed_url, headers=headers)
    if resp.status_code == 304:
        return
    resp.raise_for_status()

    if resp.headers.get("ETag"):
        try: await r.set(ETAG_KEY_FMT.format(sub_id=sub_id), resp.headers.get("ETag"))
        except Exception: pass
    if resp.headers.get("Last-Modified"):
        try: await r.set(LM_KEY_FMT.format(sub_id=sub_id), resp.headers.get("Last-Modified"))
        except Exception: pass

    body = resp.text
    parsed = feedparser.parse(body)

    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    # fetch session_jwt for moderator
    cur.execute("SELECT session_jwt FROM subscriptions WHERE id = ?", (sub_id,))
    session_jwt = cur.fetchone()[0] if cur.fetchone() else None

    for e in parsed.entries:
        content = e.get("summary") or e.get("description") or e.get("title") or ""
        link = e.get("link") or ""
        sid = e.get("id", link) or link
        fp = fingerprint(content, link or sid)
        cur.execute("SELECT 1 FROM items WHERE fingerprint = ?", (fp,))
        if cur.fetchone():
            continue

        mod_token = await ensure_moderator_token(sub_id, session_jwt)
        if not mod_token:
            # Skip items if moderator token unavailable
            print(f"[scout] skipping item for sub {sub_id} due to missing moderator token")
            continue

        # call moderator endpoint
        mod_headers = {"Authorization": f"Bearer {mod_token}", "Content-Type": "application/json"}
        mod_payload = {"item_id": None, "title": e.get("title",""), "content": content, "url": link}
        allowed = False
        try:
            mod_resp = await client.post(MODERATOR_URL, json=mod_payload, headers=mod_headers, timeout=MODERATOR_CALL_TIMEOUT)
            if mod_resp.status_code == 200:
                allowed = bool(mod_resp.json().get("allowed", False))
        except Exception as ex:
            print(f"[scout] moderator call failed: {ex}")
            allowed = False

        if not allowed:
            continue  # strictly LLM moderation: disallowed items never enter queue

        # persist allowed item
        cur.execute(
            "INSERT INTO items (subscription_id, source, source_id, title, content, url, fetch_time, fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sub_id, source, sid, e.get("title", ""), content, link, int(time.time()), fp),
        )
        item_id = cur.lastrowid
        conn.commit()
        try:
            await r.lpush(QUEUE_KEY, json.dumps({"item_id": item_id, "subscription_id": sub_id}))
        except Exception:
            print(f"[scout] failed to push item {item_id} to queue")

    conn.close()


# -------------------------
# Startup / Shutdown
# -------------------------
@app.on_event("startup")
async def startup_event():
    try:
        r = await get_redis()
        await r.ping()
    except Exception:
        print("[scout] warning: cannot connect to redis at startup")

    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("SELECT id, source, url FROM subscriptions")
    rows = cur.fetchall()
    conn.close()

    async with poller_lock:
        for sub_id, source, url in rows:
            if sub_id not in poller_tasks:
                poller_tasks[sub_id] = asyncio.create_task(_poller_loop(sub_id, source, url))
    print(f"[scout] started {len(poller_tasks)} poller(s)")


@app.on_event("shutdown")
async def shutdown_event():
    async with poller_lock:
        tasks = list(poller_tasks.items())
        for sub_id, task in tasks:
            if task and not task.done():
                task.cancel()
        await asyncio.sleep(0.1)
    print("[scout] shutdown: cancelled pollers")
