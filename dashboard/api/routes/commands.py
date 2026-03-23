from fastapi import APIRouter
from pydantic import BaseModel
from api.services import supabase_client as db
router = APIRouter()

class CmdReq(BaseModel):
    endpoint_id: str; command_type: str; payload: dict; priority: int = 5; timeout_seconds: int = 300

@router.post("/send")
async def send(cmd: CmdReq):
    d = {"endpoint_id":cmd.endpoint_id,"command_type":cmd.command_type,"payload":cmd.payload,"priority":cmd.priority,"timeout_seconds":cmd.timeout_seconds,"issued_by":"dashboard"}
    r = await db.insert("endpoint_commands", d)
    await db.insert("audit_log",{"actor":"dashboard","action":"command_sent","target_type":"endpoint","target_id":cmd.endpoint_id,"new_value":d})
    return {"command": r[0] if r else None}

@router.post("/broadcast")
async def broadcast(company_id: str, command_type: str, payload: dict):
    eps = await db.query("endpoints", f"company_id=eq.{company_id}&status=eq.active&select=id")
    cmds = []
    for ep in eps:
        r = await db.insert("endpoint_commands",{"endpoint_id":ep["id"],"command_type":command_type,"payload":payload,"issued_by":"broadcast"})
        cmds.append(r[0] if r else None)
    return {"sent_to": len(cmds)}

@router.get("/history/{eid}")
async def history(eid: str, limit: int = 50):
    return {"commands": await db.query("endpoint_commands", f"endpoint_id=eq.{eid}&order=created_at.desc&limit={limit}")}

@router.get("/status/{cid}")
async def status(cid: str):
    c = await db.query("endpoint_commands", f"id=eq.{cid}"); return c[0] if c else {"error":"Not found"}
