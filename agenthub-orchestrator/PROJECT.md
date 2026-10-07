# Project: Antigravity Teamwork Orchestrator

## Architecture
The Antigravity Teamwork Orchestrator is a desktop multi-agent control center that coordinates 4 independent Antigravity instances (1 Lead, 3 Workers) across isolated Google accounts via a shared filesystem hub (`.agenthub`).

```
+-------------------------------------------------------------------------------+
|                             Antigravity Teamwork UI                           |
|  (Activity Bar, Tabs, Breadcrumbs, Swarm Chat, Dispatch Form, Account Cards)  |
+---------------------------------------+---------------------------------------+
                                        | HTTP / REST (127.0.0.1:8989)
                                        v
+-------------------------------------------------------------------------------+
|                       Backend Server (`app.py`)                               |
|  - Process Launcher & Liveness Tracker (`psutil`, detached subprocess)        |
|  - Generic Static Asset Router (MIME detection for public/ assets)            |
|  - Atomic Hub File Controller (safe append, schema validation)                |
|  - Desktop Container (`pywebview` WebView2 with browser fallback)             |
+---------------------------------------+---------------------------------------+
                                        | File IPC
                                        v
+-------------------------------------------------------------------------------+
|                       Shared Filesystem (`.agenthub/`)                        |
|  ├── BOARD.md           (Mission contract, responsibility matrix, team rules) |
|  ├── TEAM_CHAT.md       (Live multi-agent chronological message log)          |
|  └── inbox/                                                                   |
|      ├── worker1.md     (Backend/Core task assignment)                        |
|      ├── worker2.md     (Frontend/UI task assignment)                         |
|      ├── worker3.md     (Testing/QA task assignment)                          |
|      └── lead_review.md (Worker deliverables submission & Lead QA)            |
+---------------------------------------+---------------------------------------+
        ^                               ^                               ^
        |                               |                               |
+-------+---------------+       +-------+---------------+       +-------+---------------+
| Lead (Antigravity.exe)|       | Worker 1 (Worker1.exe)|       | Worker 2 (Worker2.exe)|
| Roaming/Antigravity   |       | Roaming/Worker 1      |       | Roaming/Worker 2      |
| Account 1 (Main Lead) |       | Account 2 (Backend)   |       | Account 3 (Frontend)  |
+-----------------------+       +-----------------------+       +-----------------------+
                                        |
                                +-------+---------------+
                                | Worker 3 (Worker3.exe)|
                                | Roaming/Worker 3      |
                                | Account 4 (Testing)   |
                                +-----------------------+
```

## Feature Inventory
All 21 features identified during the Phase 0 Survey mapped to explicit milestones:

