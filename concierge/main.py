# concierge/main.py
import os
import logging
import traceback
from typing import List, Optional
import uvicorn

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from .descope_client import validate_session_sync, exchange_access_key_for_audience

load_dotenv()
# Descope SDK imports
from descope import DescopeClient
# AccessKeyLoginOptions path may differ across SDK versions
try:
    from descope import AccessKeyLoginOptions
except Exception:
    try:
        from descope.models import AccessKeyLoginOptions
    except Exception:
        AccessKeyLoginOptions = None

logger = logging.getLogger("concierge")
logging.basicConfig(level=logging.INFO)

PROJECT_ID = os.getenv("DESCOPE_PROJECT_ID")
ACCESS_KEY = os.getenv("NEW_DESCOPE_KEY") 
FRONTEND_ORIGINS = os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(",")
# Friendly name -> audience mapping (set env vars AUD_SCOUT etc to inbound app audience IDs)
AUD_MAP = {
    "scout": os.getenv("AUD_SCOUT"),
    "analyst": os.getenv("AUD_ANALYST"),
    "dispatcher": os.getenv("AUD_DISPATCHER"),
    "moderator": os.getenv("AUD_MODERATOR"),
}

if not PROJECT_ID:
    raise RuntimeError("DESCOPE_PROJECT_ID is required in environment")
# Initialize Descope SDK client
try:
    descope_client = DescopeClient(project_id=PROJECT_ID, jwt_validation_leeway=60)
except Exception as e:
    logger.exception("Failed to initialize DescopeClient: %s", e)
    raise
app = FastAPI(title="veritas-concierge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DelegateRequest(BaseModel):
    target: str
    scopes: Optional[List[str]] = []
    expires_in: Optional[int] = 300
    subscription_id: Optional[int] = None  # added to store token for this subscription

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/delegate")
async def delegate(req: Request, body: DelegateRequest):
    """
    Validate the frontend session token and exchange the server-side Access Key for
    a delegated token targeted at the requested audience.

    Body: { "target": "<aud alias or full audience string>", "scopes": [...], "expires_in": 300 }
    Header: Authorization: Bearer <session_token>
    """
    # Extract session token
    auth_header = req.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    session_token = auth_header.split(" ", 1)[1].strip()
    if not session_token:
        raise HTTPException(status_code=401, detail="Empty session token")

    # Validate session via SDK
    try:
        session_claims = descope_client.validate_session(session_token)
    except Exception as e:
        logger.exception("Session validation failed")
        raise HTTPException(status_code=401, detail=f"Invalid session token: {e}")

    logger.info("Validated session for sub=%s", session_claims.get("sub"))

    # Resolve audience: allow passing friendly alias (e.g., "scout") or direct audience string
    requested_target = body.target
    if isinstance(requested_target, str) and requested_target in AUD_MAP and AUD_MAP[requested_target]:
        audience = AUD_MAP[requested_target]
    else:
        # treat as literal audience string
        audience = requested_target

    logger.info("Requested audience resolved to: %s", audience)

    if not audience or not isinstance(audience, str) or audience.strip() == "":
        raise HTTPException(status_code=400, detail="Invalid audience (empty). Configure AUD_* env vars or pass full audience string.")

    # Make sure Access Key is configured
    if not ACCESS_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: DESCOPE_ACCESS_KEY missing")

    # Build AccessKeyLoginOptions (do NOT set 'aud' inside custom_claims; pass audience param explicitly)
    if AccessKeyLoginOptions is None:
        # SDK version problem: user must install a compatible SDK
        logger.error("AccessKeyLoginOptions not available from installed descope SDK")
        raise HTTPException(status_code=500, detail="Server error: Descope SDK incompatible (missing AccessKeyLoginOptions)")

    custom_claims = {}
    if body.scopes:
        # Put scopes in custom claim (server will instruct SDK to include under nsec scope)
        custom_claims["scope"] = " ".join(body.scopes)
    custom_claims["aud"]=audience

    login_opts = AccessKeyLoginOptions(custom_claims=custom_claims)

    # Call the SDK exchange_access_key with explicit audience argument.
    try:
        logger.info("Calling exchange_access_key with audience=%s scopes=%s", audience, custom_claims.get("scope"))
        sdk_resp = descope_client.exchange_access_key(access_key=ACCESS_KEY, audience=audience, login_options=login_opts)
        # sdk_resp typically contains sessionToken/jwt and other fields; return to caller
        logger.info("Descope exchange_access_key succeeded")
        # --------------------------
        # Store moderator token in Redis for subscription
        # --------------------------
        if body.subscription_id and requested_target == "moderator":
            token_to_store = sdk_resp.get("sessionToken") or sdk_resp.get("jwt")
            if token_to_store:
                r = await get_redis()
                key = f"subscription:{body.subscription_id}:moderator_token"
                await r.set(key, token_to_store, ex=body.expires_in or 300)
                logger.info(f"Stored moderator token in Redis for subscription {body.subscription_id}")

        return sdk_resp
    except Exception as e:
        # Provide helpful error when audience is invalid
        tb = traceback.format_exc()
        logger.error("exchange_access_key exception: %s\n%s", e, tb)
        msg = str(e)
        if "audience" in msg.lower() or "invalid audience" in msg.lower():
            raise HTTPException(
                status_code=400,
                detail=f"Delegation failed: Invalid audience '{audience}'. Ensure AUD_* env var matches Descope Inbound App audience and Access Key permissions.",
            )
        # else generic 500
        raise HTTPException(status_code=500, detail=f"Delegation failed: {msg}")


@app.get("/_debug/env")
async def debug_env():
    masked_key = (ACCESS_KEY[:6] + "...") if ACCESS_KEY else "<missing>"
    return {
        "project_id": PROJECT_ID,
        "access_key": masked_key,
        "aud_map": {k: (v if v else "<not set>") for k, v in AUD_MAP.items()},
        "frontend_origins": FRONTEND_ORIGINS,
    }

if __name__ == "__main__":
    uvicorn.run("concierge.main:app", host="0.0.0.0", port=int(os.getenv("CONCIERGE_PORT", "8080")), reload=True)

