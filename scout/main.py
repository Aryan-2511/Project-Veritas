# scout/main.py
import os, time, asyncio, json, hashlib, sqlite3
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import feedparser, httpx
import redis.asyncio as redis  # ✅ modern async redis client
from dotenv import load_dotenv

# import shared helper
from common.descope_auth import require_delegated_token

load_dotenv()

DB_PATH = os.getenv("SCOUT_DB", "./data/scout.db")
RSSHUB_BASE = os.getenv("RSSHUB_BASE", "http://rsshub:1200")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
POLL_INTERVAL = int(os.getenv("SCOUT_POLL_INTERVAL", "60"))

app = FastAPI(title="veritas-scout")

# ----------------------------
# DB init
# ----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        source TEXT,
        url TEXT,
        created_at INTEGER
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subscription_id INTEGER,
        source TEXT,
        source_id TEXT,
        title TEXT,
        content TEXT,
        url TEXT,
        fetch_time INTEGER,
        fingerprint TEXT
    )""")
    conn.commit()
    conn.close()

init_db()

# in-memory tasks
tasks = {}

class SubscribeRequest(BaseModel):
    user_id: str
    source: str  # "arxiv" or "twitter"
    url: str

def fingerprint(content: str, url: str) -> str:
    m = hashlib.sha256()
    m.update((content + "|" + url).encode("utf-8"))
    return m.hexdigest()

# ----------------------------
# Redis helper
# ----------------------------
async def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)

# ----------------------------
# API routes
# ----------------------------
@app.post("/subscribe")
async def subscribe(
    req: SubscribeRequest,
    claims=Depends(require_delegated_token(
        required_scopes=["data:read:arxiv"],
        expected_aud=os.getenv("AUD_SCOUT")
    ))
):
    # persist subscription
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO subscriptions (user_id, source, url, created_at) VALUES (?, ?, ?, ?)",
        (req.user_id, req.source, req.url, int(time.time()))
    )
    sub_id = cur.lastrowid
    conn.commit()
    conn.close()

    # schedule poller
    if sub_id not in tasks:
        tasks[sub_id] = asyncio.create_task(poller_loop(sub_id, req.source, req.url))
    return {"subscription_id": sub_id}

# ----------------------------
# Polling logic
# ----------------------------
async def poller_loop(sub_id: int, source: str, url: str):
    r = await get_redis()
    while True:
        try:
            await fetch_and_store(sub_id, source, url, r)
        except Exception as e:
            print("poller error:", e)
        await asyncio.sleep(POLL_INTERVAL)

async def fetch_and_store(sub_id: int, source: str, url: str, r):
    feed_url = url
    if source == "twitter" and not url.startswith("http"):
        feed_url = f"{RSSHUB_BASE}/twitter/user/{url}"

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(feed_url)
        resp.raise_for_status()
        body = resp.text

    parsed = feedparser.parse(body)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for e in parsed.entries:
        content = e.get("summary") or e.get("description") or e.get("title") or ""
        link = e.get("link", "")
        sid = e.get("id", link)
        fp = fingerprint(content, link)

        cur.execute("SELECT 1 FROM items WHERE fingerprint = ?", (fp,))
        if cur.fetchone():
            continue

        cur.execute("""
            INSERT INTO items (subscription_id, source, source_id, title, content, url, fetch_time, fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (sub_id, source, sid, e.get("title",""), content, link, int(time.time()), fp))
        item_id = cur.lastrowid
        conn.commit()

        # push to redis queue for analyst
        await r.lpush(
            "veritas:scout:queue",
            json.dumps({"item_id": item_id, "subscription_id": sub_id})
        )
    conn.close()
