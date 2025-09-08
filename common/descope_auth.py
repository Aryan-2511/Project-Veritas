# common/descope_auth.py
import os
import time
import asyncio
from typing import List, Optional, Dict, Any

import httpx
from jose import jwt, JWTError
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None

# Config
DESCOPE_JWKS_URL = os.environ.get("DESCOPE_JWKS_URL", "https://api.descope.com/v1/keys")
SERVICE_AUD = os.environ.get("DESCOPE_AUD")  # optional
REDIS_URL = os.environ.get("REDIS_URL")
JWKS_CACHE_TTL = int(os.environ.get("JWKS_CACHE_TTL", "300"))
JTI_REPLAY_TTL = int(os.environ.get("JTI_REPLAY_TTL", "300"))

bearer = HTTPBearer(auto_error=False)

class JWKSFetcher:
    def __init__(self, jwks_url: str):
        self.jwks_url = jwks_url
        self._jwks: Optional[Dict[str, Any]] = None
        self._last_fetch = 0
        self._lock = asyncio.Lock()

    async def get_jwks(self) -> Dict[str, Any]:
        now = int(time.time())
        if self._jwks and (now - self._last_fetch) < JWKS_CACHE_TTL:
            return self._jwks
        async with self._lock:
            now = int(time.time())
            if self._jwks and (now - self._last_fetch) < JWKS_CACHE_TTL:
                return self._jwks
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(self.jwks_url)
                r.raise_for_status()
                self._jwks = r.json()
                self._last_fetch = now
                return self._jwks

_jwks_fetcher: Optional[JWKSFetcher] = None
_redis_client = None

def init_jwks():
    global _jwks_fetcher
    if _jwks_fetcher is None:
        _jwks_fetcher = JWKSFetcher(DESCOPE_JWKS_URL)

async def get_redis():
    global _redis_client
    if _redis_client is None and aioredis and REDIS_URL:
        _redis_client = aioredis.from_url(REDIS_URL)
    return _redis_client

async def _record_and_check_jti(jti: Optional[str]):
    if not jti:
        return
    redis_client = await get_redis()
    if redis_client:
        key = f"veritas:jti:{jti}"
        was_set = await redis_client.set(key, "1", ex=JTI_REPLAY_TTL, nx=True)
        if not was_set:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="jti replay detected")
        return
    # in-memory fallback
    if not hasattr(_record_and_check_jti, "_store"):
        _record_and_check_jti._store = {}
    now = int(time.time())
    # cleanup
    for k, v in list(_record_and_check_jti._store.items()):
        if v < now:
            del _record_and_check_jti._store[k]
    if jti in _record_and_check_jti._store:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="jti replay detected")
    _record_and_check_jti._store[jti] = now + JTI_REPLAY_TTL

def _find_jwk_for_kid(jwks: Dict[str, Any], kid: str) -> Optional[Dict[str, Any]]:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None

async def validate_delegated_jwt(
    token: str,
    expected_aud: Optional[str] = None,
    required_scopes: Optional[List[str]] = None,
    expected_azp: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate delegated JWT offline (resource servers use this).
    """
    init_jwks()
    jwks = await _jwks_fetcher.get_jwks()

    # Extract kid from token header
    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token header: {e}")

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token missing kid")

    jwk = _find_jwk_for_kid(jwks, kid)
    if not jwk:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no matching jwk found")

    # python-jose supports passing a JWK dict directly for verification
    try:
        claims = jwt.decode(
            token,
            jwk,
            algorithms=["RS256", "RS384", "RS512"],
            options={"verify_aud": False},
        )
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {e}")

    now = int(time.time())
    if claims.get("exp") is None or claims["exp"] < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired")
    if claims.get("iat") and claims["iat"] > now + 60:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token not yet valid (iat)")

    aud = claims.get("aud")
    if expected_aud:
        auds = aud if isinstance(aud, list) else [aud] if aud else []
        if expected_aud not in auds:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid audience")

    azp = claims.get("azp")
    if expected_azp and azp != expected_azp:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="azp mismatch")

    claim_scope = claims.get("scope") or claims.get("scp") or claims.get("scopes") or ""
    if required_scopes:
        claim_scopes = set(str(claim_scope).split())
        if not set(required_scopes).issubset(claim_scopes):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")

    jti = claims.get("jti")
    await _record_and_check_jti(jti)
    return claims

def _bearer_from_auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return credentials.credentials

def require_delegated_token(required_scopes: Optional[List[str]] = None,
                            expected_aud: Optional[str] = None,
                            expected_azp: Optional[str] = None):
    async def _dep(token: str = Depends(_bearer_from_auth)):
        claims = await validate_delegated_jwt(token, expected_aud=expected_aud or SERVICE_AUD, required_scopes=required_scopes, expected_azp=expected_azp)
        return claims
    return _dep
