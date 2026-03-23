"""Machine API — OpenClaw / AI agent interface. Prefix: /m/"""
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from api.middleware.auth import require_machine_auth
from api.services import supabase_client as db
import asyncio
router = APIRouter()

class NLQuery(BaseModel):
    question: str; company_id: str = "corvex"
class CmdReq(BaseModel):
    endpoint_id: str; command_type: str; payload: dict = {}; priority: int = 5
class PolicyUpd(BaseModel):
    target_type: str; target_id: str; screenshot_policy: Optional[dict] = None; monitoring_config: Optional[dict] = None
class BulkQ(BaseModel):
    company_id: str = "corvex"; endpoint_ids: Optional[list[str]] = None; query_type: str; params: dict = {}
class PBTrigger(BaseModel):
    playbook_id: str; endpoint_id: str; input_data: dict = {}

@router.get("/fleet")
async def fleet_overview(company_id: str = "corvex", auth=Depends(require_machine_auth)):
    eps = await db.query("endpoints", f"company_id=eq.{company_id}&status=eq.active&order=user_name"); now = datetime.now(timezone.utc); out = []
    for ep in eps:
        eid=ep["id"]; hb=ep.get("last_heartbeat"); delta=None; health="unknown"
        if hb: delta=(now-datetime.fromisoformat(hb.replace("Z","+00:00"))).total_seconds(); health="healthy" if delta<120 else "warning" if delta<600 else "critical"
        act = await db.query("endpoint_activity", f"endpoint_id=eq.{eid}&event_type=eq.app_focus&order=created_at.desc&limit=1")
        met = await db.query("endpoint_heartbeats", f"endpoint_id=eq.{eid}&order=created_at.desc&limit=1")
        pend = await db.query("endpoint_commands", f"endpoint_id=eq.{eid}&status=in.(pending,in_progress)&select=id")
        out.append({"endpoint_id":eid,"user_name":ep["user_name"],"user_email":ep["user_email"],"device_name":ep["device_name"],"health":health,"seconds_since_heartbeat":int(delta) if delta else None,
            "current_activity":{"application":act[0]["application"],"window_title":act[0]["window_title"]} if act else None,
            "system_metrics":{"cpu_percent":met[0]["cpu_percent"],"ram_used_mb":met[0]["ram_used_mb"]} if met else None,"pending_commands":len(pend)})
    return {"company_id":company_id,"timestamp":now.isoformat(),"endpoint_count":len(out),"endpoints":out}

@router.post("/ask")
async def ask(q: NLQuery, auth=Depends(require_machine_auth)):
    ql = q.question.lower(); eps = await db.query("endpoints", f"company_id=eq.{q.company_id}&status=eq.active")
    nm = {ep["user_name"].lower():ep for ep in eps if ep.get("user_name")}
    tgt = None
    for n, ep in nm.items():
        if n in ql or n.split()[0].lower() in ql: tgt=ep; break

    if any(w in ql for w in ["working on","doing","current","right now","active"]):
        targets = [tgt] if tgt else eps; results = []
        for ep in targets:
            act = await db.query("endpoint_activity", f"endpoint_id=eq.{ep['id']}&event_type=eq.app_focus&order=created_at.desc&limit=5")
            results.append({"user_name":ep["user_name"],"endpoint_id":ep["id"],"recent":[{"app":a["application"],"title":a["window_title"],"minutes":round((a.get("duration_ms",0) or 0)/60000,1),"at":a["created_at"]} for a in act]})
        return {"query_type":"current_activity","results":results}

    elif any(w in ql for w in ["file","find","search","takeoff","estimate","proposal","document"]):
        return {"query_type":"file_search","results":await db.rpc("search_endpoint_files",{"p_company_id":q.company_id,"p_query":q.question,"p_endpoint_id":tgt["id"] if tgt else None,"p_limit":20})}

    elif any(w in ql for w in ["idle","inactive","away"]):
        results = []
        for ep in eps:
            last = await db.query("endpoint_activity", f"endpoint_id=eq.{ep['id']}&order=created_at.desc&limit=1")
            results.append({"user_name":ep["user_name"],"last_activity":last[0]["created_at"] if last else None})
        results.sort(key=lambda x: x.get("last_activity") or "")
        return {"query_type":"idle_check","results":results}

    elif any(w in ql for w in ["screenshot","screen","see","look"]):
        targets = [tgt] if tgt else eps[:3]; results = []
        for ep in targets:
            shots = await db.query("endpoint_screenshots", f"endpoint_id=eq.{ep['id']}&order=captured_at.desc&limit=3")
            for s in shots:
                if s.get("storage_path"):
                    try: s["image_url"] = await db.get_storage_url("endpointclaw-screenshots",s["storage_path"])
                    except: pass
            results.append({"user_name":ep["user_name"],"screenshots":shots})
        return {"query_type":"screenshots","results":results}

    elif any(w in ql for w in ["keystroke","typed","typing"]):
        if not tgt: return {"error":"Specify a user"}
        return {"query_type":"keystrokes","user_name":tgt["user_name"],"chunks":await db.query("endpoint_keystrokes",f"endpoint_id=eq.{tgt['id']}&order=chunk_start.desc&limit=20")}

    else:
        fleet = await fleet_overview(q.company_id); files = await db.rpc("search_endpoint_files",{"p_company_id":q.company_id,"p_query":q.question,"p_limit":10})
        return {"interpretation":"fleet+file_search","fleet":fleet,"files":files}

