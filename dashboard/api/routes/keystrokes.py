from fastapi import APIRouter, Query
from api.services import supabase_client as db
router = APIRouter()

@router.get("/browse/{eid}")
async def browse(eid: str, date: str = Query(None), application: str = Query(None), limit: int = 100, offset: int = 0):
    p = f"endpoint_id=eq.{eid}&order=chunk_start.desc&limit={limit}&offset={offset}"
    if date: p += f"&chunk_start=gte.{date}T00:00:00&chunk_start=lt.{date}T23:59:59"
    if application: p += f"&application=ilike.*{application}*"
    return {"keystrokes": await db.query("endpoint_keystrokes", p)}

@router.get("/search/{eid}")
async def search(eid: str, q: str = Query(...), limit: int = 50):
    return {"results": await db.query("endpoint_keystrokes", f"endpoint_id=eq.{eid}&keystrokes=ilike.*{q}*&order=chunk_start.desc&limit={limit}"), "query": q}
