# EndpointClaw — Revised Build Specification v2

**Local AI Agent Architecture for Portfolio Company Operations**



---

## 1. Executive Context

OpenClaw is an AI command-and-control layer that orchestrates operations across multiple portfolio companies (Corvex Roofing Solutions, IX Medsolutions, Excel Health, AgentClear) via WhatsApp, backed by Claude Opus on a Mac Mini. OpenClaw handles cross-company intelligence, executive decision support, and increasingly, direct operational execution (estimating, proposal delivery, CRM sync, case processing).

**What OpenClaw lacks is local presence.** It cannot see what is happening on an employee's machine, cannot access their local files, cannot observe their workflow patterns, and cannot execute tasks on their behalf at the desktop level.

EndpointClaw fills this gap. It is the nervous system that connects central AI command to the individual workstations where work actually happens.

### 1.1 What EndpointClaw Is

EndpointClaw is a lightweight, persistent agent that runs on each employee's workstation. It serves as OpenClaw's eyes, ears, and hands at the endpoint level.

**The strategic vision:** Every non-physical role at Corvex Roofing (and eventually other portfolio companies) should be augmentable or replaceable by an AI sub-agent. Estimators, project coordinators, office managers, AR/AP clerks — the goal is that EndpointClaw + OpenClaw can progressively absorb these roles. The only humans that remain indispensable are the ones who physically get on roofs.

The system has four phases, built incrementally:

| Phase | Name | What It Delivers |
|-------|------|-------------------|
| 1A | Foundation | Windows service, local file index, local AI chat, OpenClaw API channel |
| 1B | Cloud Files | SharePoint/OneDrive integration via Microsoft Graph API |
| 2 | Workflow Learning | Activity journaling, pattern recognition, workflow maps |
| 3 | Autonomous Execution | Automation playbooks, desktop control, sub-agent role replacement |

Phases are ordered by dependency, not calendar. Multiple phases can be built in parallel where dependencies allow (e.g., Phase 2 event capture can be built alongside Phase 1B cloud files). No prescribed timelines — functionality gates progression, not dates.

### 1.2 Architecture Overview

```
                    ┌─────────────────────────┐
                    │       OpenClaw           │
                    │   (Mac Mini / Claude)    │
                    │   Central Orchestrator   │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │    Relay Server          │
                    │  (Supabase Realtime +    │
                    │   REST API + Edge Fns)   │
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                   │
   ┌──────────▼──┐   ┌─────────▼───┐   ┌──────────▼──┐
   │ EndpointClaw│   │ EndpointClaw│   │ EndpointClaw│
   │  (Andrew)   │   │  (David L.) │   │  (Mark)     │
   │  Win 10/11  │   │  Win 10/11  │   │  Win 10/11  │
   └─────────────┘   └─────────────┘   └─────────────┘
```

**Communication decision (not left open):** WebSocket connection from each EndpointClaw instance to a Supabase Realtime channel, with REST fallback. All connections are outbound from endpoints (NAT-friendly). OpenClaw queries via the same Supabase project. This leverages existing infrastructure — Corvex RoofBids already runs on Supabase project `twgdhuimqspfoimfmyxz`.

### 1.3 Target Environment

**Initial deployment:** Corvex Roofing estimators on Windows 10/11 with Microsoft 365 (SharePoint/OneDrive).

**Current Corvex tech stack (context for the developer):**
- **RoofBids** (roofbids.ai): Custom estimating + proposal platform built on Supabase + React. Handles estimate creation, line items, proposal generation, digital signatures, CRM sync to AccuLynx.
- **AccuLynx**: CRM for job tracking, customer management, document storage. Has REST API at `api.acculynx.com/api/v2`.
- **Measurement/takeoff tools**: EagleView, iRoofing, or manual measurements. Outputs PDFs and measurement files.
- **Excel templates**: Mark Flickinger's estimating spreadsheets (TPO RhinoBond template, concrete deck template, etc.) used as reference pricing.
- **SharePoint/OneDrive**: Shared document libraries for price sheets, templates, project files.
- **Outlook/M365**: Email communication with customers, GCs, suppliers.
- **TimeClock**: Separate Supabase app (`nhjryrcqyevttpgkrsij`) for crew time tracking.

**Key Corvex personnel:**
- David Flickinger (Owner/CEO) — oversight, deal negotiation, strategic estimates
- Mark Flickinger — Senior Estimator, maintains pricing templates
- Andrew Jordan — Estimator, field measurements + proposals
- David Lopez — Estimator
- Evelyn Lopez — Office/Admin
- Sarah Whiting — Operations

**Multi-tenant from day one.** The codebase and infrastructure are shared. Each portfolio company gets its own configuration, data isolation, and AI personality. Corvex is first; IX Medsolutions and Excel Health follow.

### 1.4 Existing Capabilities to Leverage (Not Rebuild)

