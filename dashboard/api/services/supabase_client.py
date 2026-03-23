import os, httpx
from typing import Optional
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://twgdhuimqspfoimfmyxz.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
_client: Optional[httpx.AsyncClient] = None

async def init_supabase():
    global _client; _client = httpx.AsyncClient(timeout=30.0)

def _h(): return {"apikey":SUPABASE_KEY,"Authorization":f"Bearer {SUPABASE_KEY}","Content-Type":"application/json","Prefer":"return=representation"}

async def query(table, params=""):
    r = await _client.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=_h()); r.raise_for_status(); return r.json()

async def insert(table, data):
    r = await _client.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=_h(), json=data); r.raise_for_status(); return r.json()

async def update(table, match, data):
    r = await _client.patch(f"{SUPABASE_URL}/rest/v1/{table}?{match}", headers=_h(), json=data); r.raise_for_status(); return r.json()

async def rpc(fn, params):
    r = await _client.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=_h(), json=params); r.raise_for_status(); return r.json()

async def get_storage_url(bucket, path):
    r = await _client.post(f"{SUPABASE_URL}/storage/v1/object/sign/{bucket}/{path}", headers=_h(), json={"expiresIn":3600}); r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1{r.json().get('signedURL','')}"
