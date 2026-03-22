# Architecture

## Overview

EndpointClaw follows a hub-and-spoke architecture where multiple endpoint agents communicate with a central orchestrator through a relay server.

### Components

1. **Endpoint Agent** — Windows service running on each workstation
2. **Relay Server** — Supabase project providing Realtime WebSocket channels, REST API, and Edge Functions
3. **Central Orchestrator** — AI backbone (e.g., OpenClaw) that queries and directs endpoints

### Communication

- All connections are **outbound from endpoints** — NAT-friendly, no port forwarding needed
- Primary: WebSocket via Supabase Realtime (instant command delivery)
- Fallback: REST polling every 10 seconds
- Heartbeat: Every 60 seconds (lightweight POST with status)
- File index sync: Batch upsert every 5 minutes
- Activity events (Phase 2): Batch insert every 5 minutes

### Data Flow

```
Endpoint → Relay:
  - Heartbeat (status, CPU/memory, sync state)
  - File index changes (batch upsert)
  - Activity events (batch insert)
  - Command results

Orchestrator → Endpoint (via Relay):
  - File queries (search, get content)
  - Actions (open file, send chat message)
  - Config updates
  - Playbook triggers (Phase 3)
```

### Security

- Unique API key per endpoint (encrypted locally via Windows DPAPI)
- All traffic over HTTPS/WSS (Supabase enforces TLS)
- Row-Level Security (RLS) for company data isolation
- Device registration requires admin approval

### Local Storage

SQLite with WAL mode for crash-safe writes. FTS5 extension for full-text search on file content and tags.

### Resource Budget

| State | CPU | RAM | Disk |
|-------|-----|-----|------|
| Steady | < 3% | < 300 MB | < 2 GB |
| Indexing | < 15% | < 500 MB | < 5 GB |
| Playbook | < 25% | < 500 MB | Same |

## For More Detail

See [SPEC.md](SPEC.md) for the full build specification.
