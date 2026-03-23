from fastapi import APIRouter, Query
from api.services import supabase_client as db
from datetime import datetime, timezone
router = APIRouter()

@router.get("/endpoints")
async def list_endpoints(company_id: str = Query(None), status: str = Query(None)):
    params = "select=*&order=last_heartbeat.desc"
    if company_id: params += f"&company_id=eq.{company_id}"
    if status: params += f"&status=eq.{status}"
    endpoints = await db.query("endpoints", params); now = datetime.now(timezone.utc)
    for ep in endpoints:
        hb = ep.get("last_heartbeat")
        if hb:
            d = (now - datetime.fromisoformat(hb.replace("Z","+00:00"))).total_seconds()
            ep["health"] = "healthy" if d<120 else "warning" if d<600 else "critical"
            ep["seconds_since_heartbeat"] = int(d)
        else: ep["health"]="unknown"; ep["seconds_since_heartbeat"]=None
    return {"endpoints": endpoints, "total": len(endpoints)}

@router.get("/endpoints/{eid}")
async def get_endpoint(eid: str):
    eps = await db.query("endpoints", f"id=eq.{eid}")
    if not eps: return {"error":"Not found"}
    ep = eps[0]
    ep["recent_heartbeats"] = await db.query("endpoint_heartbeats", f"endpoint_id=eq.{eid}&order=created_at.desc&limit=60")
    ep["pending_commands"] = await db.query("endpoint_commands", f"endpoint_id=eq.{eid}&status=in.(pending,in_progress)")
    return ep

@router.get("/endpoints/{eid}/effective-config")
async def effective_config(eid: str):
    return {"config": await db.rpc("get_effective_config",{"p_endpoint_id":eid}), "screenshot_policy": await db.rpc("get_effective_screenshot_policy",{"p_endpoint_id":eid})}

@router.patch("/endpoints/{eid}")
async def update_endpoint(eid: str, data: dict):
    allowed = {"config_overrides","screenshot_policy_override","monitored_paths","status"}
    f = {k:v for k,v in data.items() if k in allowed}
    r = await db.update("endpoints", f"id=eq.{eid}", f)
    await db.insert("audit_log",{"actor":"dashboard","action":"endpoint_update","target_type":"endpoint","target_id":eid,"new_value":f})
    return r[0] if r else {"error":"Failed"}

@router.get("/companies")
async def list_companies(): return await db.query("company_configs","select=id,company_name,branding&order=company_name")
