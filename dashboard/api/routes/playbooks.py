from fastapi import APIRouter
from api.services import supabase_client as db
router = APIRouter()

@router.get("/")
async def list_pb(company_id: str = "corvex"): return await db.query("playbooks", f"company_id=eq.{company_id}&order=name")

@router.get("/{pid}")
async def get_pb(pid: str):
    p = await db.query("playbooks", f"id=eq.{pid}"); return p[0] if p else {"error":"Not found"}

@router.post("/{pid}/trigger")
async def trigger(pid: str, endpoint_id: str, input_data: dict = None):
    r = await db.insert("endpoint_commands",{"endpoint_id":endpoint_id,"command_type":"playbook_trigger","payload":{"playbook_id":pid,"input_data":input_data or {}},"priority":3,"issued_by":"dashboard"})
    return {"command": r[0] if r else None}