@router.post("/command")
async def cmd(c: CmdReq, auth=Depends(require_machine_auth)):
    d = {"endpoint_id":c.endpoint_id,"command_type":c.command_type,"payload":c.payload,"priority":c.priority,"issued_by":"openclaw"}
    r = await db.insert("endpoint_commands",d)
    await db.insert("audit_log",{"actor":"openclaw","action":"command_sent","target_type":"endpoint","target_id":c.endpoint_id,"new_value":d})
    return {"command": r[0] if r else None}

@router.post("/command/wait")
async def cmd_wait(c: CmdReq, timeout: int = Query(30), auth=Depends(require_machine_auth)):
    d = {"endpoint_id":c.endpoint_id,"command_type":c.command_type,"payload":c.payload,"priority":max(c.priority,2),"issued_by":"openclaw"}
    r = await db.insert("endpoint_commands",d)
    if not r: raise HTTPException(500,"Failed")
    cid = r[0]["id"]; elapsed = 0
    while elapsed < timeout:
        await asyncio.sleep(1); elapsed += 1
        s = await db.query("endpoint_commands",f"id=eq.{cid}")
        if s and s[0].get("status") in ("completed","failed"):
            return {"command_id":cid,"status":s[0]["status"],"result":s[0].get("result"),"elapsed":elapsed}
    return {"command_id":cid,"status":"timeout","message":f"Not completed in {timeout}s"}

@router.get("/command/{cid}")
async def get_cmd(cid: str, auth=Depends(require_machine_auth)):
    c = await db.query("endpoint_commands",f"id=eq.{cid}"); return c[0] if c else {"error":"Not found"}

@router.post("/bulk")
async def bulk(req: BulkQ, auth=Depends(require_machine_auth)):
    if req.endpoint_ids: eps = []; [eps.extend(await db.query("endpoints",f"id=eq.{eid}")) for eid in req.endpoint_ids]
    else: eps = await db.query("endpoints",f"company_id=eq.{req.company_id}&status=eq.active")
    results = {}
    for ep in eps:
        eid = ep["id"]
        if req.query_type=="status":
            hb=await db.query("endpoint_heartbeats",f"endpoint_id=eq.{eid}&order=created_at.desc&limit=1")
            act=await db.query("endpoint_activity",f"endpoint_id=eq.{eid}&order=created_at.desc&limit=1")
            results[eid]={"user_name":ep["user_name"],"heartbeat":hb[0] if hb else None,"activity":act[0] if act else None}
        elif req.query_type=="files":
            f=await db.rpc("search_endpoint_files",{"p_company_id":req.company_id,"p_query":req.params.get("query","*"),"p_endpoint_id":eid,"p_limit":req.params.get("limit",10)})
            results[eid]={"user_name":ep["user_name"],"files":f}
        elif req.query_type=="activity":
            e=await db.query("endpoint_activity",f"endpoint_id=eq.{eid}&order=created_at.desc&limit={req.params.get('limit',20)}")
            results[eid]={"user_name":ep["user_name"],"events":e}
        elif req.query_type=="screenshots":
            s=await db.query("endpoint_screenshots",f"endpoint_id=eq.{eid}&order=captured_at.desc&limit={req.params.get('limit',5)}")
            results[eid]={"user_name":ep["user_name"],"screenshots":s}
    return {"query_type":req.query_type,"endpoint_count":len(results),"results":results}