EndpointClaw should integrate with, not duplicate, capabilities that already exist:

| Capability | Already Exists In | EndpointClaw Should... |
|------------|-------------------|----------------------|
| Estimate calculation engine | RoofBids (`estimateCalculations.ts`) — supports deck types (metal/concrete/wood), attachment methods, full line-item generation | Feed takeoff data INTO RoofBids via API, not rebuild calc logic |
| Proposal PDF generation | RoofBids (jsPDF in `send-proposal-pdf` edge function) | Trigger proposal generation via existing edge function |
| AccuLynx CRM sync | RoofBids (`send-to-crm`, `sync-won-to-acculynx` edge functions) | Trigger CRM sync, not rebuild API integration |
| Material pricing | Mark's Excel templates + RoofBids rate tables | Sync pricing FROM Excel templates INTO RoofBids rate tables |
| Email delivery | Resend API + per-estimator Gmail OAuth (in progress) | Use existing email infrastructure |
| Customer/project data | RoofBids Supabase (estimates, customers, proposal_tokens tables) | Query existing tables, not maintain separate customer DB |

**Critical principle:** EndpointClaw's value is in bridging the gap between desktop work and the cloud systems that already exist. It is NOT a replacement for RoofBids — it is the conduit that feeds data into and pulls data out of RoofBids, AccuLynx, and other systems.

---

## 2. Phase 1A — Foundation

Phase 1A establishes the core: EndpointClaw runs on a Windows machine, indexes local files, provides a local AI assistant, and opens a bidirectional channel to OpenClaw.

### 2.1 Windows Service & System Tray

The agent runs as a Windows service that starts on boot, with a companion system tray application.

**System tray icon:**
- Visible at all times — no stealth mode
- Status indicator (green = connected, yellow = syncing, red = disconnected)
- Right-click menu: Open Chat, View Status, Pause Monitoring, Settings, About
- Clicking opens the local web UI in the default browser

**Service requirements:**
- Auto-start on Windows boot (before user login for the service; tray app launches on user login)
- Auto-restart on crash with exponential backoff
- CPU < 2-3% steady state, < 300MB RAM, yields to user activity during indexing
- All state persisted to local SQLite — no data loss on crash or restart

### 2.2 File Indexing Engine

#### 2.2.1 What Gets Indexed (Phase 1A — Local Only)

Standard user directories: Desktop, Documents, Downloads, plus any additional paths configured per user. Exclude system files, Program Files, temp directories, and node_modules/build artifacts.

**Index record per file:**

| Field | Description |
|-------|-------------|
| `file_path` | Full local path |
| `filename` | Name + extension |
| `file_size` | Bytes |
| `modified_at` | Last modified timestamp |
| `content_hash` | SHA-256 for change detection |
| `file_type` | Classified: estimate, proposal, takeoff, invoice, photo, measurement, price_sheet, correspondence, drawing, permit, other |
| `content_extract` | Extracted text — first 5,000 chars for documents, full text for small files (<50KB). OCR for scanned PDFs if practical within resource budget. |
| `inferred_project` | Project name extracted from filename/path/content (e.g., "Palm Bay Shopping Center") |
| `inferred_customer` | Customer name if detectable |
| `company_id` | Which portfolio company (from agent config) |
| `tags` | Auto-generated: materials mentioned, roof types, addresses, dollar amounts |

**File type classification** should use a combination of extension mapping and lightweight content analysis (Claude API call for ambiguous files, batched to minimize API cost).

#### 2.2.2 Indexing Behavior

1. **Initial scan:** Background full scan on first run. Progress visible in system tray. Should complete within 1-2 hours for a typical estimator workstation.
2. **Continuous monitoring:** Windows `ReadDirectoryChangesW` (or equivalent library) for real-time file change detection in monitored directories.
3. **Daily reconciliation:** Full re-scan at a configured time (default: 2 AM) to catch anything watchers missed.
4. **Content re-extraction:** Only when `content_hash` changes. Don't re-extract unchanged files.

#### 2.2.3 Local Storage

SQLite database in the agent's data directory (e.g., `%APPDATA%\EndpointClaw\index.db`). Schema should support full-text search on `content_extract` and `tags` fields.

### 2.3 Local AI Chat Interface

**Implementation: Local web UI** served at `localhost:PORT` (port configurable, default 8742). The agent opens this URL when the user clicks the system tray icon.

#### 2.3.1 UI Requirements

- Clean, simple chat interface. No enterprise bloat.
- Message input with send button and keyboard shortcut (Enter)
- Conversation history displayed in chat bubbles
- File references in responses should be clickable (opens the file locally)
- "Quick actions" sidebar or buttons for common tasks: "Find file...", "Summarize document...", "What did I work on today?"
- Company branding configurable (Corvex logo + colors for Corvex deployment)
- Responsive — works in any browser window size

