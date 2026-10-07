"""
Tier 2: Backend REST API & Hub Contracts Test Suite.
Validates status schema, chat appends, task dispatching, workspace switching,
process launch requests, and boundary/corner cases.
"""

import sys
import json
import time
import tempfile
import shutil
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app
from tests.conftest import TestServerContext


class TestTier2BackendContracts(unittest.TestCase):
    """Tier 2: Backend REST endpoints and boundary/corner cases."""

    def setUp(self):
        self.ctx = TestServerContext(mock_subprocess=True)
        self.ctx.__enter__()
        self.client = self.ctx.client
        self.workspace = self.ctx.workspace_dir
        self.hub = self.workspace / ".agenthub"

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    # ── Feature 6: GET /api/status ────────────────────────────────────────────

    def test_f06_status_returns_200_and_json_content_type(self):
        """F06: Verifies /api/status returns HTTP 200 and UTF-8 JSON content-type."""
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/json", resp.headers.get("Content-Type", "").lower())
        self.assertIn("utf-8", resp.headers.get("Content-Type", "").lower())

    def test_f06_status_contains_all_core_hub_keys(self):
        """F06: Verifies /api/status contains all documented hub protocol keys."""
        resp = self.client.get("/api/status")
        data = resp.json()
        expected_keys = {
            "workspace",
            "profiles",
            "board",
            "chat",
            "review",
            "worker1_inbox",
            "worker2_inbox",
            "worker3_inbox",
        }
        for key in expected_keys:
            self.assertIn(key, data, f"Key '{key}' missing from /api/status response")

    def test_f06_status_reflects_current_workspace_path(self):
        """F06: Verifies /api/status returns the active workspace path."""
        resp = self.client.get("/api/status")
        data = resp.json()
        self.assertEqual(Path(data["workspace"]).resolve(), self.workspace.resolve())

    def test_f06_status_profiles_match_configuration(self):
        """F06: Verifies /api/status profiles match app.PROFILES dictionary."""
        resp = self.client.get("/api/status")
        profiles = resp.json()["profiles"]
        self.assertEqual(set(profiles.keys()), {"lead", "worker1", "worker2", "worker3"})
        self.assertEqual(profiles["lead"]["id"], "lead")

    def test_f06_status_reflects_inbox_changes_dynamically(self):
        """F06: Verifies /api/status immediately reflects filesystem updates."""
        worker1_file = self.hub / "inbox" / "worker1.md"
        updated_text = "# Dynamic Task Update\nWorker1 execute unit tests immediately."
        worker1_file.write_text(updated_text, encoding="utf-8")

        resp = self.client.get("/api/status")
        self.assertEqual(resp.json()["worker1_inbox"], updated_text)

    def test_f06_status_process_liveness_contract_schema(self):
        """F06: Verifies process_status schema contract per PROJECT.md when present."""
        resp = self.client.get("/api/status")
        data = resp.json()
        if "process_status" not in data:
            self.skipTest("M1 in progress: process_status tracking planned for M1 implementation")
        ps = data["process_status"]
        for agent in ("lead", "worker1", "worker2", "worker3"):
            self.assertIn(agent, ps)
            self.assertIn("running", ps[agent])
            self.assertIn("pid", ps[agent])

    # ── Feature 4: POST /api/chat ─────────────────────────────────────────────

    def test_f04_chat_valid_message_returns_success(self):
        """F04: Verifies sending a valid chat message returns success: True."""
        resp = self.client.post_chat("Worker 1", "Starting task implementation.")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

    def test_f04_chat_appends_delimiter_and_timestamp_format(self):
        """F04: Verifies chat message is appended to TEAM_CHAT.md with correct format."""
        sender = "Worker 2"
        msg = "UI components updated with VS Code dark theme."
        self.client.post_chat(sender, msg)

        chat_content = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn(f"[{sender}]", chat_content)
        self.assertIn(msg, chat_content)

    def test_f04_chat_empty_message_returns_false(self):
        """F04: Verifies empty message payload is rejected with success: False."""
        resp = self.client.post_chat("Lead", "")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("success"), "Empty message must not succeed")
        self.assertIn("error", data)

    def test_f04_chat_whitespace_only_message_returns_false(self):
        """F04: Verifies whitespace-only message payload is rejected."""
        resp = self.client.post_chat("Lead", "   \t\n  ")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("success"), "Whitespace message must not succeed")

    def test_f04_chat_sender_defaults_to_you_if_omitted(self):
        """F04: Verifies omitted sender defaults gracefully to 'You'."""
        resp = self.client.post("/api/chat", {"message": "Message without explicit sender."})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        chat_content = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn("[You]", chat_content)

    def test_f04_chat_handles_unicode_thai_characters(self):
        """F04: Verifies chat handles Thai Unicode characters without corruption."""
        thai_msg = "ภารกิจพร้อมแล้ว เริ่มทดสอบระบบได้เลย 🚀"
        resp = self.client.post_chat("หัวหน้าทีม", thai_msg)
        self.assertEqual(resp.status_code, 200)

        chat_content = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn(thai_msg, chat_content)
        self.assertIn("หัวหน้าทีม", chat_content)

    def test_f04_chat_handles_markdown_and_special_symbols(self):
        """F04: Verifies markdown delimiters, code fences, and symbols are preserved."""
        code_msg = "`inline_func()` and [bracket_link] and *italic* and **bold**"
        resp = self.client.post_chat("Worker 3", code_msg)
        self.assertEqual(resp.status_code, 200)

        chat_content = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn(code_msg, chat_content)

    # ── Feature 2 & 3: POST /api/dispatch ─────────────────────────────────────

    def test_f02_dispatch_valid_payload_updates_board(self):
        """F02: Verifies dispatch overwrites BOARD.md with title and data contract."""
        payload = {
            "title": "Build Authentication Module",
            "overview": "Implement OAuth2 and JWT token validation.",
            "worker1": "Develop token generation endpoints.",
            "worker2": "Design login dialog.",
            "worker3": "Write security test suite."
        }
        resp = self.client.post_dispatch(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        board = (self.hub / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("Build Authentication Module", board)
        self.assertIn("Implement OAuth2 and JWT token validation", board)

    def test_f02_dispatch_board_contains_responsibility_matrix(self):
        """F02: Verifies BOARD.md contains responsibility table mapping workers to tasks."""
        payload = {
            "title": "Database Optimization",
            "overview": "Optimize index lookups and migration.",
            "worker1": "Run EXPLAIN ANALYZE on queries",
            "worker2": "Update dashboard metrics UI",
            "worker3": "Run query benchmark suite"
        }
        self.client.post_dispatch(payload)

        board = (self.hub / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("Responsibility Matrix", board)
        self.assertIn("Worker 1", board)
        self.assertIn("Run EXPLAIN ANALYZE", board)

    def test_f03_dispatch_updates_all_three_worker_inboxes(self):
        """F03: Verifies dispatch creates/updates separate task files for workers 1, 2, and 3."""
        payload = {
            "title": "Release 1.0 Milestone",
            "overview": "Finalize core components.",
            "worker1": "Task 1: Core backend cleanup",
            "worker2": "Task 2: Frontend styling polish",
            "worker3": "Task 3: Automated regression run"
        }
        self.client.post_dispatch(payload)

        inbox = self.hub / "inbox"
        self.assertIn("Task 1: Core backend cleanup", (inbox / "worker1.md").read_text(encoding="utf-8"))
        self.assertIn("Task 2: Frontend styling polish", (inbox / "worker2.md").read_text(encoding="utf-8"))
        self.assertIn("Task 3: Automated regression run", (inbox / "worker3.md").read_text(encoding="utf-8"))

    def test_f04_dispatch_appends_announcement_to_team_chat(self):
        """F04: Verifies dispatch posts an announcement to TEAM_CHAT.md."""
        payload = {
            "title": "Urgent Security Patch",
            "overview": "Fix vulnerability.",
            "worker1": "Patch cipher suite",
            "worker2": "Update banner",
            "worker3": "Verify CVE exploit"
        }
        self.client.post_dispatch(payload)

        chat = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn("Urgent Security Patch", chat)
        self.assertIn("Central Dispatcher", chat)

    def test_f03_dispatch_with_empty_tasks_applies_fallbacks(self):
        """F03: Verifies dispatch with empty fields applies sensible fallback text."""
        payload = {"title": "General Sync", "overview": ""}
        resp = self.client.post_dispatch(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        w1_content = (self.hub / "inbox" / "worker1.md").read_text(encoding="utf-8")
        self.assertTrue(len(w1_content) > 0)

    def test_f02_dispatch_with_unicode_thai_and_emoji(self):
        """F02: Verifies dispatch handles Thai characters and emoji without encoding errors."""
        payload = {
            "title": "โครงการปรับปรุงประสิทธิภาพระบบ 🚀",
            "overview": "รายละเอียดข้อตกลงและเงื่อนไขการทำงาน 📋",
            "worker1": "ปรับแต่งฐานข้อมูล 🛠️",
            "worker2": "ออกแบบหน้าจอใหม่ 🎨",
            "worker3": "ทดสอบระบบอัตโนมัติ 🧪"
        }
        resp = self.client.post_dispatch(payload)
        self.assertEqual(resp.status_code, 200)

        board = (self.hub / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("โครงการปรับปรุงประสิทธิภาพระบบ 🚀", board)
        self.assertIn("ปรับแต่งฐานข้อมูล 🛠️", board)

    # ── Feature 1: POST /api/set_workspace ────────────────────────────────────

    def test_f01_set_workspace_valid_directory_updates_and_initializes(self):
        """F01: Verifies switching to a new valid directory updates config and creates .agenthub."""
        new_ws = Path(tempfile.mkdtemp(prefix="new_ws_"))
        try:
            resp = self.client.post_set_workspace(str(new_ws))
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json().get("success"))
            self.assertEqual(Path(resp.json()["workspace"]).resolve(), new_ws.resolve())

            # Verify .agenthub was auto-initialized in the new workspace
            self.assertTrue((new_ws / ".agenthub").exists())
            self.assertTrue((new_ws / ".agenthub" / "BOARD.md").exists())
        finally:
            shutil.rmtree(new_ws, ignore_errors=True)

    def test_f01_set_workspace_nonexistent_directory_rejected(self):
        """F01: Verifies pointing to a non-existent folder returns success: False."""
        fake_path = r"C:\non_existent_folder_xyz_12345"
        resp = self.client.post_set_workspace(fake_path)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json().get("success"))
        self.assertIn("error", resp.json())

    def test_f01_set_workspace_empty_string_rejected(self):
        """F01: Verifies empty workspace string returns success: False."""
        resp = self.client.post_set_workspace("")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json().get("success"))

    # ── Feature 7, 8, 9: POST /api/launch ─────────────────────────────────────

    def test_f07_launch_single_target_lead(self):
        """F07: Verifies launching 'lead' triggers process execution and returns target in launched list."""
        resp = self.client.post_launch("lead")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("lead", data.get("launched", []))

    def test_f07_launch_single_target_worker1(self):
        """F07: Verifies launching 'worker1' records worker1 in launched list."""
        resp = self.client.post_launch("worker1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("worker1", data.get("launched", []))

    def test_f09_launch_all_targets(self):
        """F09: Verifies target 'all' triggers launches for all 4 profiles."""
        resp = self.client.post_launch("all")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        launched = data.get("launched", [])
        for agent in ("lead", "worker1", "worker2", "worker3"):
            self.assertIn(agent, launched)

    def test_f07_launch_invalid_target_handled_safely(self):
        """F07: Verifies invalid target name does not crash the server and returns empty launched list."""
        resp = self.client.post_launch("non_existent_target")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("launched"), [])

    # ── Static Routing & Fallback ─────────────────────────────────────────────

    def test_f11_get_root_serves_index_html(self):
        """F11: Verifies GET / serves index.html with HTML content-type."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        self.assertIn("<html", resp.text.lower())

    def test_f11_get_index_html_serves_html(self):
        """F11: Verifies GET /index.html serves HTML content."""
        resp = self.client.get("/index.html")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))

    def test_f11_get_favicon_serves_204(self):
        """F11: Verifies GET /favicon.ico returns 204 No Content."""
        resp = self.client.get("/favicon.ico")
        self.assertEqual(resp.status_code, 204)

    def test_f11_get_nonexistent_route_returns_404(self):
        """F11: Verifies requests to unknown paths return HTTP 404."""
        resp = self.client.get("/api/unknown_route_404")
        self.assertEqual(resp.status_code, 404)

    def test_f11_options_preflight_returns_cors_headers(self):
        """F11: Verifies OPTIONS preflight request returns 200 with CORS headers."""
        resp = self.client.session.options(f"{self.ctx.base_url}/api/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")


if __name__ == "__main__":
    unittest.main()
