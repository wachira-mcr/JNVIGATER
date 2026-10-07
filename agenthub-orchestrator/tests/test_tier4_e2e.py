"""
Tier 4: End-to-End Real-World Application Scenarios Test Suite.
Simulates realistic multi-agent collaboration workflows:
  1. Full-Stack Web App Dispatch & Execution Workflow
  2. Worker Deliverable Submission & Lead Review Approval Cycle
  3. Urgent Hotfix Escalation & Granular Per-Worker Dispatch
  4. Swarm Multi-Agent Discussion Dialogue with Code Blocks & Tables
  5. Workspace Migration & Isolation Lifecycle
"""

import sys
import re
import tempfile
import shutil
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app
from tests.conftest import TestServerContext


class TestTier4E2EApplicationScenarios(unittest.TestCase):
    """Tier 4: End-to-end real-world multi-agent workflow simulations."""

    def setUp(self):
        self.ctx = TestServerContext(mock_subprocess=True)
        self.ctx.__enter__()
        self.client = self.ctx.client
        self.workspace = self.ctx.workspace_dir
        self.hub = self.workspace / ".agenthub"

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    # ── Scenario 1: Full-Stack Web App Dispatch & Swarm Execution ─────────────

    def test_scenario_1_fullstack_dispatch_and_execution_workflow(self):
        """
        Scenario 1: Full-Stack Web Application Lifecycle.
        Simulates Lead supervisor dispatching full-stack web orders to 3 workers,
        workers acknowledging via chat, and status hub synchronization.
        """
        # Step 1: Initial status verification
        status_0 = self.client.get_status().json()
        self.assertIn("[System]", status_0.get("chat", ""))

        # Step 2: Supervisor dispatches Full-Stack project
        mission_payload = {
            "title": "E-Commerce Cloud Portal v2",
            "overview": "Build a secure e-commerce application with REST API, Vue/Tailwind UI, and end-to-end tests.",
            "worker1": "Develop /api/catalog and /api/cart endpoints using SQLite and FastAPI.",
            "worker2": "Build product catalog view, cart sidebar, and checkout modal in Vue 3.",
            "worker3": "Implement pytest API regression suite and Playwright UI tests."
        }
        dispatch_res = self.client.post_dispatch(mission_payload)
        self.assertEqual(dispatch_res.status_code, 200)
        self.assertTrue(dispatch_res.json().get("success"))

        # Step 3: Validate BOARD.md was written with responsibility matrix
        board = (self.hub / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("E-Commerce Cloud Portal v2", board)
        self.assertIn("FastAPI", board)
        self.assertIn("Worker 1", board)
        self.assertIn("Worker 2", board)
        self.assertIn("Worker 3", board)

        # Step 4: Validate individualized worker inboxes
        inbox = self.hub / "inbox"
        w1_task = (inbox / "worker1.md").read_text(encoding="utf-8")
        w2_task = (inbox / "worker2.md").read_text(encoding="utf-8")
        w3_task = (inbox / "worker3.md").read_text(encoding="utf-8")

        self.assertIn("/api/catalog", w1_task)
        self.assertIn("Vue 3", w2_task)
        self.assertIn("Playwright", w3_task)

        # Step 5: Workers acknowledge task receipt via live chat
        self.client.post_chat("Worker 1", "Acknowledged. Setting up SQLite schema and FastAPI routes.")
        self.client.post_chat("Worker 2", "Acknowledged. Scaffolding Vue 3 storefront components.")
        self.client.post_chat("Worker 3", "Acknowledged. Creating test fixtures and mock catalog data.")

        # Step 6: Verify live chat has all acknowledgements and dispatcher announcement
        chat = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn("E-Commerce Cloud Portal v2", chat)
        self.assertIn("Setting up SQLite schema", chat)
        self.assertIn("Scaffolding Vue 3", chat)
        self.assertIn("Creating test fixtures", chat)

        # Step 7: Verify status endpoint returns the fully synchronized hub state
        status_final = self.client.get_status().json()
        self.assertIn("E-Commerce Cloud Portal v2", status_final["board"])
        self.assertIn("/api/catalog", status_final["worker1_inbox"])
        self.assertIn("Vue 3", status_final["worker2_inbox"])
        self.assertIn("Playwright", status_final["worker3_inbox"])

    # ── Scenario 2: Worker Deliverable Submission & Lead Review Approval ──────

    def test_scenario_2_worker_deliverable_submission_and_lead_review(self):
        """
        Scenario 2: Worker Deliverable Submission and Lead Review Cycle.
        Simulates workers completing deliverables, submitting summaries to lead_review.md,
        Lead querying the review inbox, and publishing approval to team chat.
        """
        review_file = self.hub / "inbox" / "lead_review.md"

        # Step 1: Worker 1 finishes backend and submits PR summary
        w1_deliverable = (
            "## [Worker 1 Submission] Authentication & DB Module\n"
            "- Completed: JWT auth, password hashing, and user migration.\n"
            "- Tests: 18 passed in 0.4s.\n"
            "- Deliverable branch: `feat/worker1-auth`\n\n"
        )
        review_file.write_text(w1_deliverable, encoding="utf-8")
        self.client.post_chat("Worker 1", "Delivered Authentication module. Submitted summary to lead_review.md.")

        # Step 2: Worker 2 finishes UI and appends submission
        w2_deliverable = (
            "## [Worker 2 Submission] User Profile & Settings Page\n"
            "- Completed: Dark-mode profile view and avatar uploader.\n"
            "- Deliverable branch: `feat/worker2-ui`\n\n"
        )
        existing_review = review_file.read_text(encoding="utf-8")
        review_file.write_text(existing_review + w2_deliverable, encoding="utf-8")
        self.client.post_chat("Worker 2", "Delivered User Profile page. Submitted summary to lead_review.md.")

        # Step 3: Lead polls /api/status to inspect review queue
        status = self.client.get_status().json()
        review_content = status.get("review", "")
        self.assertIn("Worker 1 Submission", review_content)
        self.assertIn("Worker 2 Submission", review_content)
        self.assertIn("feat/worker1-auth", review_content)
        self.assertIn("feat/worker2-ui", review_content)

        # Step 4: Lead reviews code and posts official sign-off in TEAM_CHAT.md
        self.client.post_chat("Team Lead", "Review complete: Auth and Profile modules meet quality standards. Approved for merge!")

        # Step 5: Verify full audit trail in TEAM_CHAT.md
        chat = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn("Delivered Authentication module", chat)
        self.assertIn("Delivered User Profile page", chat)
        self.assertIn("Approved for merge!", chat)

    # ── Scenario 3: Urgent Bugfix Escalation & Targeted Dispatch ──────────────

    def test_scenario_3_urgent_bugfix_and_granular_per_worker_dispatch(self):
        """
        Scenario 3: Urgent Incident Escalation and Targeted Task Dispatch.
        Simulates an incident reported in chat, supervisor dispatching hotfix,
        and ensuring target worker receives the order while team coordinates.
        """
        # Step 1: Initial baseline dispatch
        self.client.post_dispatch({
            "title": "Sprint 42 Regular Iteration",
            "overview": "Normal feature sprint",
            "worker1": "Routine backend optimization",
            "worker2": "Routine CSS cleanup",
            "worker3": "Routine test maintenance"
        })

        # Step 2: Supervisor detects production issue and escalates via chat
        self.client.post_chat("Supervisor", "ALERT: Production memory leak detected in database connection pool!")

        # Step 3: Targeted emergency dispatch to Worker 1
        hotfix_payload = {
            "title": "EMERGENCY HOTFIX: DB Connection Pool Leak",
            "overview": "Immediate fix required: close lingering connections and cap pool size.",
            "worker1": "CRITICAL: Audit connection checkout in db_pool.py and add timeout context manager.",
            "worker2": "Stand by: assist with dashboard metrics once patched.",
            "worker3": "Prepare load testing script to verify pool exhaustion under 500 req/s."
        }
        self.client.post_dispatch(hotfix_payload)

        # Step 4: Verify Worker 1 received the critical directive
        w1_inbox = (self.hub / "inbox" / "worker1.md").read_text(encoding="utf-8")
        self.assertIn("EMERGENCY HOTFIX", w1_inbox)
        self.assertIn("Audit connection checkout", w1_inbox)

        # Step 5: Worker 1 commits patch and announces fix
        self.client.post_chat("Worker 1", "HOTFIX deployed: Connection leak resolved and pool max capped at 20.")
        self.client.post_chat("Worker 3", "Load test complete: 0 connection timeouts under 500 req/s. Verified.")

        # Step 6: Verify complete sequence in chat
        chat = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn("ALERT: Production memory leak", chat)
        self.assertIn("HOTFIX deployed", chat)
        self.assertIn("Load test complete", chat)

    # ── Scenario 4: Swarm Multi-Agent Dialogue with Code Fences & Tables ──────

    def test_scenario_4_swarm_chat_dialogue_with_code_fences_and_tables(self):
        """
        Scenario 4: Swarm Multi-Agent Discussion with Complex Formatting.
        Verifies messages with multiline code fences (```python ... ```),
        Markdown tables, and JSON schemas persist cleanly and parse via delimiter regex.
        """
        # Delimiter pattern specified in PROJECT.md:
        # /^\s*-\s*`?\[(\d{4}-\d{2}-\d{2}\s[\d:]+)\]\s*\[([^\]]+)\]`?[:\s]*(.*)/
        delimiter_pattern = re.compile(
            r"^\s*-\s*`?\[(\d{4}-\d{2}-\d{2}\s[\d:]+)\]\s*\[([^\]]+)\]`?[:\s]*(.*)",
            re.MULTILINE
        )

        # Message 1: Python code snippet
        python_snippet = (
            "Here is the proposed caching decorator:\n"
            "```python\n"
            "def cache_result(ttl=60):\n"
            "    def decorator(fn):\n"
            "        return fn\n"
            "    return decorator\n"
            "```"
        )
        self.client.post_chat("Worker 1", python_snippet)

        # Message 2: Markdown table
        table_snippet = (
            "Performance Benchmark Results:\n"
            "| Endpoint | Before | After | Improvement |\n"
            "|:---|:---|:---|:---|\n"
            "| /api/users | 240ms | 18ms | 13.3x |\n"
            "| /api/stats | 890ms | 45ms | 19.7x |"
        )
        self.client.post_chat("Worker 3", table_snippet)

        # Message 3: Supervisor approval
        self.client.post_chat("Supervisor", "LGTM! Outstanding performance gains.")

        # Read back raw chat file
        chat_raw = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")

        # Verify snippets exist verbatim
        self.assertIn("def cache_result(ttl=60):", chat_raw)
        self.assertIn("| /api/users | 240ms | 18ms | 13.3x |", chat_raw)
        self.assertIn("LGTM! Outstanding performance gains.", chat_raw)

        # Match delimiters
        matches = delimiter_pattern.findall(chat_raw)
        self.assertGreaterEqual(len(matches), 3)

        senders = [m[1] for m in matches]
        self.assertIn("Worker 1", senders)
        self.assertIn("Worker 3", senders)
        self.assertIn("Supervisor", senders)

    # ── Scenario 5: Workspace Switching & Isolation Lifecycle ─────────────────

    def test_scenario_5_workspace_switching_and_isolation_lifecycle(self):
        """
        Scenario 5: Workspace Switching and Project Isolation.
        Verifies switching between Project A and Project B keeps .agenthub instances
        strictly isolated without cross-contamination.
        """
        # Project A setup (initial workspace)
        self.client.post_dispatch({
            "title": "Project Alpha",
            "overview": "Scope of Alpha project",
            "worker1": "Alpha task 1",
            "worker2": "Alpha task 2",
            "worker3": "Alpha task 3"
        })
        self.client.post_chat("Worker 1", "Alpha work in progress.")

        # Create Project B directory
        project_b_dir = Path(tempfile.mkdtemp(prefix="project_beta_"))
        try:
            # Switch to Project B
            switch_res = self.client.post_set_workspace(str(project_b_dir))
            self.assertEqual(switch_res.status_code, 200)
            self.assertTrue(switch_res.json().get("success"))

            # Dispatch Project Beta
            self.client.post_dispatch({
                "title": "Project Beta",
                "overview": "Scope of Beta project",
                "worker1": "Beta task 1",
                "worker2": "Beta task 2",
                "worker3": "Beta task 3"
            })
            self.client.post_chat("Worker 1", "Beta work in progress.")

            # Validate Project B hub state
            status_b = self.client.get_status().json()
            self.assertIn("Project Beta", status_b["board"])
            self.assertIn("Beta task 1", status_b["worker1_inbox"])
            self.assertIn("Beta work in progress", status_b["chat"])
            self.assertNotIn("Project Alpha", status_b["board"])

            # Validate Project A files on disk were NOT contaminated by Project B
            alpha_board = (self.hub / "BOARD.md").read_text(encoding="utf-8")
            alpha_chat = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
            self.assertIn("Project Alpha", alpha_board)
            self.assertNotIn("Project Beta", alpha_board)
            self.assertIn("Alpha work in progress", alpha_chat)
            self.assertNotIn("Beta work in progress", alpha_chat)

        finally:
            shutil.rmtree(project_b_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
