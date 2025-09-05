# analyst/main.py
import os, asyncio, json, sqlite3, time
from fastapi import FastAPI
from dotenv import load_dotenv
import redis.asyncio as redis

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DB_PATH = os.getenv("SCOUT_DB", "./data/scout.db")
INSIGHT_DB = os.getenv("ANALYST_DB", "./data/analyst.db")

app = FastAPI(title="veritas-analyst")


def init_insight_db():
    conn = sqlite3.connect(INSIGHT_DB)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            insight_type TEXT,
            score REAL,
            summary TEXT,
            evidence TEXT,
            created_at INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


init_insight_db()


async def consume_loop():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    while True:
        try:
            # BRPOP returns (key, value)
            item = await r.brpop("veritas:scout:queue", timeout=0)
            if not item:
                await asyncio.sleep(1)
                continue
            _, payload = item
            obj = json.loads(payload)
            await process_item(obj)
        except Exception as e:
            print("consume_loop error:", e)
            await asyncio.sleep(2)


async def process_item(obj):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, source, title, content, url, fetch_time FROM items WHERE id=?",
        (obj["item_id"],),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return

    item_id, source, title, content, url, fetch_time = row

    if source == "arxiv":
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        window = int(time.time()) - 3600
        cur.execute(
            """
            SELECT id, source, title, url, fetch_time FROM items
            WHERE source='twitter' AND fetch_time > ? 
              AND (title LIKE ? OR content LIKE ?)
            """,
            (window, f"%{title[:30]}%", f"%{title[:30]}%"),
        )
        matches = cur.fetchall()
        conn.close()

        score = min(1.0, 0.2 * len(matches))
        if score > 0:
            summary = (
                f"Research Momentum: arXiv paper appears to be gaining attention "
                f"({len(matches)} references)."
            )
            evidence = {
                "paper": {"id": item_id, "url": url},
                "tweets": [{"id": m[0], "url": m[3]} for m in matches],
            }
            conn = sqlite3.connect(INSIGHT_DB)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO insights (insight_type, score, summary, evidence, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "research_momentum",
                    score,
                    summary,
                    json.dumps(evidence),
                    int(time.time()),
                ),
            )
            conn.commit()
            conn.close()
            print("Generated insight:", summary)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(consume_loop())


@app.get("/insights")
def get_insights():
    conn = sqlite3.connect(INSIGHT_DB)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, insight_type, score, summary, evidence, created_at
        FROM insights ORDER BY created_at DESC LIMIT 50
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "type": r[1],
            "score": r[2],
            "summary": r[3],
            "evidence": json.loads(r[4]),
            "created_at": r[5],
        }
        for r in rows
    ]
