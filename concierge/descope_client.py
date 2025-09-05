# concierge/descope_client.py
import os
import httpx
from descope import DescopeClient
from typing import List, Optional

DESCOPE_PROJECT_ID = os.getenv("DESCOPE_PROJECT_ID")
DESCOPE_BASE_URI = os.getenv("DESCOPE_BASE_URI", "https://api.descope.com")
DESCOPE_TOKEN_EXCHANGE_URL = os.getenv("DESCOPE_TOKEN_EXCHANGE_URL")
CONCIERGE_CLIENT_ID = os.getenv("DESCOPE_CONCIERGE_CLIENT_ID")
CONCIERGE_CLIENT_SECRET = os.getenv("DESCOPE_CONCIERGE_CLIENT_SECRET")

descope_client = DescopeClient(project_id=DESCOPE_PROJECT_ID, base_url=DESCOPE_BASE_URI)

def validate_session_sync(session_token: str, audience: Optional[str] = None):
    try:
        return descope_client.validate_session(session_token=session_token, audience=audience)
    except Exception as e:
        raise RuntimeError(f"Session validation failed: {e}")

async def exchange_for_delegated_token(subject_token: str, audience: str, scopes: List[str], expires_in: int = 300) -> dict:
    if not DESCOPE_TOKEN_EXCHANGE_URL:
        raise RuntimeError("DESCOPE_TOKEN_EXCHANGE_URL not configured")
    payload = {
        "subject_token": subject_token,
        "requested_audience": audience,
        "requested_scope": " ".join(scopes),
        "expires_in": expires_in
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(DESCOPE_TOKEN_EXCHANGE_URL, json=payload, auth=(CONCIERGE_CLIENT_ID, CONCIERGE_CLIENT_SECRET))
        r.raise_for_status()
        return r.json()
