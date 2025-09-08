# moderator/main.py
import os
import asyncio
import json
import sqlite3
import hashlib
import time
from typing import Optional, Dict, Any, Tuple

from fastapi import FastAPI, HTTPException, Request
from dotenv import load_dotenv
import redis.asyncio as redis
from groq import Groq
from fastapi.middleware.cors import CORSMiddleware

from common.audit import audit_insert

load_dotenv()

# Config
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
MODERATOR_QUEUE = os.getenv("MODERATOR_QUEUE_KEY", "veritas:moderator:queue")
ANALYST_QUEUE = os.getenv("SCOUT_QUEUE_KEY", "veritas:scout:queue")
MODERATOR_DB = os.getenv("MODERATOR_DB", "./data/moderator.db")
POLL_SLEEP_ON_ERROR = float(os.getenv("MODERATOR_POLL_BACKOFF", "2.0"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
LLM_CALL_DELAY = float(os.getenv("MODERATOR_LLM_CALL_DELAY", "2.0"))
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
AUD_MODERATOR = os.getenv("AUD_MODERATOR")

app = FastAPI(title="veritas-moderator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

groq_client = Groq(api_key=GROQ_API_KEY)
_redis: Optional[redis.Redis] = None

# ----------------------
# Redis connection
# ----------------------
async def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis

# ----------------------
# Initialize DB
# ----------------------
def init_db():
    conn = sqlite3.connect(MODERATOR_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS moderation_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      subscription_id INTEGER,
      user_id TEXT,
      item_title TEXT,
      item_url TEXT,
      content_hash TEXT,
      content_snippet TEXT,
      requested_at INTEGER NOT NULL,
      decision_allowed INTEGER NOT NULL,
      categories TEXT,
      reason TEXT,
      model_response TEXT,
      created_at INTEGER DEFAULT (strftime('%s','now'))
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_moderation_logs_content_hash ON moderation_logs(content_hash)")
    conn.commit()
    conn.close()

init_db()

# ----------------------
# Utility
# ----------------------
def compute_content_hash(title: str, content: str, url: str) -> str:
    text = (title + content + (url or "")).encode("utf-8")
    return hashlib.sha256(text).hexdigest()

def store_moderation_log(
    subscription_id: Optional[int],
    user_id: Optional[str],
    title: str,
    url: str,
    content: str,
    allowed: bool,
    categories: list,
    reason: str,
    model_response: str
) -> int:
    conn = sqlite3.connect(MODERATOR_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    content_hash = compute_content_hash(title, content, url)
    cur.execute("""
    INSERT INTO moderation_logs
    (subscription_id, user_id, item_title, item_url, content_hash, content_snippet, requested_at,
     decision_allowed, categories, reason, model_response)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        subscription_id,
        user_id,
        title,
        url,
        content_hash,
        content[:2000],
        int(time.time()),
        1 if allowed else 0,
        json.dumps(categories),
        reason,
        model_response
    ))
    rowid = cur.lastrowid
    conn.commit()
    conn.close()
    return rowid

# ----------------------
# LLM moderation
# ----------------------
def build_moderation_prompt(title: str, content: str) -> str:
    return f"""
You are a strict content moderation agent. Analyze the text and determine whether it is allowed.
Return ONLY a valid JSON object with keys:
- allowed: boolean
- categories: array of strings (possible values: "sexual","violent","criminal","hate","other")
- reason: short string

TITLE: {json.dumps((title or "")[:1000])}
CONTENT: {json.dumps((content or "")[:5000])}
Rules:
1) Output only JSON.
2) If unsure, block conservatively.
"""

async def call_groq_moderator(prompt: str) -> Dict[str, Any]:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")
    loop = asyncio.get_event_loop()
    def run_sync():
        return groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_completion_tokens=256,
            top_p=1,
            reasoning_effort="medium",
            stream=False,
        )
    resp = await loop.run_in_executor(None, run_sync)
    try:
        return {"text": resp.choices[0].message.content}
    except Exception:
        return {"text": json.dumps(resp, default=str)}

def parse_llm_response(resp: Dict[str, Any]) -> Tuple[bool, list, str, str]:
    raw = resp.get("text", "")
    try:
        parsed = json.loads(raw)
        allowed = bool(parsed.get("allowed", False))
        categories = parsed.get("categories", [])
        reason = parsed.get("reason", "")
        return allowed, categories, reason, raw
    except Exception:
        return False, ["other"], "parse_error_or_timeout", raw

# ----------------------
# Process item
# ----------------------
async def process_item(obj: Dict[str, Any], retries: int = 2):
    item_id = obj.get("item_id")
    subscription_id = obj.get("subscription_id")
    title = obj.get("title", "")
    content = obj.get("content", "")
    url = obj.get("url", "")

    prompt = build_moderation_prompt(title, content)
    allowed = False
    categories = []
    reason = ""
    raw_resp = ""

    for attempt in range(retries):
        try:
            resp = await asyncio.wait_for(call_groq_moderator(prompt), timeout=LLM_TIMEOUT)
            allowed, categories, reason, raw_resp = parse_llm_response(resp)
            break
        except asyncio.TimeoutError:
            reason = f"timeout_attempt_{attempt+1}"
        except Exception as e:
            reason = str(e)
        await asyncio.sleep(1.0 * (attempt + 1))

    log_id = store_moderation_log(subscription_id, None, title, url, content, allowed, categories, reason, raw_resp)
    print(f"[moderator] logged moderation {log_id} -> allowed={allowed}")

    if allowed:
        r = await get_redis()
        await r.lpush(ANALYST_QUEUE, json.dumps({
            "item_id": item_id,
            "subscription_id": subscription_id
        }))
        print(f"[moderator] item {item_id} forwarded to analyst queue")

    await asyncio.sleep(LLM_CALL_DELAY)

# ----------------------
# Consumer loop
# ----------------------
async def consume_loop():
    r = await get_redis()
    while True:
        try:
            item = await r.brpop(MODERATOR_QUEUE, timeout=0)
            if not item:
                await asyncio.sleep(0.2)
                continue
            _, payload = item
            try:
                obj = json.loads(payload)
            except Exception:
                print("[moderator] invalid payload:", payload)
                continue
            await process_item(obj)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print("[moderator] consume_loop error:", e)
            await asyncio.sleep(POLL_SLEEP_ON_ERROR)

consumer_task: Optional[asyncio.Task] = None

@app.on_event("startup")
async def startup_event():
    global consumer_task
    consumer_task = asyncio.create_task(consume_loop())
    print("[moderator] consumer started")

@app.on_event("shutdown")
async def shutdown_event():
    global consumer_task
    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except Exception:
            pass
    print("[moderator] consumer stopped")

# ----------------------
# API endpoints
# ----------------------
@app.post("/moderate_now")
async def moderate_now(request: Request):
    body = await request.json()
    title = body.get("title", "")
    content = body.get("content", "")
    url = body.get("url", "")
    await process_item({"item_id": -1, "subscription_id": None, "title": title, "content": content, "url": url})
    return {"status": "ok"}
