# analyst/main.py
import os
import asyncio
import json
import sqlite3
import time
import traceback
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
import redis.asyncio as redis
from groq import Groq  # ✅ official Groq client
from fastapi.middleware.cors import CORSMiddleware

from common.audit import audit_insert

load_dotenv()

# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SCOUT_DB = os.getenv("SCOUT_DB", "./data/scout.db")
ANALYST_DB = os.getenv("ANALYST_DB", "./data/analyst.db")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
QUEUE_KEY = os.getenv("SCOUT_QUEUE_KEY", "veritas:scout:queue")
DISPATCHER_QUEUE = os.getenv("DISPATCHER_QUEUE", "veritas:dispatcher:queue")
POLL_SLEEP_ON_ERROR = float(os.getenv("ANALYST_POLL_BACKOFF", "2.0"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
LLM_CALL_DELAY = float(os.getenv("ANALYST_LLM_CALL_DELAY", "2.0"))  # seconds

if not GROQ_API_KEY:
    print("[analyst] WARNING: GROQ_API_KEY not set. LLM calls will fail until configured.")

app = FastAPI(title="veritas-analyst")

# ✅ Allow frontend (Vite dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # or ["*"] for dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Groq client (sync — run in executor for async)
groq_client = Groq(api_key=GROQ_API_KEY)

redis_client: Optional[redis.Redis] = None

async def get_redis():
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client

# Ensure DB exists & schema
def init_insight_db():
    conn = sqlite3.connect(ANALYST_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            insight_type TEXT,
            score REAL,
            summary TEXT,
            evidence TEXT,
            recommended_action TEXT,
            raw_response TEXT,
            subscription_id INTEGER,
            user_id TEXT,
            created_at INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_insight_db()

# Utility: load item from scout DB
def load_item(item_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(SCOUT_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, subscription_id, source, source_id, title, content, url, fetch_time FROM items WHERE id = ?",
        (item_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "subscription_id": row[1],
        "source": row[2],
        "source_id": row[3],
        "title": row[4],
        "content": row[5],
        "url": row[6],
        "fetch_time": row[7],
    }

# Build structured prompt
def build_insight_prompt(item: Dict[str, Any]) -> str:
    title = item.get("title") or ""
    content = item.get("content") or ""
    url = item.get("url") or ""
    source = item.get("source") or ""
    fetch_time = item.get("fetch_time") or 0

    return f"""
You are an objective analyst assistant. Given a single `item` fetched from a data feed, produce
a JSON object (and only the JSON object) with the following keys:

- "insight_type": one of ["research_momentum","novel_finding","contrarian_signal","actionable_advice","other"]
- "score": a number between 0.0 and 1.0 indicating confidence/importance
- "summary": a short summary (max 200 characters)
- "evidence": a list of objects: {{ "type": "tweet"|"paper"|"other", "id": "<id>", "url": "<url>", "note": "<note>" }}
- "recommended_action": short, specific next-step
- "reasoning": optional one-line reason

Item:
- source: {source}
- title: {json.dumps(title)}
- content: {json.dumps(content[:2000])}
- url: {url}
- fetch_time: {fetch_time}

Rules:
1. Output MUST be valid JSON only (no markdown, no explanations).
2. If no clear insight, use "score": 0.0, "insight_type": "other", "summary": "".
"""

# Call Groq API
async def call_groq_model(prompt: str) -> Dict[str, Any]:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")

    loop = asyncio.get_event_loop()

    def run_sync():
        return groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_completion_tokens=1024,
            top_p=1,
            reasoning_effort="medium",
            stream=False,
        )

    resp = await loop.run_in_executor(None, run_sync)

    try:
        return {"text": resp.choices[0].message.content}
    except Exception:
        return {"text": json.dumps(resp, default=str)}

def parse_llm_response(resp: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    raw_text = resp.get("text")
    if not raw_text:
        return None, None
    try:
        parsed = json.loads(raw_text)
        return parsed, raw_text
    except Exception:
        return None, raw_text

def store_insight(insight: Dict[str, Any], raw_response: Optional[str] = None) -> int:
    conn = sqlite3.connect(ANALYST_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO insights
        (insight_type, score, summary, evidence, recommended_action, raw_response,
         subscription_id, user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        insight.get("insight_type"),
        float(insight.get("score", 0.0)),
        insight.get("summary"),
        json.dumps(insight.get("evidence", [])),
        insight.get("recommended_action"),
        raw_response,
        insight.get("subscription_id"),
        insight.get("user_id"),
        int(time.time()),
    ))
    rowid = cur.lastrowid
    conn.commit()
    conn.close()
    return rowid

async def notify_dispatcher(insight: Dict[str, Any], insight_id: int):
    try:
        r = await get_redis()
        payload = {
            "insight_id": insight_id,
            "subscription_id": insight.get("subscription_id"),
            "user_id": insight.get("user_id"),
            "score": insight.get("score"),
            "summary": insight.get("summary"),
        }
        await r.lpush(DISPATCHER_QUEUE, json.dumps(payload))
    except Exception:
        print(f"[analyst] notify_dispatcher failed for {insight_id}")

# ---------------------
# Process single item with delay
# ---------------------
async def process_item(obj: Dict[str, Any], retries: int = 3):
    item_id = int(obj.get("item_id", -1))
    item = load_item(item_id)
    if not item:
        print(f"[analyst] item {item_id} not found")
        return

    prompt = build_insight_prompt(item)

    raw_resp = None
    parsed = None

    for attempt in range(retries):
        try:
            resp = await asyncio.wait_for(call_groq_model(prompt), timeout=LLM_TIMEOUT)
            parsed, raw_resp = parse_llm_response(resp)
            break  # success
        except asyncio.TimeoutError:
            print(f"[analyst] LLM call timed out on attempt {attempt + 1}")
            parsed = None
        except Exception as e:
            print(f"[analyst] LLM call failed on attempt {attempt + 1}: {e}")
            await asyncio.sleep(2 ** attempt)  # exponential backoff

    if not parsed:
        parsed = {
            "insight_type": "other",
            "score": 0.0,
            "summary": "",
            "evidence": [],
            "recommended_action": "no action",
        }

    # fetch user_id from subscription
    conn = sqlite3.connect(SCOUT_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM subscriptions WHERE id = ?", (item["subscription_id"],))
    sub_row = cur.fetchone()
    conn.close()
    user_id = sub_row[0] if sub_row else None
    if not user_id:
        print(f"[analyst] subscription {item['subscription_id']} has no user_id, skipping")
        return

    insight = {
        "insight_type": parsed.get("insight_type", "other"),
        "score": round(float(parsed.get("score", 0.0)), 2),
        "summary": parsed.get("summary", "")[:1000],
        "evidence": parsed.get("evidence", []),
        "recommended_action": parsed.get("recommended_action", ""),
        "subscription_id": item.get("subscription_id"),
        "user_id": user_id,
    }

    try:
        insight_id = store_insight(insight, raw_resp)
        audit_insert(
            actor="analyst",
            action="insight_created",
            user_id=user_id,
            audience=os.getenv("AUD_ANALYST"),
            scope="analysis:perform",
            jti=None,
            outcome="success",
            details={"insight_id": insight_id, "item_id": item_id, "score": insight["score"]},
        )
        await notify_dispatcher(insight, insight_id)
        print(f"[analyst] stored insight {insight_id} for item {item_id}")
    except Exception:
        traceback.print_exc()

    # Delay to prevent rate limiting
    await asyncio.sleep(LLM_CALL_DELAY)

# ---------------------
# Consumer loop
# ---------------------
async def consume_loop():
    r = await get_redis()
    while True:
        try:
            _, payload = await r.brpop(QUEUE_KEY, timeout=0)
            obj = json.loads(payload)
            await process_item(obj)
        except Exception as e:
            print("[analyst] consume_loop error:", e)
            await asyncio.sleep(POLL_SLEEP_ON_ERROR)

consumer_task: Optional[asyncio.Task] = None

@app.on_event("startup")
async def startup_event():
    global consumer_task
    try:
        r = await get_redis()
        await r.ping()
    except Exception:
        print("[analyst] warning: cannot connect to redis")
    consumer_task = asyncio.create_task(consume_loop())

@app.on_event("shutdown")
async def shutdown_event():
    global consumer_task
    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except Exception:
            pass

# ---------------------
# API endpoints
# ---------------------
@app.get("/insights")
def get_insights(limit: int = 50):
    conn = sqlite3.connect(ANALYST_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, insight_type, score, summary, evidence, recommended_action, subscription_id, user_id, created_at "
        "FROM insights ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "type": r[1],
            "score": r[2],
            "summary": r[3],
            "evidence": json.loads(r[4]) if r[4] else [],
            "recommended_action": r[5],
            "subscription_id": r[6],
            "user_id": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]

@app.post("/analyze_now/{item_id}")
async def analyze_now(item_id: int):
    # Check moderation
    conn = sqlite3.connect(SCOUT_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("SELECT allowed FROM moderated_items WHERE item_id = ?", (item_id,))
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        raise HTTPException(status_code=403, detail="Item not approved by moderator")

    await process_item({"item_id": item_id, "subscription_id": None})
    return {"status": "processed", "item_id": item_id}