#### 2.3.2 AI Backend

Claude API (Anthropic) powers the assistant. **Not a local model** — the quality ceiling matters more than offline capability for this use case.

**System prompt structure:**
```
[Company context — pulled from central config at startup]
[User context — who this employee is, their role, their team]
[File index summary — top-level stats, recent files, project list]
[Conversation history — last N exchanges]
[User's current query]
```

**Company-specific prompts (examples):**
- **Corvex:** "You are a roofing estimating assistant. You understand commercial roofing systems (TPO, EPDM, modified bitumen, metal), measurement terminology (squares, linear feet, penetrations), and the Corvex estimating workflow. You know the team: Mark is the senior estimator, Andrew and David Lopez are estimators."
- **IX Medsolutions:** "You are a medical device case processing assistant. You understand spinal implant products, charge sheet processing, and surgical case coordination."

**File-aware capabilities:**
- "Find the latest takeoff for Palm Bay" → searches index, returns file with path
- "What's the total on the Rodriguez estimate?" → opens/reads the file, extracts the answer
- "Compare material costs between the Johnson and Martinez estimates" → reads both files, presents comparison
- "Open the Q1 price sheet" → launches the file in its default application

**Conversation persistence:** Store conversations in local SQLite. Maintain session context within a conversation. Key facts learned about the user's work should be persisted across sessions (lightweight memory, similar to how OpenClaw's MEMORY.md works but per-user).

### 2.4 OpenClaw Communication Channel

**This is the spine of the system. Not optional, not deferred.**

#### 2.4.1 Architecture: Supabase Relay

Use the existing Corvex RoofBids Supabase project (`twgdhuimqspfoimfmyxz`) as the relay infrastructure. New tables and edge functions handle the communication.

**New Supabase tables:**

```sql
-- Registered EndpointClaw instances
CREATE TABLE endpoints (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_name TEXT NOT NULL,
  user_email TEXT NOT NULL,
  company_id TEXT NOT NULL,
  api_key TEXT NOT NULL UNIQUE,
  status TEXT DEFAULT 'active', -- active, paused, offline
  last_heartbeat TIMESTAMPTZ,
  agent_version TEXT,
  os_version TEXT,
  config JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Commands from OpenClaw to endpoints
CREATE TABLE endpoint_commands (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  endpoint_id UUID REFERENCES endpoints(id),
  command_type TEXT NOT NULL, -- query, action, config_update
  payload JSONB NOT NULL,
  status TEXT DEFAULT 'pending', -- pending, acknowledged, in_progress, completed, failed
  result JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);

-- File index sync (endpoints push to central)
CREATE TABLE endpoint_file_index (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  endpoint_id UUID REFERENCES endpoints(id),
  file_path TEXT NOT NULL,
  filename TEXT NOT NULL,
  file_type TEXT,
  file_size BIGINT,
  modified_at TIMESTAMPTZ,
  content_hash TEXT,
  content_extract TEXT,
  inferred_project TEXT,
  inferred_customer TEXT,
  tags TEXT[],
  company_id TEXT NOT NULL,
  synced_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(endpoint_id, file_path)
);

-- Activity events (Phase 2, but table created now)
CREATE TABLE endpoint_activity (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  endpoint_id UUID REFERENCES endpoints(id),
  event_type TEXT NOT NULL,
  application TEXT,
  window_title TEXT,
  file_path TEXT,
  file_id UUID,
  duration_ms INTEGER,
  metadata JSONB,
  company_id TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

**RLS policies:** Strict company isolation. Endpoints can only read/write their own data. OpenClaw queries via service role key (already in use for RoofBids).

#### 2.4.2 Communication Flow

**Endpoint → Relay (push):**
- Heartbeat every 60 seconds (lightweight POST with status, CPU/memory, sync state)
- File index sync: batch upsert of new/changed/deleted files every 5 minutes
- Activity events (Phase 2): batch insert every 5 minutes
- Query results: update `endpoint_commands.result` when a command completes

**OpenClaw → Endpoint (pull-based commands):**
- OpenClaw inserts a row into `endpoint_commands`
- Endpoint polls `endpoint_commands` for pending commands (every 10 seconds, or use Supabase Realtime subscription for instant delivery)
- Endpoint processes command, updates status and result

**Supabase Realtime (preferred over polling):**
- Endpoint subscribes to `endpoint_commands` changes filtered by its own `endpoint_id`
- Instant command delivery, no polling overhead
- Falls back to REST polling if WebSocket connection drops

#### 2.4.3 Command Types

**Queries (read-only):**
```json
{"command_type": "query", "payload": {
  "action": "search_files",
  "query": "takeoff Palm Bay",
  "file_type": "measurement"
}}

{"command_type": "query", "payload": {
  "action": "get_file_content",
  "file_path": "C:\\Users\\ajordan\\Documents\\Palm Bay\\takeoff.pdf"
}}

