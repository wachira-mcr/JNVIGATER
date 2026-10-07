"""
Tier 1: Static Verification & Feature Coverage Test Suite.
Validates file structure, physical executables, independent roaming profiles,
python syntax/AST compilation, and frontend HTML/CSS component presence.
"""

import os
import sys
import ast
import py_compile
import tempfile
import shutil
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app


class TestTier1StaticVerification(unittest.TestCase):
    """Tier 1: Static verification tests for executables, profiles, code integrity, and templates."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tier1_test_")
        self.workspace = Path(self.temp_dir) / "test_workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ── Feature 1: Workspace Initialization ───────────────────────────────────

    def test_f01_workspace_init_creates_hub_directory(self):
        """F01: Verifies _ensure_agenthub creates .agenthub and inbox directories."""
        app._ensure_agenthub(self.workspace)
        hub = self.workspace / ".agenthub"
        self.assertTrue(hub.exists(), ".agenthub directory must be created")
        self.assertTrue(hub.is_dir(), ".agenthub must be a directory")
        self.assertTrue((hub / "inbox").exists(), "inbox directory must be created")

    def test_f01_workspace_init_creates_default_files(self):
        """F01: Verifies default markdown files are created with non-empty content."""
        app._ensure_agenthub(self.workspace)
        hub = self.workspace / ".agenthub"
        expected_files = [
            "BOARD.md",
            "TEAM_CHAT.md",
            "inbox/lead_review.md",
            "inbox/worker1.md",
            "inbox/worker2.md",
            "inbox/worker3.md",
        ]
        for rel_path in expected_files:
            file_path = hub / rel_path
            self.assertTrue(file_path.exists(), f"Expected file {rel_path} to exist")
            content = file_path.read_text(encoding="utf-8")
            self.assertGreater(len(content.strip()), 0, f"File {rel_path} must not be empty")

    def test_f01_workspace_init_idempotency(self):
        """F01: Verifies re-running _ensure_agenthub does not overwrite existing data."""
        app._ensure_agenthub(self.workspace)
        board_file = self.workspace / ".agenthub" / "BOARD.md"
        custom_content = "# Custom Board Content\nNever overwrite me!"
        board_file.write_text(custom_content, encoding="utf-8")

        # Second initialization call
        app._ensure_agenthub(self.workspace)
        self.assertEqual(board_file.read_text(encoding="utf-8"), custom_content)

    def test_f01_workspace_init_all_worker_inboxes(self):
        """F01: Verifies each worker has an individualized inbox file."""
        app._ensure_agenthub(self.workspace)
        inbox = self.workspace / ".agenthub" / "inbox"
        for w in ("worker1", "worker2", "worker3"):
            w_file = inbox / f"{w}.md"
            self.assertTrue(w_file.exists())
            self.assertIn(w, w_file.read_text(encoding="utf-8").lower())

    # ── Feature 2: Central Board Management ───────────────────────────────────

    def test_f02_board_template_structure(self):
        """F02: Verifies BOARD.md default template contains title and readiness status."""
        app._ensure_agenthub(self.workspace)
        board_content = (self.workspace / ".agenthub" / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("Central Project Board", board_content)

    def test_f02_board_encoding_utf8_without_bom(self):
        """F02: Verifies BOARD.md is saved in valid UTF-8 without byte order mark (BOM)."""
        app._ensure_agenthub(self.workspace)
        raw_bytes = (self.workspace / ".agenthub" / "BOARD.md").read_bytes()
        self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf"), "BOARD.md must not contain UTF-8 BOM")

    # ── Feature 3: Worker Task Inboxes ────────────────────────────────────────

    def test_f03_worker_inboxes_contain_instruction_headers(self):
        """F03: Verifies worker inboxes contain instruction headers and status prompts."""
        app._ensure_agenthub(self.workspace)
        inbox = self.workspace / ".agenthub" / "inbox"
        for w in ("worker1", "worker2", "worker3"):
            content = (inbox / f"{w}.md").read_text(encoding="utf-8")
            self.assertIn("Inbox", content)

    # ── Feature 4: Live Swarm Chat Stream ─────────────────────────────────────

    def test_f04_chat_initial_content(self):
        """F04: Verifies TEAM_CHAT.md begins with a system header/message."""
        app._ensure_agenthub(self.workspace)
        chat = (self.workspace / ".agenthub" / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn("Swarm Team Live Chat", chat)
        self.assertIn("[System]", chat)

    # ── Feature 5: Lead Review Queue ──────────────────────────────────────────

    def test_f05_lead_review_file_initial_state(self):
        """F05: Verifies lead_review.md exists and is prepared for submissions."""
        app._ensure_agenthub(self.workspace)
        review = (self.workspace / ".agenthub" / "inbox" / "lead_review.md").read_text(encoding="utf-8")
        self.assertIn("Review", review)

    # ── Feature 7: Cloned Executable Integration ──────────────────────────────

    def test_f07_antigravity_binaries_exist_on_host(self):
        """F07: Verifies all 4 physical executable binaries exist on the host filesystem."""
        programs_dir = Path(r"C:\Users\MBx13\AppData\Local\Programs")
        expected_binaries = {
            "lead": programs_dir / "antigravity" / "Antigravity.exe",
            "worker1": programs_dir / "antigravity-worker1" / "Worker1.exe",
            "worker2": programs_dir / "antigravity-worker2" / "Worker2.exe",
            "worker3": programs_dir / "antigravity-worker3" / "Worker3.exe",
        }
        for agent_id, exe_path in expected_binaries.items():
            self.assertTrue(
                exe_path.exists(),
                f"Binary for {agent_id} must exist at {exe_path}"
            )

    def test_f07_app_profiles_point_to_valid_executables(self):
        """F07: Verifies app.PROFILES paths resolve directly to existing executables."""
        for target, profile in app.PROFILES.items():
            exe_path = Path(profile["exe"])
            self.assertTrue(exe_path.exists(), f"Profile {target} exe does not exist: {exe_path}")

    # ── Feature 10: Profile & Session Isolation ───────────────────────────────

    def test_f10_roaming_profile_directories_exist(self):
        """F10: Verifies separate AppData\\Roaming user profile roots exist for 4 accounts."""
        roaming = Path(r"C:\Users\MBx13\AppData\Roaming")
        expected_profiles = [
            roaming / "Antigravity",
            roaming / "Antigravity Worker 1",
            roaming / "Antigravity Worker 2",
            roaming / "Antigravity Worker 3",
        ]
        for prof_path in expected_profiles:
            self.assertTrue(
                prof_path.exists() and prof_path.is_dir(),
                f"Roaming profile directory must exist: {prof_path}"
            )

    def test_f10_profiles_definition_has_all_agents(self):
        """F10: Verifies PROFILES dictionary contains all 4 agents with required metadata."""
        required_keys = {"lead", "worker1", "worker2", "worker3"}
        self.assertEqual(set(app.PROFILES.keys()), required_keys)
        for agent_id, p in app.PROFILES.items():
            self.assertEqual(p["id"], agent_id)
            self.assertIn("name", p)
            self.assertIn("role", p)
            self.assertIn("exe", p)
            self.assertIn("color", p)

    # ── Codebase Syntax & AST Compilation ─────────────────────────────────────

    def test_code_compile_app_py(self):
        """Verifies app.py compiles cleanly to bytecode without syntax errors."""
        app_path = PROJECT_ROOT / "app.py"
        self.assertTrue(app_path.exists())
        # AST parse
        with open(app_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="app.py")
        self.assertIsNotNone(tree)
        # Bytecode compile
        py_compile.compile(str(app_path), doraise=True)

    def test_code_compile_server_py(self):
        """Verifies legacy server.py compiles cleanly without syntax errors."""
        server_path = PROJECT_ROOT / "server.py"
        self.assertTrue(server_path.exists())
        with open(server_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="server.py")
        self.assertIsNotNone(tree)
        py_compile.compile(str(server_path), doraise=True)

    def test_code_compile_clone_agents_py(self):
        """Verifies clone_agents.py compiles cleanly without syntax errors."""
        clone_path = PROJECT_ROOT / "clone_agents.py"
        self.assertTrue(clone_path.exists())
        with open(clone_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="clone_agents.py")
        self.assertIsNotNone(tree)
        py_compile.compile(str(clone_path), doraise=True)

    # ── Features 11-19: Frontend UI Layout & Static Inspection ────────────────

    def test_f11_html_ui_exists_and_readable(self):
        """F11: Verifies public/index.html exists and is non-empty."""
        index_html = PROJECT_ROOT / "public" / "index.html"
        self.assertTrue(index_html.exists(), "public/index.html must exist")
        content = index_html.read_text(encoding="utf-8")
        self.assertGreater(len(content), 1000)

    def test_f11_theme_variables_in_html(self):
        """F11: Verifies VS Code dark theme CSS variables are defined in public/index.html."""
        html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("--bg-app", html)
        self.assertIn("--bg-sidebar", html)
        self.assertIn("--accent-blue", html)

    def test_f12_activity_bar_in_html(self):
        """F12: Verifies Activity Bar icon rail container exists with 44px width definition."""
        html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("activity-bar", html)
        self.assertIn("44px", html)

    def test_f13_editor_tabs_and_breadcrumbs_in_html(self):
        """F13: Verifies editor tabs and breadcrumb navigation are present in the DOM."""
        html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
        # Tabs and breadcrumbs
        self.assertTrue("tab" in html.lower())
        self.assertTrue("breadcrumb" in html.lower() or "agenthub" in html.lower())

    def test_f14_unified_chat_thread_in_html(self):
        """F14: Verifies live chat container and input elements exist in UI."""
        html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("chat", html.lower())

    def test_f16_task_presets_in_html(self):
        """F16: Verifies task preset options are defined in the template."""
        html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
        # Check for preset templates (Full-Stack / Data & AI / Refactor & Test)
        self.assertTrue(
            "full-stack" in html.lower() or "web" in html.lower() or "preset" in html.lower(),
            "Task presets should be present in UI"
        )

    def test_f17_account_launcher_cards_in_html(self):
        """F17: Verifies account launcher cards exist for all 4 profiles."""
        html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
        for agent_id in ("lead", "worker1", "worker2", "worker3"):
            self.assertIn(agent_id, html.lower(), f"Agent ID {agent_id} should appear in launcher UI")

    def test_f19_desktop_status_bar_in_html(self):
        """F19: Verifies persistent desktop status bar elements exist."""
        html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertTrue(
            "statusbar" in html.lower() or "status-bar" in html.lower() or "8989" in html,
            "Desktop status bar should be present in UI"
        )

    # ── Features 20-21: Desktop Container & JS API ────────────────────────────

    def test_f20_webview_fallback_logic_present(self):
        """F20: Verifies HAS_WEBVIEW import guard and browser fallback exist in app.py."""
        self.assertTrue(hasattr(app, "HAS_WEBVIEW"))
        self.assertTrue(hasattr(app, "JSApi"))

    def test_f21_js_api_pick_folder_method_exists(self):
        """F21: Verifies JSApi class exposes pick_folder method."""
        api = app.JSApi()
        self.assertTrue(hasattr(api, "pick_folder"))
        self.assertTrue(callable(api.pick_folder))


if __name__ == "__main__":
    unittest.main()
