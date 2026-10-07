# Test Infrastructure Specification — Antigravity Teamwork Orchestrator

## 1. Philosophy & Testing Principles

The Antigravity Teamwork Orchestrator test suite adheres to the following core tenets:

1. **Opaque-Box Requirement-Driven Verification**:
   - Tests are derived strictly from user requirements (`ORIGINAL_REQUEST.md`) and architectural contracts (`PROJECT.md`).
   - Testing validates externally observable behavior: HTTP REST endpoints (`/api/status`, `/api/chat`, `/api/dispatch`, `/api/launch`, `/api/set_workspace`), static asset delivery, and file system IPC (`.agenthub/` structure, file formatting, delimiter parsing, and concurrency safety).
   - No assumptions are made about internal implementation quirks beyond the documented interface contracts.

2. **Authoritative Expected Output Derivation**:
   - Every assertion derives its expected values from documented contracts:
     - `TEAM_CHAT.md` delimiter format: `- [YYYY-MM-DD HH:MM] [Sender]: Message\n`
     - Status schema: JSON containing `workspace`, `profiles`, `board`, `chat`, `review`, `worker1_inbox`, `worker2_inbox`, `worker3_inbox`, and `process_status`.
     - Dispatch template: Markdown responsibility matrix with Lead, Worker 1, Worker 2, Worker 3, and individual inbox assignments.
     - Process executables: Verified binary paths in `C:\Users\MBx13\AppData\Local\Programs` and `AppData\Roaming` profile roots.

3. **Progressive Testability & Defect Escalation**:
   - Tests distinguish between current implementation status and contractual requirements.
   - Implementation bugs discovered during test execution are recorded, categorized, and escalated to implementing agents rather than silently masked or worked around in test assertions.

4. **Environment Isolation & Non-Destructive Execution**:
   - Automated tests **never mutate** the live development repository's `.agenthub/` or `config.json`.
   - Test server fixtures dynamically allocate ephemeral TCP ports (`port 0`) and spin up isolated temporary workspaces (`tempfile.TemporaryDirectory`).
   - Process launches during CI/automated test runs utilize mocked process execution or verify dry-run binary integrity to prevent launching four heavy Electron instances repeatedly on the host machine.
   - Full tear-down cleans up all temporary sockets, files, and threads.

---

## 2. 4-Tier Testing Methodology

The test suite is organized into four complementary verification tiers:

```
+-------------------------------------------------------------------------------+
|                      Tier 4: Real-World Application Scenarios                 |
|  - Full-Stack Web App Dispatch Workflow                                       |
|  - Worker Deliverable Submission & Lead Review Approval Cycle                |
|  - Urgent Hotfix Escalation & Granular Per-Worker Dispatch                    |
|  - Multi-Agent Swarm Chat Dialogue with Code Fences & Markdown Tables          |
|  - Workspace Migration & Isolation Lifecycle                                 |
+---------------------------------------+---------------------------------------+
                                        |
+---------------------------------------v---------------------------------------+
|                 Tier 3: Cross-Feature Interactions & Concurrency              |
|  - Multi-threaded concurrent chat appends (race condition validation)         |
|  - Simultaneous status polling under heavy file write load                    |
|  - Concurrent dispatch requests & board integrity                             |
|  - Workspace switching during active polling                                  |
|  - Malformed payloads, oversized inputs, and edge error cascading            |
+---------------------------------------+---------------------------------------+
                                        |
+---------------------------------------v---------------------------------------+
|                    Tier 2: Backend REST & Hub Contracts                       |
|  - GET /api/status response schema and process liveness contract              |
|  - POST /api/chat valid append, sender fallback, empty message rejection      |
|  - POST /api/dispatch board generation, granular targeting, inbox separation   |
|  - POST /api/set_workspace valid switch and invalid path rejection           |
|  - POST /api/launch individual, all, and error handling for missing binaries  |
|  - Generic static asset router MIME types and fallback                        |
+---------------------------------------+---------------------------------------+
                                        |
+---------------------------------------v---------------------------------------+
|                   Tier 1: Static Verification & Feature Coverage              |
|  - Python compile, AST syntax checks (app.py, server.py, clone_agents.py)      |
|  - Physical executable path verification for all 4 agent binaries             |
|  - Independent AppData\Roaming profile directory isolation                    |
|  - Template auto-initialization files (BOARD.md, TEAM_CHAT.md, inboxes)       |
|  - UI HTML/CSS static assets inspection (Activity Bar, Chat, Presets, Cards)  |
+-------------------------------------------------------------------------------+
```

### Tier 1: Static Verification & Feature Coverage
- Focus: Binary presence, file system structure, syntax validity, AST integrity, profile isolation, and UI layout components.
- Criteria: >= 5 tests per feature domain. Ensures the environment and foundational prerequisites are satisfied.

### Tier 2: Boundary & Corner Cases (Backend REST & Contracts)
- Focus: API contract schema validation, error codes, edge cases (empty strings, whitespace, Unicode/Thai characters, large texts, non-existent directories, unknown targets).
- Criteria: >= 5 tests per feature domain. Asserts precise contract adherence and robust error handling.