{"command_type": "query", "payload": {
  "action": "user_status",
  // Returns: active app, current file, last activity timestamp
}}
```

**Actions (write/execute):**
```json
{"command_type": "action", "payload": {
  "action": "open_file",
  "file_path": "C:\\Users\\ajordan\\Documents\\price-sheet-2026.xlsx"
}}

{"command_type": "action", "payload": {
  "action": "chat_message",
  "message": "Hey Andrew, can you re-measure the south parapet on the Rodriguez job? Mark thinks it might be 24\" not 18\"."
}}
```

**Config updates:**
```json
{"command_type": "config_update", "payload": {
  "monitored_paths": ["C:\\Users\\ajordan\\Documents", "C:\\Users\\ajordan\\OneDrive"],
  "system_prompt_override": "...",
  "sync_interval_seconds": 300
}}
```

#### 2.4.4 Authentication & Security

- Each EndpointClaw instance gets a unique API key generated during installation/registration
- API key is stored locally in an encrypted config file (Windows DPAPI or equivalent)
- All communication over HTTPS (Supabase enforces TLS)
- Company isolation enforced at the database level via RLS
- Device registration requires a one-time company admin approval (prevents rogue installations)

### 2.5 Installation & Deployment

**Installer:** MSI or EXE built with a standard tool (WiX, Inno Setup, NSIS, or equivalent for the chosen language).

**Installation flow:**
1. Run installer (can be silent for RMM/GPO deployment: `EndpointClawSetup.exe /S /COMPANY=corvex /API_KEY=xxx`)
2. Installs Windows service + system tray companion
3. On first launch, opens browser to `localhost:8742/setup` for:
   - Company selection (if not pre-configured)
   - User email verification
   - Directory selection for monitoring (defaults pre-filled)
   - Connection test to Supabase relay
4. Agent begins initial file scan
5. System tray icon appears, chat is available

**Auto-updates:** Agent checks for new versions on a configurable interval (default: daily). Downloads and applies updates silently with a service restart. Version info reported in heartbeat.

### 2.6 Phase 1A Success Criteria

- [ ] EndpointClaw installs cleanly on Windows 10/11 and runs as a persistent service
- [ ] Local file index is built and stays current via filesystem watchers
- [ ] Employee can chat with a file-aware AI assistant via local web UI
- [ ] OpenClaw can query any endpoint's file index via Supabase
- [ ] OpenClaw can send commands and receive results
- [ ] Resource usage stays within budget (<3% CPU, <300MB RAM)
- [ ] At least one Corvex estimator running for 3+ days with no crashes

---

## 3. Phase 1B — Cloud Files

### 3.1 Microsoft Graph API Integration

Add SharePoint and OneDrive file indexing to the existing local file index.

**Authentication:** OAuth2 via Azure AD app registration. The developer should register a multi-tenant Azure AD application with the following Graph API permissions:
- `Files.Read.All` (read OneDrive/SharePoint files)
- `Sites.Read.All` (read SharePoint sites/libraries)
- `User.Read` (basic user profile)

**Indexing scope:**
- User's OneDrive for Business (all files)
- SharePoint document libraries the user has access to
- Shared files/folders explicitly shared with the user

**Sync approach:**
- Use Graph API delta queries (`/me/drive/root/delta`) for efficient change detection
- Poll every 15 minutes (configurable)
- Index records go into the same `endpoint_file_index` table with a `source` field distinguishing `local` vs `onedrive` vs `sharepoint`

**File content access:**
- For files synced locally via OneDrive, read from the local sync folder (faster, no API call)
- For cloud-only files, download via Graph API on demand when content is requested

### 3.2 Outlook Email Indexing (Stretch)

If practical within the Phase 1B timeline:
- Index email subjects, senders, recipients, and attachment names (not email body content)
- Use Graph API `/me/messages` with delta queries
- Focus on emails with attachments (estimates, proposals, contracts)
- Store in a separate `endpoint_email_index` table

### 3.3 Phase 1B Success Criteria

- [ ] SharePoint/OneDrive files appear in the file index alongside local files
- [ ] Changes in cloud files are detected within 15 minutes
- [ ] Local chat can find and reference cloud files
- [ ] OpenClaw can search across both local and cloud files in a single query

---

## 4. Phase 2 — Workflow Learning

Phase 2 adds the observation engine. The agent learns how employees actually work by logging structured events.

### 4.1 Employee Positioning (Critical)

**This must be handled carefully.** Activity logging will face resistance if positioned as surveillance.

**Required approach:**
1. **Frame as personal productivity tool first.** "See how you spend your time. Find bottlenecks. Get suggestions." The employee's own dashboard is the primary output.
2. **Employee sees their data before management does.** The local chat should answer "What did I work on today?" before any manager can ask the same question.
3. **Explicit opt-in for team-level visibility.** Individual activity data is visible to the employee by default. Team-level aggregation and management dashboards require explicit communication (and ideally, consent).
4. **No keystroke logging, no screenshots, no content capture of what's typed.** Only structured events: what app was active, what file was open, for how long.
5. **Pause button in system tray.** Employee can pause monitoring for breaks, personal tasks, etc.

### 4.2 Activity Data Collection

#### 4.2.1 Event Types

| Event Type | Source | What's Captured |
|------------|--------|-----------------|
| `app_focus` | Win32 API (SetWinEventHook) | App name, window title, start/end timestamps, duration |
| `file_opened` | Filesystem watcher + Win32 | File path, application used, timestamp |
| `file_saved` | Filesystem watcher | File path, size delta, timestamp |
| `file_created` | Filesystem watcher | File path, probable source (download, save-as, copy) |
| `file_emailed` | Outlook COM/Graph (if feasible) | File attached to email, recipient(s) |
| `file_uploaded` | Network monitor or browser URL (optional) | File sent to web app (AccuLynx, RoofBids, etc.) |
| `browser_tab` | Optional, configurable | Active URL domain + page title (not full URL for privacy) |
| `idle_start` / `idle_end` | Win32 (GetLastInputInfo) | User went idle / returned |

#### 4.2.2 Event Structure

```json
{
  "id": "uuid",
  "endpoint_id": "uuid",
  "event_type": "app_focus",
  "timestamp": "2026-03-22T14:30:45Z",
  "application": "EXCEL.EXE",
  "window_title": "Johnson Estimate - TPO Rhino.xlsx - Excel",
  "file_path": "C:\\Users\\ajordan\\Documents\\Estimates\\Johnson\\TPO Rhino.xlsx",
  "file_id": "uuid (ref to file index)",
  "duration_ms": 842000,
  "metadata": {"sheet_name": "Material Costs"},
  "user_email": "andrew@corvexroofing.com",
  "company_id": "corvex"
}
```

#### 4.2.3 Storage & Sync

- **Local buffer:** SQLite table, events written immediately
- **Central sync:** Batch POST to Supabase `endpoint_activity` table every 5 minutes
- **Deduplication:** Collapse consecutive `app_focus` events for the same window into a single event with cumulative duration
- **Local retention:** 30 days after successful sync, then prune
- **Central retention:** Indefinite (this is the training data for Phase 3)
- **Estimated volume:** ~500-2,000 events/user/day after dedup. Manageable.

### 4.3 Workflow Pattern Recognition

#### 4.3.1 Approach: AI-Powered Analysis (Not Custom ML)

The pattern recognition engine uses periodic Claude API calls against batched activity data. No custom ML models needed.

**Weekly analysis job (runs centrally, not on endpoints):**

1. Pull one week of activity data per user from `endpoint_activity`
2. Group into "work sessions" (continuous activity between idle periods)
3. Send to Claude with a structured prompt:

```
Analyze this employee's work activity for the past week. The employee is a commercial roofing estimator.

