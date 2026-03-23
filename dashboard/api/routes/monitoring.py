from fastapi import APIRouter, Query
from api.services import supabase_client as db
router = APIRouter()

@router.get("/live")
async def live(company_id: str = "corvex"):
    endpoints = await db.query("endpoints", f"company_id=eq.{company_id}&status=eq.active&select=id,device_name,user_name,user_email,last_heartbeat")
    result = []
    for ep in endpoints:
        hb = await db.query("endpoint_heartbeats", f"endpoint_id=eq.{ep['id']}&order=created_at.desc&limit=1")
        act = await db.query("endpoint_activity", f"endpoint_id=eq.{ep['id']}&order=created_at.desc&limit=1")
        result.append({**ep, "heartbeat": hb[0] if hb else None, "last_activity": act[0] if act else None})
    return {"endpoints": result}

@router.get("/timeline/{eid}")
async def timeline(eid: str, date: str = Query(None), limit: int = 200):
    p = f"endpoint_id=eq.{eid}&order=created_at.desc&limit={limit}"
    if date: p += f"&created_at=gte.{date}T00:00:00&created_at=lt.{date}T23:59:59"
    return {"events": await db.query("endpoint_activity", p)}
