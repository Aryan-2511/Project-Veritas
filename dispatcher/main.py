import os
import time
import json
import sqlite3
import smtplib
from email.mime.text import MIMEText
from fastapi import FastAPI
from pydantic import BaseModel
import redis
import threading

# === Config ===
DISPATCHER_DB = "data/dispatcher.db"
ANALYST_DB = "data/analyst.db"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DISPATCHER_QUEUE = os.getenv("DISPATCHER_QUEUE", "veritas:dispatcher:queue")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@example.com")

app = FastAPI()

# === Database Init ===
def init_db():
    conn = sqlite3.connect(DISPATCHER_DB)
    cur = conn.cursor()
    with open("sql/create_dispatcher_tables.sql") as f:
        cur.executescript(f.read())
    conn.commit()
    conn.close()

init_db()

# === Email Sending ===
def send_email_with_retries(recipient: str, subject: str, body: str, retries=3):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = recipient

    for attempt in range(retries):
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"[dispatcher] Email send error (attempt {attempt+1}): {e}")
            time.sleep(2)
    return False

# === Redis Consumer ===
def process_dispatch_payload(payload: dict):
    """Store new insights into pending_dispatch instead of sending immediately"""
    insight_id = payload.get("insight_id")
    user_id = payload.get("user_id")
    subscription_id = payload.get("subscription_id")
    score = payload.get("score", 0.0)
    created_at = int(time.time())

    conn = sqlite3.connect(DISPATCHER_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pending_dispatch (user_id, subscription_id, insight_id, score, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, subscription_id, insight_id, score, created_at))
    conn.commit()
    conn.close()

def consume_loop():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    print(f"[dispatcher] Listening on queue: {DISPATCHER_QUEUE}")

    while True:
        try:
            _, payload = r.brpop(DISPATCHER_QUEUE, timeout=0)
            data = json.loads(payload)
            process_dispatch_payload(data)
        except Exception as e:
            print(f"[dispatcher] Error processing payload: {e}")
            time.sleep(1)

# === Weekly Digest Job ===
def send_weekly_digest():
    conn = sqlite3.connect(DISPATCHER_DB, timeout=30, check_same_thread=False)
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id, subscription_id
        FROM pending_dispatch
        GROUP BY user_id, subscription_id
    """)
    groups = cur.fetchall()

    for user_id, sub_id in groups:
        cur.execute("""
            SELECT insight_id, score
            FROM pending_dispatch
            WHERE user_id=? AND subscription_id=?
            ORDER BY score DESC
            LIMIT 10
        """, (user_id, sub_id))
        top_insights = cur.fetchall()

        # fetch full details from Analyst DB
        ac = sqlite3.connect(ANALYST_DB)
        ac_cur = ac.cursor()
        insights = []
        for iid, score in top_insights:
            ac_cur.execute("SELECT insight_type, summary FROM insights WHERE id=?", (iid,))
            row = ac_cur.fetchone()
            if row:
                insights.append((row[0], score, row[1]))
        ac.close()

        if insights:
            subject = f"Weekly Digest: Top Insights for Subscription {sub_id}"
            body = "\n\n".join(
                [f"[{itype}] (score {s:.2f})\n{summ}" for itype, s, summ in insights]
            )
            body = f"Here are your top {len(insights)} insights this week:\n\n{body}"

            cur.execute("SELECT email FROM user_contacts WHERE user_id=?", (user_id,))
            row = cur.fetchone()
            if row:
                send_email_with_retries(row[0], subject, body)

        cur.execute("""
            INSERT INTO sent_digests (user_id, subscription_id, sent_at)
            VALUES (?, ?, ?)
        """, (user_id, sub_id, int(time.time())))

    cur.execute("DELETE FROM pending_dispatch")
    conn.commit()
    conn.close()
    print("[dispatcher] Weekly digest sent")

def digest_scheduler():
    while True:
        now = time.localtime()
        # run every Monday at 09:00
        if now.tm_wday == 0 and now.tm_hour == 9 and now.tm_min < 5:
            send_weekly_digest()
            time.sleep(3600)  # avoid duplicate sends within the hour
        time.sleep(60)

# === Startup Tasks ===
@app.on_event("startup")
def startup_event():
    threading.Thread(target=consume_loop, daemon=True).start()
    threading.Thread(target=digest_scheduler, daemon=True).start()

# === API ===
class Contact(BaseModel):
    user_id: str
    email: str

@app.post("/contacts")
def add_contact(contact: Contact):
    conn = sqlite3.connect(DISPATCHER_DB)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO user_contacts (user_id, email)
        VALUES (?, ?)
    """, (contact.user_id, contact.email))
    conn.commit()
    conn.close()
    return {"status": "ok"}
