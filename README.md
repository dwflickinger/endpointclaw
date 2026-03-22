# EndpointClaw 🦞

**Open-source endpoint agent for AI-powered workflow automation.**

EndpointClaw is a lightweight Windows agent that runs on employee workstations, providing local file indexing, an AI-powered chat assistant, and a relay protocol for central orchestration. It's designed to observe, learn, and eventually automate desktop workflows — starting with commercial roofing estimation and expanding to any knowledge-worker role.

## What It Does

| Phase | Capability | Status |
|-------|-----------|--------|
| **1A — Foundation** | Windows service, local file indexing, AI chat, relay protocol | 🚧 In Progress |
| **1B — Cloud Files** | SharePoint/OneDrive integration via Microsoft Graph API | ⏳ Planned |
| **2 — Workflow Learning** | Activity journaling, pattern recognition, workflow maps | ⏳ Planned |
| **3 — Autonomous Execution** | Playbook engine, desktop automation, role augmentation | ⏳ Planned |

## Architecture

```
                    ┌─────────────────────────┐
                    │    Central Orchestrator  │
                    │   (your AI backbone)     │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │      Relay Server        │
                    │  (Supabase Realtime +    │
                    │   REST API + Edge Fns)   │
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                   │
   ┌──────────▼──┐   ┌─────────▼───┐   ┌──────────▼──┐
   │ EndpointClaw│   │ EndpointClaw│   │ EndpointClaw│
   │  Workstation│   │  Workstation│   │  Workstation│
   │  Win 10/11  │   │  Win 10/11  │   │  Win 10/11  │
   └─────────────┘   └─────────────┘   └─────────────┘
```

All connections are **outbound from endpoints** (NAT-friendly). WebSocket via Supabase Realtime with REST fallback.

## Key Features

- **Windows Service** — Persistent background agent, auto-start on boot, system tray companion
- **File Indexing** — Real-time filesystem monitoring with full-text search (SQLite + FTS5)
- **AI Chat** — Local web UI at `localhost:8742` powered by Claude (Anthropic API)
- **Relay Protocol** — Bidirectional command channel via Supabase for central orchestration
- **Activity Capture** (Phase 2) — Structured event logging for workflow learning
- **Playbook Engine** (Phase 3) — Executable workflow definitions with shadow → guided → semi-autonomous → autonomous progression

## Quick Start

```bash
# Clone
git clone https://github.com/dwflickinger/endpointclaw.git
cd endpointclaw

# See docs/SPEC.md for the full specification
# See docs/ARCHITECTURE.md for technical deep-dive
```

## Project Structure

```
endpointclaw/
├── agent/                    # Core agent code
│   ├── service/              # Windows service + system tray
│   ├── indexer/              # File indexing engine
│   ├── chat/                 # Local AI chat backend
│   ├── relay/                # Supabase relay client
│   ├── activity/             # Activity capture (Phase 2)
│   └── playbook_engine/      # Playbook execution (Phase 3)
├── ui/                       # React local chat UI
│   ├── src/
│   └── public/
├── installer/                # Windows installer configs
├── scripts/                  # Setup & deployment scripts
├── docs/                     # Specifications & architecture
│   ├── SPEC.md               # Full build specification
│   └── ARCHITECTURE.md       # Technical architecture details
├── tests/
│   ├── unit/
│   └── integration/
├── .gitignore
├── LICENSE                   # MIT License
└── README.md
```

## Playbooks

The playbook engine executes structured workflow definitions with four execution modes:

| Mode | Behavior |
|------|----------|
| **Shadow** | Runs in parallel with the human, compares outputs. No real actions. |
| **Guided** | Walks user through each step with suggestions. User executes manually. |
| **Semi-Autonomous** | Executes automatically, pauses at decision points for approval. |
| **Autonomous** | Full execution, completion report for after-the-fact review. |

**Playbooks are proprietary to each deployment.** This repo contains the open-source engine; actual playbook definitions (which encode company-specific workflows, pricing logic, and business rules) are kept in private repositories.

## Non-Negotiable Principles

1. **Visible to the user** — System tray icon, no stealth mode
2. **Employee controls** — Pause button, sees their own data first
3. **No surveillance** — No keystroke logging, no screenshots, no screen recording
4. **Multi-tenant** — Company isolation from day one
5. **Lightweight** — <3% CPU, <300MB RAM in steady state
6. **Resilient** — Offline operation, crash recovery, auto-restart

## Tech Stack (Recommended)

| Component | Technology |
|-----------|-----------|
| Agent Runtime | Python (PyInstaller) or C# .NET |
| Local Storage | SQLite with FTS5 |
| Local Web UI | React |
| AI Backend | Claude (Anthropic API) |
| Central Relay | Supabase (Realtime + REST + Edge Functions) |
| Installer | Inno Setup or MSIX |

## Contributing

Contributions welcome! Please read [CONTRIBUTING.md](docs/CONTRIBUTING.md) before submitting PRs.

## License

[MIT License](LICENSE) — the agent engine is fully open source.

**Note:** Company-specific playbooks, workflow definitions, and business logic configurations are proprietary and maintained in separate private repositories.