### Tier 3: Cross-Feature Interactions & Concurrency
- Focus: Pairwise interactions between system components under concurrent loads, thread safety of file operations on `TEAM_CHAT.md` and `BOARD.md`, race conditions, and error cascading.
- Criteria: Thread-safety stress testing (10-20 concurrent threads) without data corruption or unhandled server exceptions.

### Tier 4: Real-World Application Scenarios
- Focus: Complete end-to-end multi-agent orchestration flows from dispatch to review.
- Criteria: >= 5 end-to-end scenarios executing complex user journeys end-to-end.

---

## 3. 21-Feature Traceability Matrix

Every feature defined in `PROJECT.md § Feature Inventory` is mapped to its corresponding automated tests:

| Feature # | Category | Feature Name | Tier 1 Tests | Tier 2 Tests | Tier 3 Tests | Tier 4 Tests |
|---|---|---|---|---|---|---|
| **F01** | Hub Protocol | Workspace Initialization | `test_f01_workspace_init_creates_files`, `test_f01_inbox_directory_created`, `test_f01_board_template_structure` | `test_f01_set_workspace_initializes_hub`, `test_f01_invalid_workspace_rejected`, `test_f01_idempotent_init` | `test_f01_concurrent_workspace_init` | `test_scenario_5_workspace_migration` |
| **F02** | Hub Protocol | Central Board Management | `test_f02_board_markdown_structure`, `test_f02_board_table_headers` | `test_f02_dispatch_updates_board`, `test_f02_board_with_unicode_thai`, `test_f02_board_empty_fields_fallback` | `test_f02_concurrent_board_dispatches` | `test_scenario_1_fullstack_dispatch`, `test_scenario_3_urgent_bugfix` |
| **F03** | Hub Protocol | Worker Task Inboxes | `test_f03_worker_inbox_files_exist`, `test_f03_worker_roles_configured` | `test_f03_granular_dispatch_worker1`, `test_f03_granular_dispatch_all`, `test_f03_inbox_headers_and_dates` | `test_f03_concurrent_inbox_writes` | `test_scenario_1_fullstack_dispatch`, `test_scenario_3_urgent_bugfix` |
| **F04** | Hub Protocol | Live Swarm Chat Stream | `test_f04_chat_file_exists`, `test_f04_chat_initial_content` | `test_f04_chat_append_format`, `test_f04_chat_empty_rejected`, `test_f04_chat_sender_defaults`, `test_f04_chat_unicode` | `test_f04_concurrent_chat_appends_lossless`, `test_f04_concurrent_chat_poll_race` | `test_scenario_4_swarm_chat_dialogue`, `test_scenario_1_fullstack_dispatch` |
| **F05** | Hub Protocol | Lead Review Queue | `test_f05_lead_review_file_exists`, `test_f05_lead_review_default_content` | `test_f05_status_exposes_lead_review`, `test_f05_lead_review_manual_submission` | `test_f05_concurrent_review_submissions` | `test_scenario_2_review_queue_approval` |
| **F06** | Hub Protocol | Hub Polling & Status Protocol | `test_f06_status_schema_keys`, `test_f06_profiles_metadata` | `test_f06_status_reflects_updated_files`, `test_f06_status_process_liveness_contract`, `test_f06_status_content_type` | `test_f06_rapid_polling_under_load` | `test_scenario_1_fullstack_dispatch`, `test_scenario_5_workspace_migration` |
| **F07** | Process Exec | Cloned Executable Integration | `test_f07_antigravity_binaries_exist`, `test_f07_binary_paths_in_profiles`, `test_f07_cloner_script_compiles` | `test_f07_launch_valid_target`, `test_f07_launch_missing_binary_contract`, `test_f07_launch_workspace_arg_contract` | `test_f07_launch_during_heavy_polling` | `test_scenario_1_fullstack_dispatch` |
| **F08** | Process Exec | Double-Launch Window Activation | `test_f08_double_launch_timing_logic` | `test_f08_launch_staggered_calls_mocked` | `test_f08_rapid_relaunch_race` | `test_scenario_1_fullstack_dispatch` |
| **F09** | Process Exec | Batch Launch ("Launch All") | `test_f09_launch_all_profile_targets` | `test_f09_launch_all_returns_all_targets`, `test_f09_launch_all_stagger_delay` | `test_f09_concurrent_launch_all_requests` | `test_scenario_1_fullstack_dispatch` |
| **F10** | Process Exec | Profile & Session Isolation | `test_f10_roaming_profile_dirs_exist`, `test_f10_profile_names_distinct` | `test_f10_profile_configs_isolated` | `test_f10_profile_access_isolation` | `test_scenario_1_fullstack_dispatch` |
| **F11** | UI / Layout | VS Code Dark Theme Design | `test_f11_html_contains_dark_vars`, `test_f11_zero_cdn_requirement` | `test_f11_static_router_serves_css` | `test_f11_asset_serving_concurrency` | `test_scenario_1_fullstack_dispatch` |
| **F12** | UI / Layout | Activity Bar (Icon Rail) | `test_f12_html_activity_bar_elements`, `test_f12_activity_bar_dimensions` | `test_f12_activity_icon_classes` | `test_f12_ui_dom_integrity` | `test_scenario_4_swarm_chat_dialogue` |
| **F13** | UI / Layout | Editor Tab Bar & Breadcrumbs | `test_f13_html_tab_bar_structure`, `test_f13_breadcrumbs_elements` | `test_f13_tab_switching_attributes` | `test_f13_ui_dom_integrity` | `test_scenario_1_fullstack_dispatch` |
| **F14** | UI / Layout | Unified Chat Thread Rendering | `test_f14_html_chat_container_exists`, `test_f14_chat_delimiter_regex` | `test_f14_chat_parser_multiline_blocks`, `test_f14_chat_code_fence_preservation` | `test_f14_chat_parser_heavy_history` | `test_scenario_4_swarm_chat_dialogue` |
| **F15** | UI / Layout | Auto-Scroll with Scroll Lock | `test_f15_html_scroll_pill_elements` | `test_f15_autoscroll_script_attributes` | `test_f15_ui_dom_integrity` | `test_scenario_4_swarm_chat_dialogue` |
| **F16** | UI / Layout | Task Preset Templates | `test_f16_html_task_presets_present` | `test_f16_task_presets_content_values` | `test_f16_ui_dom_integrity` | `test_scenario_1_fullstack_dispatch` |
| **F17** | UI / Layout | Account Launcher Cards | `test_f17_html_account_cards_all_agents`, `test_f17_status_dot_elements` | `test_f17_account_card_role_metadata` | `test_f17_ui_dom_integrity` | `test_scenario_1_fullstack_dispatch` |
| **F18** | UI / Layout | Lead Command Helper | `test_f18_html_command_helper_exists` | `test_f18_command_helper_copy_payload` | `test_f18_ui_dom_integrity` | `test_scenario_2_review_queue_approval` |
| **F19** | UI / Layout | Desktop Status Bar | `test_f19_html_status_bar_elements`, `test_f19_status_bar_port_and_clock` | `test_f19_status_bar_connection_dot` | `test_f19_ui_dom_integrity` | `test_scenario_5_workspace_migration` |
| **F20** | Desktop Host | Native Window with Fallback | `test_f20_webview_import_fallback_logic`, `test_f20_app_py_browser_fallback` | `test_f20_window_config_parameters` | `test_f20_server_thread_resilience` | `test_scenario_5_workspace_migration` |
| **F21** | Desktop Host | Native Folder Picker API | `test_f21_js_api_class_structure` | `test_f21_js_api_pick_folder_mock` | `test_f21_folder_picker_thread_safety` | `test_scenario_5_workspace_migration` |