Identify:
1. Recurring workflow sequences (what apps/files in what order, how often)
2. Time allocation by activity type (estimating, proposals, email, admin)
3. Bottleneck patterns (where does the most time get spent? what steps seem manual/repetitive?)
4. Anomalies (unusual patterns that might indicate confusion, rework, or inefficiency)

Activity data:
[structured events]
```

4. Store recognized patterns in a `workflow_patterns` table:

```sql
CREATE TABLE workflow_patterns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  pattern_name TEXT, -- "Commercial Roof Estimate", "Proposal Delivery"
  description TEXT,
  steps JSONB, -- ordered list of step definitions
  avg_duration_minutes INTEGER,
  frequency_per_week REAL,
  users TEXT[], -- which users exhibit this pattern
  automation_score REAL, -- 0-1, how automatable
  detected_at TIMESTAMPTZ DEFAULT now(),
  status TEXT DEFAULT 'detected' -- detected, confirmed, playbook_drafted, automated
);
```

#### 4.3.2 The Corvex Estimating Workflow (Known Target)

We already know the primary workflow to detect and ultimately automate. This is what the pattern recognition should confirm and refine:

1. Receive bid invitation (email or phone call)
2. Download/review plans (PDF, typically architectural drawings)
3. Perform takeoff/measurement (EagleView, iRoofing, or manual from plans)
4. Open estimating template (Excel or RoofBids)
5. Enter measurements and select materials/system
6. Pull current pricing (SharePoint price sheet or RoofBids rate tables)
7. Calculate quantities, labor, overhead, profit
8. Generate proposal PDF
9. Email proposal to customer/GC
10. Log in AccuLynx CRM
11. Follow up

**What's already automated (via RoofBids + OpenClaw):** Steps 6-10 are partially automated. RoofBids handles calculation, proposal PDF, email delivery, and AccuLynx sync. **The gap is steps 1-5** — getting bid invitations processed, plans analyzed, and measurements into the system.

### 4.4 Reporting

#### 4.4.1 Employee Self-Service (via local chat)

- "What did I work on today?" → time breakdown by project/activity
- "How long did the Rodriguez estimate take?" → total time from first file touch to proposal sent
- "What's my average estimate turnaround time?" → historical analysis

#### 4.4.2 OpenClaw Queries

- "What has the estimating team been working on this week?"
- "How much time is being spent on estimating vs. admin?"
- "Which estimator has the fastest turnaround on proposals?"
- "Are there any projects where an estimator seems stuck?"

#### 4.4.3 Workflow Maps

Generated by the weekly analysis — visual or structured representations stored centrally and queryable by OpenClaw. These feed directly into Phase 3 playbook creation.

### 4.5 Phase 2 Success Criteria

- [ ] Activity events captured in real-time without perceptible performance impact
- [ ] Data syncs reliably to central store
- [ ] At least 3 recurring workflow patterns identified from real data
- [ ] Employees can query their own activity via local chat
- [ ] OpenClaw can query activity summaries for any user or team
- [ ] Employee sentiment is neutral-to-positive (not perceived as surveillance)

---

## 5. Phase 3 — Autonomous Execution

Phase 3 is where EndpointClaw transitions from observing to acting. The strategic goal: every step in the estimating workflow that doesn't require a human on a roof should be executable by a sub-agent.

### 5.1 Playbook Architecture

A playbook is a structured, executable workflow definition.

```json
{
  "id": "uuid",
  "name": "Commercial Roof Estimate - TPO",
  "version": 3,
  "trigger": {
    "type": "event",
    "condition": "new_takeoff_file_detected",
    "manual_trigger": true
  },
  "execution_mode": "semi_autonomous",
  "steps": [
    {
      "id": 1,
      "name": "Ingest Takeoff Data",
      "type": "automated",
      "action": "extract_measurements",
      "input": "takeoff_file",
      "output": "measurements_json",
      "validation": {
        "check": "measurements_complete",
        "fields": ["total_area_sf", "perimeter_lf", "penetration_count"]
      }
    },
    {
      "id": 2,
      "name": "Determine Roof System",
      "type": "decision_point",
      "prompt": "Based on the plans and specs, recommend a roof system",
      "ai_suggestion": true,
      "requires_approval": true
    }
  ],
  "metrics": {
    "avg_execution_minutes": 45,
    "manual_baseline_minutes": 180,
    "success_rate": 0.94,
    "override_rate": 0.12
  }
}
```

### 5.2 Execution Modes

| Mode | Behavior | When to Use |
|------|----------|-------------|
| **Shadow** | Runs in parallel with the human, compares outputs. No real actions. | Pre-deployment validation. Run for 2+ weeks before going live. |
| **Guided** | Walks user through each step with suggestions. User executes manually. | New workflows, building trust. |
| **Semi-Autonomous** | Executes automatically, pauses at decision points for human approval. | Default for most automation. |
| **Autonomous** | Full execution, completion report sent for after-the-fact review. | High-volume, low-risk, validated workflows. |

**Mode progression is per-playbook, per-user.** Andrew might be on Semi-Autonomous for TPO estimates while David Lopez is still on Guided.

### 5.3 Desktop Automation Engine

**Priority order of automation approaches:**

1. **API-first (strongly preferred):** Use RoofBids Supabase API, AccuLynx REST API, Microsoft Graph API, Gmail API. These are reliable, testable, and don't break when UI changes.

2. **File-based:** Read/write Excel files (openpyxl, xlsxwriter), generate PDFs (reportlab, jsPDF), parse measurement files. This covers a huge amount of the estimating workflow.

3. **COM automation (Windows-specific):** Excel COM for workbook manipulation, Outlook COM for email, Word COM for document generation. More powerful than file-based but tied to Windows.

4. **UI automation (last resort):** pywinauto, Windows UI Automation API, or similar. Only for applications with no API and no file-based alternative. Fragile, test-heavy, breaks on UI updates.

### 5.4 The First Playbook: Plan-to-Estimate

This is the highest-value automation target. It bridges the gap between receiving plans and having a complete estimate in RoofBids.

**Trigger:** New architectural plan PDF detected in an estimator's watched folder, OR OpenClaw directs "Build an estimate for [project]."

**Step 1 — Plan Ingestion & Analysis (AI-powered):**
- Extract text and images from PDF plans
- Use Claude vision to identify roof plan sheets, wall sections, details
- Extract: roof area, perimeter, slope, membrane type, insulation spec, attachment method, deck type, penetrations, scuppers/drains, coping type, edge conditions
- Cross-reference spec sections for discrepancies (like Palm Bay's EPDM-in-spec vs TPO-on-plans issue)
- Output: structured measurements JSON + list of RFIs

**Step 2 — System Selection (decision point):**
- AI recommends roof system based on extracted specs
- Presents options with rationale
- Estimator confirms or overrides
- Flags discrepancies for RFI

**Step 3 — Estimate Generation (automated via RoofBids API):**
- Call RoofBids `estimateCalculations.ts` logic via API (or directly create estimate records in Supabase)
- Use deck-type-specific pricing (metal vs concrete vs wood)
- Apply Mark's calibrated unit rates
- Generate complete line-item estimate

**Step 4 — Review (decision point):**
- Present estimate to estimator for review
- Compare to historical estimates for similar size/type
- Flag any line items outside normal range
- Estimator approves or adjusts

**Step 5 — Proposal + Delivery (automated):**
- Trigger `send-proposal-pdf` edge function
- Generate proposal with rounded totals
- Queue email for estimator review (or auto-send in Autonomous mode)
- Sync to AccuLynx via `send-to-crm`

**This playbook replaces ~3 hours of manual work with ~15 minutes of review at decision points.**

### 5.5 Sub-Agent Role Replacement Roadmap

The long-term vision — each role mapped to automation potential:

| Role | Current Human | Automation Path | Target State |
|------|--------------|-----------------|--------------|
| **Estimator** | Andrew, David L., Mark | Plan analysis → measurement → estimate → proposal → CRM. Phase 3 playbooks. | Human reviews AI-generated estimates, handles complex/custom jobs. 70% volume reduction in manual work. |
| **Project Coordinator** | (varies) | Automated status tracking, customer follow-up emails, schedule coordination via AccuLynx API + calendar integration. | AI handles routine coordination. Human handles escalations and relationship management. |
| **Office Manager** | Evelyn | AR/AP automation (invoice generation from completed jobs, payment tracking), document filing (auto-classify and file in SharePoint), permit tracking. | AI handles 80% of admin tasks. Human handles exceptions and vendor relationships. |
| **Ops/Admin** | Sarah | TimeClock review automation, crew scheduling suggestions, material ordering based on upcoming jobs. | AI provides recommendations. Human approves and handles crew communication. |
| **Sales/BD** | David F. | Lead scoring from bid invitations, auto-response to RFPs, pipeline reporting. | AI triages and prioritizes. Human handles relationship building and deal closing. |

### 5.6 OpenClaw Integration for Phase 3

Phase 3 massively expands what OpenClaw (me) can do:

- **"Run an estimate on the Rodriguez plans"** → triggers Plan-to-Estimate playbook on the appropriate estimator's machine
- **"What's the status of all open estimates?"** → queries running playbooks across all endpoints
- **"How much time has automation saved this month?"** → pulls playbook metrics
- **"Andrew is out sick — reassign his pending estimates to David Lopez"** → transfers playbook context between endpoints
- **"Generate the weekly estimating report"** → aggregates data from all endpoints + RoofBids + AccuLynx

### 5.7 Phase 3 Success Criteria

- [ ] Plan-to-Estimate playbook running in Semi-Autonomous mode with at least one estimator
- [ ] Shadow mode validated accuracy for 2+ weeks before going live
- [ ] Measurable time savings of 30%+ on automated workflows
- [ ] OpenClaw can trigger, monitor, and report on playbooks
- [ ] Playbooks are versioned with rollback capability
- [ ] At least one additional playbook beyond estimating (e.g., proposal follow-up, permit tracking)

---

## 6. Cross-Cutting Concerns

### 6.1 Performance & Resource Budget

| Resource | Steady State | During Indexing | During Playbook |
|----------|-------------|-----------------|-----------------|
| CPU | < 2-3% | < 15% (yields to user) | < 25% (yields to user) |
| RAM | < 300 MB | < 500 MB | < 500 MB |
| Disk | < 2 GB | < 5 GB during initial scan | Same |
| Network | < 100 KB/s avg | Burst during sync | Burst during API calls |

### 6.2 Error Handling & Resilience

- **Offline operation:** If Supabase is unreachable, agent continues locally (indexing, local chat, activity logging). Queues data for sync on reconnection.
- **Crash recovery:** Windows service auto-restarts. SQLite WAL mode for crash-safe writes. No in-memory-only state.
- **Central logging:** Agent sends structured logs to a central endpoint for remote troubleshooting. Log level configurable per-endpoint.
- **Alerting:** Endpoint offline for >30 minutes → alert to OpenClaw. Sync backlog >1 hour → alert. Playbook failure → immediate alert.

### 6.3 Multi-Company Configuration

Per-company config stored centrally (Supabase `company_configs` table), pulled at agent startup:

```json
{
  "company_id": "corvex",
  "company_name": "Corvex Roofing Solutions",
  "ai_system_prompt": "You are a roofing estimating assistant...",
  "monitored_extensions": [".xlsx", ".pdf", ".docx", ".dwg", ".dxf", ".jpg", ".png"],
  "activity_tracking": {
    "enabled": true,
    "capture_browser": false,
    "capture_clipboard": false,
    "idle_threshold_minutes": 5
  },
  "integrations": {
    "roofbids_project_ref": "twgdhuimqspfoimfmyxz",
    "acculynx_api_base": "https://api.acculynx.com/api/v2",
    "sharepoint_site_id": "..."
  },
  "branding": {
    "logo_url": "https://corvexroofing.com/logo.png",
    "primary_color": "#1a365d",
    "accent_color": "#c05621"
  }
}
```

### 6.4 Observability & Fleet Management

A central admin view (web dashboard or OpenClaw-queryable endpoints) providing:

- Fleet overview: all registered endpoints, status, last heartbeat, agent version
- Health metrics: CPU/memory per endpoint, error rates, sync lag
- Remote config: push configuration changes to endpoints
- Remote update: deploy new agent versions
- Activity overview: aggregate time-tracking across team

---

## 7. Technology Recommendations (Not Mandates)

Based on the requirements, here are recommendations. The developer should use their judgment.

| Component | Recommendation | Rationale |
|-----------|---------------|-----------|
| Agent runtime | **Python** (PyInstaller for packaging) or **C# .NET** | Python: fastest to build, rich library ecosystem (watchdog, openpyxl, pywinauto, sqlite3). C#: best Windows integration (native service, COM interop, DPAPI). |
| Local storage | **SQLite** (with FTS5 for full-text search) | Single-file, zero-config, crash-safe with WAL, full-text search built in. |
| Local web UI | **React** served by local HTTP server (FastAPI/Flask for Python, Kestrel for C#) | Rich chat UX, can reuse patterns from RoofBids codebase. |
| Central relay | **Supabase** (existing project) | Already in use, Realtime channels for WebSocket, Edge Functions for processing, RLS for isolation. |
| Installer | **Inno Setup** (Python) or **MSIX** (C#) | Standard Windows installer formats with silent install support. |
| PDF/Office manipulation | **openpyxl** (Excel), **PyMuPDF/fitz** (PDF), **python-docx** (Word) | Reliable, well-maintained libraries. |
| Desktop automation | **pywinauto** + **comtypes** (COM) | pywinauto for UI automation fallback, comtypes for Excel/Outlook COM. |

---

## 8. Development Sequence (Recommended)

### Phase 1A — Build Order:
1. Windows service skeleton + system tray icon + auto-start
2. Local SQLite database + file indexing engine + filesystem watchers
3. Local web UI (React chat interface) + Claude API integration
4. Supabase tables + endpoint registration + heartbeat
5. OpenClaw command channel (endpoint_commands table + Realtime subscription)
6. Installer packaging (MSI/EXE)
7. Deploy to one Corvex estimator machine for testing

### Phase 1B — Build Order:
1. Azure AD app registration + Graph API OAuth
2. OneDrive delta sync integration
3. SharePoint document library indexing
4. Unified file search across local + cloud
5. Deploy to second estimator

### Phase 2 — Build Order:
1. Win32 event hooks (app focus, window title)
2. File lifecycle event capture
3. Local event buffer + central sync
4. Employee self-service activity queries in local chat
5. Weekly AI-powered workflow analysis job
6. Basic activity reporting via OpenClaw queries
7. Pattern storage and visualization

### Phase 3 — Build Order:
1. Playbook data model + management API
2. Shadow mode execution framework
3. Plan-to-Estimate playbook (the first and most valuable)
4. Semi-autonomous execution with decision points
5. Performance tracking + metrics
6. Additional playbooks based on Phase 2 patterns

---

## 9. Non-Negotiable Requirements

Regardless of implementation choices, these are firm:

1. Runs on Windows 10/11 as a persistent background service
2. Multi-company tenancy with data isolation from day one
3. OpenClaw can query and direct any EndpointClaw instance
4. Claude (Anthropic API) is the AI backend
5. No perceptible performance degradation on the user's machine
6. Simple installation deployable by non-technical staff
7. Agent is visible to the user (system tray icon, no stealth)
8. Employee can pause monitoring at any time
9. Employee sees their own data before management does
10. No keystroke logging, no screenshots, no screen recording

---

## 10. Success Metrics (Overall)

| Metric | Target | Measured By |
|--------|--------|-------------|
| Estimate turnaround time | 50% reduction | Time from plans received to proposal sent |
| Estimator capacity | 2x more estimates per estimator | Weekly estimate count per person |
| Data visibility | 100% of project files indexed and searchable | OpenClaw query coverage |
| Employee satisfaction | Net positive | Direct feedback, usage metrics |
| Cost per estimate | 30% reduction | Labor hours per estimate × hourly rate |
| Automation coverage | 70% of estimating steps automated | Playbook step completion without manual intervention |

---

*End of Specification — v2*
