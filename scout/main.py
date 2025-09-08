# scout/main.py
import os
import time
import asyncio
import json
import hashlib
import sqlite3
from typing import Optional, List, Dict

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
import feedparser
import httpx
import redis.asyncio as redis
from dotenv import load_dotenv

from common.descope_auth import require_delegated_token
from common.audit import audit_insert

load_dotenv()

# Configuration
DB_PATH = os.getenv("SCOUT_DB", "./data/scout.db")
RSSHUB_BASE = os.getenv("RSSHUB_BASE", "http://rsshub:1200")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
POLL_INTERVAL = int(os.getenv("SCOUT_POLL_INTERVAL", "60"))
AUD_SCOUT = os.getenv("AUD_SCOUT")  # expected audience

QUEUE_KEY = "veritas:scout:queue"
ETAG_KEY_FMT = "veritas:sub:{sub_id}:etag"
LM_KEY_FMT = "veritas:sub:{sub_id}:last_modified"

app = FastAPI(title="veritas-scout")
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
            created_at INTEGER NOT NULL
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
    h = hashlib.sha256()
    h.update((content + "|" + url).encode("utf-8"))
    return h.hexdigest()


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


# -------------------------
# In-memory poller tasks
# -------------------------
poller_tasks: Dict[int, asyncio.Task] = {}
poller_lock = asyncio.Lock()


# -------------------------
# Helper to extract scopes
# -------------------------
def extract_scopes(claims: dict) -> set:
    if "nsec" in claims and "scope" in claims["nsec"]:
        return set(str(claims["nsec"]["scope"]).split())
    return set(str(claims.get("scope") or claims.get("scp") or "").split())


# -------------------------
# Endpoints
# -------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "time": int(time.time())}


@app.post("/subscribe")
async def subscribe(
    req: SubscribeRequest,
    claims: dict = Depends(require_delegated_token(required_scopes=None, expected_aud=AUD_SCOUT)),
):
    token_scopes = extract_scopes(claims)
    needed_scope = required_scope_for_source(req.source)
    if needed_scope not in token_scopes:
        raise HTTPException(status_code=403, detail=f"missing required scope: {needed_scope}")

    # normalize url
    feed_url = normalize_arxiv_url(req.url) if req.source == "arxiv" else normalize_twitter_url(req.url)

    # persist subscription
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO subscriptions (user_id, source, url, created_at) VALUES (?, ?, ?, ?)",
            (req.user_id, req.source, feed_url, int(time.time())),
        )
        sub_id = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="subscription already exists")
    conn.close()

    # schedule poller
    async with poller_lock:
        if sub_id not in poller_tasks:
            poller_tasks[sub_id] = asyncio.create_task(_poller_loop(sub_id, req.source, feed_url))

    # audit
    audit_insert(
        actor="scout",
        action="subscribe",
        user_id=req.user_id,
        audience=AUD_SCOUT,
        scope=needed_scope,
        jti=claims.get("jti"),
        outcome="success",
        details={"subscription_id": sub_id, "source": req.source, "url": feed_url},
    )

    return {"subscription_id": sub_id}


@app.get("/subscriptions")
async def list_subscriptions(
    user_id: Optional[str] = Query(None),
    claims: dict = Depends(require_delegated_token(required_scopes=None, expected_aud=AUD_SCOUT)),
):
    token_scopes = extract_scopes(claims)
    if not token_scopes.intersection({"data:read:arxiv", "data:read:twitter"}):
        raise HTTPException(status_code=403, detail="missing read scopes")

    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    if user_id:
        cur.execute("SELECT id, user_id, source, url, created_at FROM subscriptions WHERE user_id = ?", (user_id,))
    else:
        cur.execute("SELECT id, user_id, source, url, created_at FROM subscriptions")
    rows = cur.fetchall()
    conn.close()

    return [
        {"id": r[0], "user_id": r[1], "source": r[2], "url": r[3], "created_at": r[4]}
        for r in rows
    ]


@app.delete("/subscriptions/{sub_id}")
async def delete_subscription(
    sub_id: int,
    claims: dict = Depends(require_delegated_token(required_scopes=None, expected_aud=AUD_SCOUT)),
):
    token_scopes = extract_scopes(claims)
    if not token_scopes.intersection({"data:read:arxiv", "data:read:twitter"}):
        raise HTTPException(status_code=403, detail="missing read scopes")

    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()
    cur.execute("SELECT user_id, source, url FROM subscriptions WHERE id = ?", (sub_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="subscription not found")

    user_id, source, url = row
    cur.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
    conn.commit()
    conn.close()

    # cancel poller
    async with poller_lock:
        t = poller_tasks.pop(sub_id, None)
        if t and not t.done():
            t.cancel()

    audit_insert(
        actor="scout",
        action="unsubscribe",
        user_id=user_id,
        audience=AUD_SCOUT,
        scope="data:read:twitter" if source == "twitter" else "data:read:arxiv",
        jti=claims.get("jti"),
        outcome="success",
        details={"subscription_id": sub_id, "source": source, "url": url},
    )

    return {"status": "deleted"}


# -------------------------
# Poller implementation
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

    # conditional headers
    etag_key = ETAG_KEY_FMT.format(sub_id=sub_id)
    lm_key = LM_KEY_FMT.format(sub_id=sub_id)
    headers = {}
    try:
        etag = await r.get(etag_key)
        lm = await r.get(lm_key)
        if etag:
            headers["If-None-Match"] = etag
        if lm:
            headers["If-Modified-Since"] = lm
    except Exception:
        etag = lm = None

    resp = await client.get(feed_url, headers=headers)
    if resp.status_code == 304:
        return
    resp.raise_for_status()

    if resp.headers.get("ETag"):
        try: await r.set(etag_key, resp.headers.get("ETag"))
        except Exception: pass
    if resp.headers.get("Last-Modified"):
        try: await r.set(lm_key, resp.headers.get("Last-Modified"))
        except Exception: pass

    body = resp.text
    parsed = feedparser.parse(body)

    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()
    new_count = 0
    for e in parsed.entries:
        content = e.get("summary") or e.get("description") or e.get("title") or ""
        link = e.get("link") or ""
        sid = e.get("id", link) or link or (e.get("title", "")[:200])
        fp = fingerprint(content, link or sid)
        cur.execute("SELECT 1 FROM items WHERE fingerprint = ?", (fp,))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO items (subscription_id, source, source_id, title, content, url, fetch_time, fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sub_id, source, sid, e.get("title", ""), content, link, int(time.time()), fp),
        )
        item_id = cur.lastrowid
        conn.commit()
        new_count += 1
        try:
            await r.lpush(QUEUE_KEY, json.dumps({"item_id": item_id, "subscription_id": sub_id}))
        except Exception:
            print(f"[scout] failed to push item {item_id} to queue")
    conn.close()

    if new_count:
        audit_insert(
            actor="scout",
            action="fetch_items",
            user_id=None,
            audience=AUD_SCOUT,
            scope="data:read:twitter" if source == "twitter" else "data:read:arxiv",
            jti=None,
            outcome="success",
            details={"subscription_id": sub_id, "new_items": new_count},
        )


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
        for rrow in rows:
            sub_id, source, url = rrow
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
