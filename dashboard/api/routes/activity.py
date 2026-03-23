from fastapi import APIRouter, Query
from api.services import supabase_client as db
router = APIRouter()

@router.get("/summary/{eid}")
async def summary(eid: str, date: str = Query(None)):
    df = f"&created_at=gte.{date}T00:00:00&created_at=lt.{date}T23:59:59" if date else ""
    events = await db.query("endpoint_activity", f"endpoint_id=eq.{eid}&event_type=eq.app_focus{df}&order=created_at.desc&limit=5000")
    at = {}
    for e in events: a=e.get("application","?"); d=e.get("duration_ms",0) or 0; at[a]=at.get(a,0)+d
    t = sum(at.values())
    return {"total_active_hours":round(t/3600000,2),"event_count":len(events),
        "by_application":[{"application":a,"duration_minutes":round(d/60000,1),"percent":round(d/t*100,1) if t else 0} for a,d in sorted(at.items(),key=lambda x:x[1],reverse=True)]}

@router.get("/team-summary")
async def team_summary(company_id: str = "corvex", date: str = None):
    eps = await db.query("endpoints", f"company_id=eq.{company_id}&status=eq.active")
    out = []
    for ep in eps:
        evts = await db.query("endpoint_activity", f"endpoint_id=eq.{ep['id']}&event_type=eq.app_focus&limit=2000"+(f"&created_at=gte.{date}T00:00:00" if date else ""))
        t = sum(e.get("duration_ms",0) or 0 for e in evts)
        out.append({"user_name":ep["user_name"],"total_active_hours":round(t/3600000,2)})
    return {"team": out}
