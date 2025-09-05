# concierge/main.py
import os
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import jwt as pyjwt
from descope import DescopeClient

load_dotenv()

app = FastAPI(title="veritas-concierge")

# Init Descope client
DESCOPE_PROJECT_ID = os.getenv("DESCOPE_PROJECT_ID")
DESCOPE_CLIENT_ID = os.getenv("DESCOPE_CONCIERGE_CLIENT_ID")
DESCOPE_CLIENT_SECRET = os.getenv("DESCOPE_CONCIERGE_CLIENT_SECRET")

if not DESCOPE_PROJECT_ID or not DESCOPE_CLIENT_ID or not DESCOPE_CLIENT_SECRET:
    raise RuntimeError("Missing Descope config (check env vars).")

client = DescopeClient(project_id=DESCOPE_PROJECT_ID)

AUD_MAP = {
    "scout": os.getenv("AUD_SCOUT"),
    "analyst": os.getenv("AUD_ANALYST"),
    "dispatcher": os.getenv("AUD_DISPATCHER"),
}


class DelegateRequest(BaseModel):
    target: str
    scopes: list[str] = []
    expires_in: int = 300


@app.post("/delegate")
async def delegate(req: DelegateRequest, authorization: str | None = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")

    session_token = authorization.split(" ", 1)[1].strip()

    # Validate session token
    try:
        client.validate_session(session_token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Session invalid: {e}")

    audience = AUD_MAP.get(req.target)
    if not audience:
        raise HTTPException(status_code=400, detail="invalid target")

    # Exchange for delegated token
    try:
        token_resp = client.mgmt.jwt().exchange_token(
            login_token=session_token,
            target_audience=audience,
            scopes=req.scopes,
            expiration=req.expires_in,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delegation failed: {e}")

    # optional: decode token for audit convenience
    token = token_resp.get("sessionJwt") or token_resp.get("access_token")
    if token:
        try:
            claims = pyjwt.decode(token, options={"verify_signature": False})
            print(
                "Delegation audit:",
                {
                    "user": claims.get("sub"),
                    "aud": claims.get("aud"),
                    "scope": claims.get("scope"),
                    "jti": claims.get("jti"),
                },
            )
        except Exception:
            pass

    return token_resp


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
