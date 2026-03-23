from fastapi import APIRouter, Query
from api.services import supabase_client as db
from datetime import datetime, timezone
router = APIRouter()

@router.get("/fleet-health")
async def fleet_health(company_id: str = "corvex"):
    eps = await db.query("endpoints", f"company_id=eq.{company_id}&select=id,status,last_heartbeat"); now = datetime.now(timezone.utc)
    h=w=c=o=0
    for ep in eps:
        if ep["status"]!="active": o+=1; continue
        hb=ep.get("last_heartbeat")
        if not hb: c+=1
        else:
            d=(now-datetime.fromisoformat(hb.replace("Z","+00:00"))).total_seconds()
            if d<120: h+=1
            elif d<600: w+=1
            else: c+=1
    return {"total":len(eps),"healthy":h,"warning":w,"critical":c,"offline":o}

@router.get("/productivity")
async def productivity(company_id: str = "corvex"):
    eps = await db.query("endpoints", f"company_id=eq.{company_id}&status=eq.active")
    prod_apps = {"EXCEL.EXE","chrome.exe","msedge.exe","OUTLOOK.EXE","WINWORD.EXE","ACROBAT.EXE"}
    rpt = []
    for ep in eps:
        evts = await db.query("endpoint_activity", f"endpoint_id=eq.{ep['id']}&event_type=eq.app_focus&limit=10000")
        t = sum(e.get("duration_ms",0) or 0 for e in evts)
        p = sum(e.get("duration_ms",0) or 0 for e in evts if e.get("application") in prod_apps)
        rpt.append({"user_name":ep["user_name"],"user_email":ep["user_email"],"total_hours":round(t/3600000,2),"productive_hours":round(p/3600000,2),"productivity_pct":round(p/t*100,1) if t else 0})
    return {"team": rpt}

@router.get("/audit-log")
async def audit_log(limit: int = 100, actor: str = Query(None)):
    p = f"order=created_at.desc&limit={limit}"
    if actor: p += f"&actor=eq.{actor}"
    return await db.query("audit_log", p)
