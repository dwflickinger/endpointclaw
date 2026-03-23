from fastapi import APIRouter
from api.services import supabase_client as db
router = APIRouter()

@router.get("/screenshot/{eid}")
async def get_pol(eid: str): return {"policy": await db.rpc("get_effective_screenshot_policy",{"p_endpoint_id":eid})}

@router.put("/screenshot/{eid}")
async def set_pol(eid: str, policy: dict):
    await db.update("endpoints", f"id=eq.{eid}", {"screenshot_policy_override": policy})
    await db.insert("endpoint_commands",{"endpoint_id":eid,"command_type":"config_update","payload":{"screenshot":policy},"priority":2,"issued_by":"dashboard"})
    await db.insert("audit_log",{"actor":"dashboard","action":"screenshot_policy_update","target_type":"endpoint","target_id":eid,"new_value":policy})
    return {"updated": True}

@router.put("/screenshot/company/{cid}")
async def set_company_pol(cid: str, policy: dict):
    await db.update("company_configs", f"id=eq.{cid}", {"screenshot_policy": policy})
    eps = await db.query("endpoints", f"company_id=eq.{cid}&status=eq.active&select=id")
    for ep in eps: await db.insert("endpoint_commands",{"endpoint_id":ep["id"],"command_type":"config_update","payload":{"screenshot":policy},"priority":2,"issued_by":"dashboard"})
    return {"updated":True,"endpoints_notified":len(eps)}

@router.get("/monitoring/{eid}")
async def get_mon(eid: str): return {"config": await db.rpc("get_effective_config",{"p_endpoint_id":eid})}
