# EndpointClaw — Complete Project
AI-powered endpoint monitoring for Corvex Roofing Solutions  
Built for David Flickinger | March 2026

## Quick Start

### 1. Supabase Schema
Run `schema/endpointclaw_schema.sql` in your Supabase SQL Editor.
Create storage bucket `endpointclaw-screenshots` (private).

### 2. Agent (each Windows machine)
```bash
cd agent && pip install -e .
set SUPABASE_SERVICE_ROLE_KEY=your-key
set ENDPOINTCLAW_EMAIL=andrew@corvexroofing.com
set ANTHROPIC_API_KEY=your-key
python -m agent.src.main
```

### 3. Dashboard
```bash
cd dashboard && pip install -e .
export SUPABASE_URL=https://twgdhuimqspfoimfmyxz.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=your-key
export DASHBOARD_SECRET=your-dashboard-token
export OPENCLAW_API_KEY=your-openclaw-key
uvicorn api.main:app --host 0.0.0.0 --port 3000
```
Or: `docker-compose up -d`

### 4. OpenClaw Machine API
```bash
curl -H "X-OpenClaw-Key: your-key" http://localhost:3000/m/fleet
curl -X POST -H "X-OpenClaw-Key: your-key" -H "Content-Type: application/json" \
  -d '{"question":"What is Andrew working on?"}' http://localhost:3000/m/ask
curl -H "X-OpenClaw-Key: your-key" http://localhost:3000/m/capabilities
```

## Project Structure
```
endpointclaw/
├── schema/endpointclaw_schema.sql    ← 14 tables, RLS, functions, seed
├── agent/                            ← Windows endpoint agent
│   ├── config/default_config.json
│   └── src/
│       ├── main.py                   ← Entry point
│       ├── core/                     ← Config, DB, orchestrator, tray
│       ├── indexing/                  ← File scanner + classifier
│       ├── monitoring/               ← Activity, keystrokes, screenshots
│       ├── comms/                    ← Supabase relay + sync
│       ├── chat/                     ← Claude-powered local chat
│       └── ui/templates/index.html   ← Employee chat UI
├── dashboard/                        ← Owner dashboard + AI API
│   ├── api/
│   │   ├── main.py                   ← FastAPI app
│   │   ├── services/                 ← Supabase client
│   │   ├── middleware/               ← Auth (Bearer + API key)
│   │   └── routes/                   ← 11 route modules
│   │       └── machine_api.py        ← OpenClaw /m/ interface
│   ├── frontend/public/index.html    ← React dashboard SPA
│   ├── Dockerfile
│   └── docker-compose.yml
└── .env.example
```

## Environment Variables
| Variable | Where | Purpose |
|----------|-------|---------|
| SUPABASE_SERVICE_ROLE_KEY | Both | Full DB access |
| SUPABASE_URL | Dashboard | Supabase URL |
| ENDPOINTCLAW_EMAIL | Agent | Employee email |
| ANTHROPIC_API_KEY | Agent | Claude for chat |
| DASHBOARD_SECRET | Dashboard | UI auth token |
| OPENCLAW_API_KEY | Dashboard | Machine API key |

## Architecture
```
Employee PC          Supabase             Owner Dashboard
┌──────────┐       ┌──────────┐         ┌──────────────┐
│  Agent   │─sync─▶│ Postgres │◀─read──│  Web UI      │
│  SQLite  │─hb──▶│ Storage  │◀─read──│  /api/*      │
│  Chat UI │◀─cmd──│ Realtime │◀─write─│  /m/* (AI)   │
└──────────┘       └──────────┘         └──────────────┘
                                               ↑
                                         OpenClaw / AI
```