| # | Category | Feature | Description | Milestone | Source |
|---|----------|---------|-------------|-----------|--------|
| 1 | Hub Protocol | Workspace Initialization | Auto-initializes `.agenthub` and `inbox/` with templates if missing | M1 | Survey |
| 2 | Hub Protocol | Central Board Management | Manages `BOARD.md` data contract and responsibility matrix | M1 | Survey |
| 3 | Hub Protocol | Worker Task Inboxes | Generates individualized task orders in `inbox/worker{1,2,3}.md` | M1 | Survey |
| 4 | Hub Protocol | Live Swarm Chat Stream | Append-only Markdown chat log with atomic write protection | M1 | Survey |
| 5 | Hub Protocol | Lead Review Queue | Central delivery submission queue in `inbox/lead_review.md` | M1 | Survey |
| 6 | Hub Protocol | Hub Polling & Status Protocol | Returns full hub state and live PID process liveness via `/api/status` | M1 | Survey |
| 7 | Process Exec | Cloned Executable Integration | Launches verified binaries (`Antigravity.exe`, `Worker1..3.exe`) with workspace | M1 | Survey |
| 8 | Process Exec | Double-Launch Window Activation | Staggered 1.5s launch triggers Electron `second-instance` window focus | M1 | Survey |
| 9 | Process Exec | Batch Launch ("Launch All") | Sequentially spawns all 4 agent instances with 0.4s delay | M1 | Survey |
| 10 | Process Exec | Profile & Session Isolation | Confirms independent `userData` in `AppData\Roaming` for 4 Google accounts | M1 | Survey |
| 11 | UI / Layout | VS Code Dark Theme Design | Full UI design system replicating VS Code/Antigravity dark theme (offline, no CDN) | M2 | Survey |
| 12 | UI / Layout | Activity Bar (Icon Rail) | 44px navigation bar with view switching and user avatar | M2 | Survey |
| 13 | UI / Layout | Editor Tab Bar & Breadcrumbs | VS Code styled tabs and breadcrumb navigation path | M2 | Survey |
| 14 | UI / Layout | Unified Chat Thread Rendering | Block-based parser preserving multi-line code blocks, tables, and agent avatars | M2 | Survey |
| 15 | UI / Layout | Auto-Scroll with Scroll Lock | Smart auto-scroll that detects manual scroll up and shows new messages pill | M2 | Survey |
| 16 | UI / Layout | Task Preset Templates | Quick-load templates for Full-Stack Web, Data & AI, Refactor & Test | M2 | Survey |
| 17 | UI / Layout | Account Launcher Cards | Interactive profile cards with real-time status dots (🟢 Running / ⚪ Stopped) | M2 | Survey |
| 18 | UI / Layout | Lead Command Helper | Click-to-copy button for terminal review command | M2 | Survey |
| 19 | UI / Layout | Desktop Status Bar | Persistent status bar with port, connection dot, workspace path, clock | M2 | Survey |
| 20 | Desktop Host | Native Window with Fallback | Desktop window via `pywebview` (WebView2) with auto-fallback to browser | M1 | Survey |
| 21 | Desktop Host | Native Folder Picker API | Bridges JS to Python native folder picker dialog | M1 | Survey |

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Backend Core & Process Launcher & Hub Protocol | Refactor `app.py`: pass workspace to executable on launch, add `psutil` process liveness tracking to `/api/status`, implement generic static asset router, add atomic append to `TEAM_CHAT.md`, add comprehensive error handling for missing binaries | none | PLANNED |
| M2 | Frontend UI/UX Antigravity Theme & Multi-Agent Chat | Refactor `public/index.html` (and bundled CSS): eliminate external CDN dependencies, implement robust multi-line chat block parser with code blocks & markdown formatting, wire live process status dots, add per-worker dispatch controls and Antigravity styling polish | M1 (contracts defined) | PLANNED |
| M3 | E2E Testing Suite Track | Design and build automated E2E test runner (`tests/`) covering Tiers 1-4 (>=5 tests per feature for Tiers 1-2, pairwise Tier 3, application scenarios Tier 4). Publish `TEST_READY.md`. | none (requirement-driven) | PLANNED |
| M4 | System Integration & E2E Test Suite Pass | Execute full automated E2E test suite against combined backend and frontend; achieve 100% pass rate across Tiers 1-4. | M1, M2, M3 | PLANNED |
| M5 | Adversarial Coverage Hardening (Tier 5) & Final Audit | White-box challenger review, edge case stress testing, forensic integrity audit, and final verification of acceptance criteria. | M4 | PLANNED |

## Interface Contracts

### 1. Backend REST API Contract (`app.py` ↔ Client)

#### `GET /api/status`
- **Description**: Returns complete hub state and process liveness.
- **Response Schema**:
```json
{
  "workspace": "C:\\path\\to\\project",
  "profiles": {
    "lead": { "name": "Team Lead", "exe": "...", "role": "..." },
    "worker1": { "name": "Worker 1", "exe": "...", "role": "..." },
    "worker2": { "name": "Worker 2", "exe": "...", "role": "..." },
    "worker3": { "name": "Worker 3", "exe": "...", "role": "..." }
  },
  "process_status": {
    "lead": { "running": true, "pid": 12345 },
    "worker1": { "running": false, "pid": null },
    "worker2": { "running": false, "pid": null },
    "worker3": { "running": false, "pid": null }
  },
  "board": "# 📋 Central Project Board...",
  "chat": "- [2026-09-28 23:50] [System]: Ready\n",
  "review": "# 🔍 Inbox สำหรับ Team Lead...",
  "worker1_inbox": "# 📥 ใบสั่งงาน...",
  "worker2_inbox": "# 📥 ใบสั่งงาน...",
  "worker3_inbox": "# 📥 ใบสั่งงาน..."
}
```

