from fastapi import APIRouter, Query
from api.services import supabase_client as db
router = APIRouter()

@router.get("/search")
async def search(q: str, company_id: str = "corvex", endpoint_id: str = Query(None), file_type: str = Query(None), limit: int = 20):
    return {"files": await db.rpc("search_endpoint_files",{"p_company_id":company_id,"p_query":q,"p_file_type":file_type,"p_endpoint_id":endpoint_id,"p_limit":limit})}

@router.get("/browse/{eid}")
async def browse(eid: str, file_type: str = Query(None), project: str = Query(None), limit: int = 50, offset: int = 0):
    p = f"endpoint_id=eq.{eid}&order=modified_at.desc&limit={limit}&offset={offset}"
    if file_type: p += f"&file_type=eq.{file_type}"
    if project: p += f"&inferred_project=ilike.*{project}*"
    return {"files": await db.query("endpoint_file_index", p)}

@router.get("/stats")
async def stats(company_id: str = "corvex"):
    files = await db.query("endpoint_file_index", f"company_id=eq.{company_id}&select=file_type,file_size")
    bt = {}; ts = 0
    for f in files: bt[f.get("file_type","other")]=bt.get(f.get("file_type","other"),0)+1; ts+=f.get("file_size",0) or 0
    return {"total_files":len(files),"total_size_gb":round(ts/(1024**3),2),"by_type":dict(sorted(bt.items(),key=lambda x:x[1],reverse=True))}
