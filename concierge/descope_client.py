# concierge/descope_client.py
import os
from typing import Any, Dict, Optional, List
from dotenv import load_dotenv

# Official Descope SDK imports
from descope import DescopeClient

load_dotenv()

# AccessKeyLoginOptions location differs across SDK versions; try the common locations
try:
    from descope import AccessKeyLoginOptions  # newer packaging sometimes exposes this
except Exception:
    try:
        from descope.models import AccessKeyLoginOptions  # fallback
    except Exception:
        AccessKeyLoginOptions = None

PROJECT_ID = os.getenv("DESCOPE_PROJECT_ID")
ACCESS_KEY = os.getenv("NEW_DESCOPE_KEY") 
DESCOPE_BASE_URI = os.getenv("DESCOPE_BASE_URI")  # optional

if not PROJECT_ID:
    raise RuntimeError("DESCOPE_PROJECT_ID environment variable is required for concierge service")

# Initialize Descope SDK client
_client: Optional[DescopeClient] = None
try:
    if DESCOPE_BASE_URI:
        _client = DescopeClient(project_id=PROJECT_ID, base_url=DESCOPE_BASE_URI, jwt_validation_leeway=30)
    else:
        _client = DescopeClient(project_id=PROJECT_ID, jwt_validation_leeway=30)
except Exception as e:
    # raise so problems surface early
    raise

def get_client() -> DescopeClient:
    if _client is None:
        raise RuntimeError("Descope client not initialized")
    return _client

def validate_session_sync(session_token: str) -> Dict[str, Any]:
    """
    Validate session token using Descope SDK. Returns session info dict on success.
    """
    client = get_client()
    # SDK call: validate_session(session_token)
    return client.validate_session(session_token)

def exchange_access_key_for_audience(audience: str, scopes: Optional[List[str]] = None, ttl_seconds: int = 300) -> Dict[str, Any]:
    """
    Exchange the server-side Access Key for a delegated token targeted at `audience`
    with requested scopes. Returns the raw SDK response.
    Implementation note:
      - Some SDK versions expect the 'aud' and scopes to be placed in AccessKeyLoginOptions.custom_claims.
      - We avoid passing a separate 'audience' keyword to the SDK call (some versions don't accept it).
    """
    client = get_client()
    if AccessKeyLoginOptions is None:
        raise RuntimeError("AccessKeyLoginOptions not available in installed descope SDK. Please upgrade/downgrade SDK or adjust import.")

    custom_claims: Dict[str, Any] = {}
    if scopes:
        # Put requested scopes under 'nsec.scope' so resource validators that look there (like your descope_auth) see them.
        custom_claims["nsec"] = {"scope": " ".join(scopes)}
    if audience:
        # include audience claim so the generated token includes the correct aud
        custom_claims["aud"] = audience

    login_opts = AccessKeyLoginOptions(custom_claims=custom_claims)

    # SDK signature: exchange_access_key(access_key=..., login_options=...)
    resp = client.exchange_access_key(access_key=ACCESS_KEY, login_options=login_opts)

    # resp typically includes a jwt field (or sessionJwt) depending on SDK
    return resp
