import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
bearer = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-OpenClaw-Key", auto_error=False)
DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET", "change-me")
OPENCLAW_KEY = os.environ.get("OPENCLAW_API_KEY", "change-me-openclaw")

async def require_dashboard_auth(creds: HTTPAuthorizationCredentials = Security(bearer)):
    if not creds or creds.credentials != DASHBOARD_SECRET: raise HTTPException(401, "Invalid")
    return creds.credentials

async def require_machine_auth(key: str = Security(api_key_header)):
    if not key or key != OPENCLAW_KEY: raise HTTPException(401, "Invalid API key")
    return key
