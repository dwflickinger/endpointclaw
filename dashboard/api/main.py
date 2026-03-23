from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from api.routes import fleet, monitoring, screenshots, activity, keystrokes, files as files_rt, commands, policies, playbooks, analytics, machine_api
from api.services.supabase_client import init_supabase

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_supabase()
    yield

app = FastAPI(title="EndpointClaw Dashboard", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
for r, p, t in [(fleet.router,"/api/fleet","Fleet"),(monitoring.router,"/api/monitoring","Monitoring"),(screenshots.router,"/api/screenshots","Screenshots"),(activity.router,"/api/activity","Activity"),(keystrokes.router,"/api/keystrokes","Keystrokes"),(files_rt.router,"/api/files","Files"),(commands.router,"/api/commands","Commands"),(policies.router,"/api/policies","Policies"),(playbooks.router,"/api/playbooks","Playbooks"),(analytics.router,"/api/analytics","Analytics"),(machine_api.router,"/m","MachineAPI")]:
    app.include_router(r, prefix=p, tags=[t])

@app.get("/")
async def index(): return FileResponse("frontend/public/index.html")
