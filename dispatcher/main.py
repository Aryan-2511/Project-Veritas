# dispatcher/main.py
import os
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import httpx
from common.descope_auth import require_delegated_token
from dotenv import load_dotenv

load_dotenv()
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")
AUD_DISPATCHER = os.getenv("AUD_DISPATCHER")

app = FastAPI(title="veritas-dispatcher")

class SlackRequest(BaseModel):
    user_id: str
    insight_id: str
    message: str

@app.post("/send_slack_alert")
async def send_slack(req: SlackRequest, claims = Depends(require_delegated_token(required_scopes=["notification:send:slack"], expected_aud=AUD_DISPATCHER))):
    if not SLACK_WEBHOOK:
        raise HTTPException(status_code=500, detail="Slack webhook not configured")
    async with httpx.AsyncClient() as client:
        r = await client.post(SLACK_WEBHOOK, json={"text": req.message})
        r.raise_for_status()
    return {"status":"sent"}