@router.post("/policy")
async def upd_policy(u: PolicyUpd, auth=Depends(require_machine_auth)):
    if u.target_type=="endpoint":
        ch={}
        if u.screenshot_policy: ch["screenshot_policy_override"]=u.screenshot_policy
        if u.monitoring_config: ch["config_overrides"]=u.monitoring_config
        await db.update("endpoints",f"id=eq.{u.target_id}",ch)
        pl={}
        if u.screenshot_policy: pl["screenshot"]=u.screenshot_policy
        if u.monitoring_config: pl.update(u.monitoring_config)
        await db.insert("endpoint_commands",{"endpoint_id":u.target_id,"command_type":"config_update","payload":pl,"priority":2,"issued_by":"openclaw"})
    elif u.target_type=="company":
        ch={}
        if u.screenshot_policy: ch["screenshot_policy"]=u.screenshot_policy
        if u.monitoring_config: ch["monitoring_config"]=u.monitoring_config
        await db.update("company_configs",f"id=eq.{u.target_id}",ch)
    await db.insert("audit_log",{"actor":"openclaw","action":"policy_update","target_type":u.target_type,"target_id":u.target_id,"new_value":{"sp":u.screenshot_policy,"mc":u.monitoring_config}})
    return {"updated":True}

@router.post("/playbook/trigger")
async def trigger_pb(t: PBTrigger, auth=Depends(require_machine_auth)):
    r=await db.insert("endpoint_commands",{"endpoint_id":t.endpoint_id,"command_type":"playbook_trigger","payload":{"playbook_id":t.playbook_id,"input_data":t.input_data},"priority":3,"issued_by":"openclaw"})
    return {"command":r[0] if r else None}

@router.get("/user/{name}")
async def user_lookup(name: str, company_id: str = "corvex", auth=Depends(require_machine_auth)):
    eps=await db.query("endpoints",f"company_id=eq.{company_id}&status=eq.active&user_name=ilike.*{name}*")
    if not eps: eps=await db.query("endpoints",f"company_id=eq.{company_id}&user_email=ilike.*{name}*")
    if not eps: return {"found":False}
    ep=eps[0]; act=await db.query("endpoint_activity",f"endpoint_id=eq.{ep['id']}&event_type=eq.app_focus&order=created_at.desc&limit=5")
    return {"found":True,"endpoint_id":ep["id"],"user_name":ep["user_name"],"user_email":ep["user_email"],"device_name":ep["device_name"],
        "recent_activity":[{"app":a["application"],"title":a["window_title"],"at":a["created_at"]} for a in act]}

@router.get("/capabilities")
async def capabilities(auth=Depends(require_machine_auth)):
    return {"version":"1.0.0","endpoints":{
        "GET /m/fleet":"Full fleet overview","POST /m/ask":"Natural language query",
        "POST /m/command":"Send command (fire-and-forget)","POST /m/command/wait":"Send and block until result",
        "POST /m/bulk":"Query multiple endpoints","POST /m/policy":"Update policies",
        "GET /m/user/{name}":"Lookup user by name","GET /m/capabilities":"This endpoint",
        "POST /m/playbook/trigger":"Trigger a playbook"},
        "command_types":["query","action","config_update","screenshot_now","file_retrieve","playbook_trigger"],
        "auth":"X-OpenClaw-Key header"}