#### `POST /api/launch`
- **Request Body**: `{"target": "lead" | "worker1" | "worker2" | "worker3" | "all"}`
- **Response Schema**:
```json
{
  "success": true,
  "launched": ["lead"],
  "failed": [],
  "errors": {}
}
```
- **Error Behavior**: If an executable does not exist, `"success": false`, `"failed": ["worker1"]`, `"errors": {"worker1": "Executable not found at ..."}`.

#### `POST /api/dispatch`
- **Request Body**:
```json
{
  "title": "Mission Name",
  "overview": "Data contract and guidelines",
  "worker1": "Task for Worker 1 (optional)",
  "worker2": "Task for Worker 2 (optional)",
  "worker3": "Task for Worker 3 (optional)",
  "target_worker": "all" | "worker1" | "worker2" | "worker3"
}
```
- **Behavior**: Granular per-worker dispatch: if `target_worker` is specified, updates only that worker's inbox; if "all", updates all three. Always updates `BOARD.md` and appends dispatch announcement to `TEAM_CHAT.md`.

#### `POST /api/chat`
- **Request Body**: `{"sender": "Human Supervisor", "message": "Content..."}`
- **Behavior**: Atomically appends `- [YYYY-MM-DD HH:MM] [Sender]: Message\n` to `TEAM_CHAT.md`.
- **Validation**: Rejects empty message with HTTP 400 or `{"success": false, "error": "Empty message"}`.

### 2. Static Asset Routing Contract (`app.py`)
- Any request starting with `/public/` or matching a file in `public/` (e.g. `/style.css`, `/marked.min.js`, `/app.js`) is served with correct `Content-Type` header (text/html, text/css, application/javascript, image/svg+xml, font/woff2). Default fallback for `/` is `public/index.html`.

### 3. File System IPC Contract (`.agenthub/`)
- All files encoded in UTF-8 without BOM.
- `TEAM_CHAT.md`: Message delimiter regex: `/^\s*-\s*`?\[(\d{4}-\d{2}-\d{2}\s[\d:]+)\]\s*\[([^\]]+)\]`?[:\s]*(.*)/`.
- Blocks between message delimiters belong to the preceding message body, preserving newlines, code fences (````python ... ````), and Markdown tables.

## Code Layout
```
C:\Users\MBx13\.gemini\antigravity\scratch\agenthub-orchestrator/
├── app.py                      # Main backend server, process manager, static router, pywebview host
├── server.py                   # Legacy headless server (preserved for reference)
├── clone_agents.py             # ASAR patching & executable cloner utility
├── start_hub.bat               # Desktop launch batch script
├── config.json                 # Persistent workspace configuration
├── public/                     # Frontend web application assets
│   ├── index.html              # Antigravity Teamwork desktop shell & UI components
│   ├── style.css               # Self-contained VS Code dark theme styling (zero-CDN)
│   └── js/                     # Client logic (chat parser, status poller, dispatch)
├── tests/                      # Automated E2E & Contract Test Suite
│   ├── conftest.py             # Shared fixtures and test server lifecycle
│   ├── test_tier1_static.py    # Binary verification & syntax checks
│   ├── test_tier2_backend.py   # REST API contracts & process launch tests
│   ├── test_tier3_concurrency.py # Chat race conditions & edge case resilience
│   └── test_tier4_e2e.py       # Full workflow & UI integration tests
└── .agenthub/                  # Shared communication hub
    ├── BOARD.md                # Project board & responsibility matrix
    ├── TEAM_CHAT.md            # Multi-agent chat timeline
    └── inbox/
        ├── lead_review.md      # Deliverable submissions for Lead review
        ├── worker1.md          # Task queue for Worker 1
        ├── worker2.md          # Task queue for Worker 2
        └── worker3.md          # Task queue for Worker 3
```