---

## 4. Test Suite File Structure

```
tests/
├── __init__.py                # Package marker
├── conftest.py                # Pytest fixtures, test server lifecycle, temporary workspace manager
├── test_tier1_static.py       # Tier 1: Static verification, binary paths, AST compile, UI DOM check
├── test_tier2_backend.py      # Tier 2: Backend REST contracts, input validation, status schema
├── test_tier3_concurrency.py  # Tier 3: Multithreaded chat appends, polling race conditions, stress
└── test_tier4_e2e.py          # Tier 4: 5 Real-world application scenarios end-to-end
```

### Shared Fixtures (`conftest.py`)
- `isolated_workspace`: Creates a fresh, isolated directory with `.agenthub` initialized, cleaned up post-test.
- `temp_config`: Sandboxes `app.CONFIG_FILE` to avoid modifying production `config.json`.
- `mock_process_launcher`: Mock for `subprocess.Popen` preventing accidental desktop window spawning during CI.
- `test_server`: Runs `RobustHTTPServer` on `127.0.0.1:0` (ephemeral port) with `HubHandler` in a daemon thread, yielding `(base_url, workspace_path)`. Shuts down cleanly on teardown.
- `api_client`: Helper client executing typed HTTP GET and POST requests against the test server.

---

## 5. Test Execution Commands

```powershell
# Run entire test suite with verbose output
python -m pytest tests/ -v

# Run individual tiers
python -m pytest tests/test_tier1_static.py -v
python -m pytest tests/test_tier2_backend.py -v
python -m pytest tests/test_tier3_concurrency.py -v
python -m pytest tests/test_tier4_e2e.py -v

# Run via standard library unittest runner (zero external dependencies)
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## 6. Implementation Defect Escalation Protocol

When tests fail due to implementation gaps against `PROJECT.md` contracts:
1. **Never mutate implementation files** (`app.py`, `public/index.html`, etc.) within the Test Writer role.
2. Assert the explicit contract requirement.
3. Classify defects as:
   - **BLOCKER**: Prevents basic operation (e.g. server crash, syntax error, port collision).
   - **CONTRACT_GAP**: Discrepancy between API response and schema defined in `PROJECT.md` (e.g. missing `process_status` in `/api/status`, missing static asset routing).
   - **EDGE_CASE**: Failure under high concurrency or invalid input (e.g. unhandled 500 on malformed JSON).
4. Document the exact failure, reproducing test case, and line number in `handoff.md` and `TEST_READY.md`.
