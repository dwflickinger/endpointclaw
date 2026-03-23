from fastapi import APIRouter, Query
from api.services import supabase_client as db
router = APIRouter()

@router.get("/browse/{eid}")
async def browse(eid: str, date: str = Query(None), application: str = Query(None), limit: int = 50, offset: int = 0):
    p = f"endpoint_id=eq.{eid}&order=captured_at.desc&limit={limit}&offset={offset}"
    if date: p += f"&captured_at=gte.{date}T00:00:00&captured_at=lt.{date}T23:59:59"
    if application: p += f"&active_application=ilike.*{application}*"
    shots = await db.query("endpoint_screenshots", p)
    for s in shots:
        if s.get("storage_path"):
            try: s["image_url"] = await db.get_storage_url("endpointclaw-screenshots", s["storage_path"])
            except: s["image_url"] = None
    return {"screenshots": shots}

@router.post("/capture-now/{eid}")
async def capture_now(eid: str):
    r = await db.insert("endpoint_commands",{"endpoint_id":eid,"command_type":"screenshot_now","payload":{},"priority":1,"issued_by":"dashboard"})
    return {"command_id": r[0]["id"] if r else None}

@router.get("/latest/{eid}")
async def latest(eid: str):
    s = await db.query("endpoint_screenshots", f"endpoint_id=eq.{eid}&order=captured_at.desc&limit=1")
    if s and s[0].get("storage_path"): s[0]["image_url"] = await db.get_storage_url("endpointclaw-screenshots", s[0]["storage_path"])
    return s[0] if s else {}
