# concierge/descope_client.py
import os
from typing import Any, Dict, Optional
from dotenv import load_dotenv
# Prefer the SDK import. The SDK exposes DescopeClient and AccessKeyLoginOptions.
from descope import DescopeClient
load_dotenv()

# AccessKeyLoginOptions location may vary across small SDK versions.
# Try import from top-level then fallback to models.
try:
    from descope import AccessKeyLoginOptions  # preferred if present
except Exception:
    try:
        from descope.models import AccessKeyLoginOptions
    except Exception:
        AccessKeyLoginOptions = None  # we'll raise if missing when needed

# Environment
PROJECT_ID = os.getenv("DESCOPE_PROJECT_ID")
DESCOPE_BASE_URI = os.getenv("DESCOPE_BASE_URI", "https://api.descope.com")
# Access Key (not management key) — must be created in Descope UI > Access Keys
ACCESS_KEY = os.getenv("NEW_DESCOPE_KEY")

if not PROJECT_ID:
    raise RuntimeError("DESCOPE_PROJECT_ID environment variable is required for concierge service")

# Initialize Descope SDK client
# The SDK accepts 'project_id' and optionally a base_url param in newer versions.
_client: Optional[DescopeClient] = None
try:
    _client = DescopeClient(project_id=PROJECT_ID)
except Exception as e:
    # If SDK initialization fails (rare), we still let the module import fail loudly where used.
    raise

def get_client() -> DescopeClient:
    if _client is None:
        raise RuntimeError("Descope client not initialized")
    return _client

def validate_session_sync(session_token: str) -> Dict[str, Any]:
    """
    Validate session token using Descope SDK.
    Returns session claims dict on success; raises exception on failure.
    """
    client = get_client()
    # SDK method per docs:
    return client.validate_session(session_token)

def exchange_access_key(access_key: str, audience: str, scopes: list[str], ttl_seconds: int = 300) -> Dict[str, Any]:
    """
    Build AccessKeyLoginOptions with required custom_claims and call SDK exchange_access_key.
    Returns the SDK response (dict) containing the delegated jwt.
    """
    client = get_client()
    if AccessKeyLoginOptions is None:
        raise RuntimeError("AccessKeyLoginOptions not available in descope SDK version")

    # Build custom claims as recommended in earlier examples:
    custom_claims = {}
    if audience:
        custom_claims["aud"] = audience
    if scopes:
        # use "scope" or "scp" depending on your resource validation logic
        custom_claims["scope"] = " ".join(scopes)
    # Optionally include sub so the delegated token is tied to a user (some flows prefer it)
    # Note: The SDK/userflow may set sub internally; we avoid overriding unless needed.

    login_opts = AccessKeyLoginOptions(custom_claims=custom_claims)

    # SDK signature: exchange_access_key(access_key=..., login_options=...)
    resp = client.exchange_access_key(access_key=access_key, login_options=login_opts)

    # resp typically includes a jwt field (or sessionJwt) depending on SDK
    return resp
