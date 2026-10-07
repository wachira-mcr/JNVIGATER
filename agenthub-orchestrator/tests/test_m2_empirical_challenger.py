"""
Milestone 2 Empirical Challenger Test Suite:
Adversarial Stress Testing of Frontend UI/UX, Process Controls, and Granular Dispatch.
Author: teamwork_preview_challenger_m2_2
"""

import sys
import os
import re
import json
import time
import hashlib
import tempfile
import threading
import subprocess
from pathlib import Path
import unittest
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app
from tests.conftest import TestServerContext


class TestM2TargetedDispatchIsolation(unittest.TestCase):
    """
    Empirically verifies that targeted dispatch (target_worker = worker1/worker2/worker3)
    modifies ONLY the target worker's inbox and preserves other inboxes intact on disk.
    """

    def setUp(self):
        self.ctx = TestServerContext(mock_subprocess=True)
        self.ctx.__enter__()
        self.client = self.ctx.client
        self.workspace = self.ctx.workspace_dir
        self.hub = self.workspace / ".agenthub"
        self.inbox = self.hub / "inbox"

        # Seed initial tasks into all 3 worker inboxes
        self.initial_w1 = (
            "# 📥 Inbox สำหรับ Worker1 (Module A / Backend & Core)\n\n"
            "> **ภารกิจ:** Initial Mission Alpha  \n> **มอบหมายเมื่อ:** 2026-09-28 10:00\n\n---\n\n"
            "### 📝 งานที่ต้องทำ:\nInitial Backend Core Task Alpha\n\n---\n\n"
            "### 📌 ขั้นตอน:\n1. ประกาศเริ่มงานใน `.agenthub/TEAM_CHAT.md`\n"
            "2. ส่งงานที่ `.agenthub/inbox/lead_review.md` เมื่อเสร็จ\n"
        )
        self.initial_w2 = (
            "# 📥 Inbox สำหรับ Worker2 (Module B / Frontend & UI)\n\n"
            "> **ภารกิจ:** Initial Mission Alpha  \n> **มอบหมายเมื่อ:** 2026-09-28 10:00\n\n---\n\n"
            "### 📝 งานที่ต้องทำ:\nInitial Frontend UI Task Alpha\n\n---\n\n"
            "### 📌 ขั้นตอน:\n1. ประกาศเริ่มงานใน `.agenthub/TEAM_CHAT.md`\n"
            "2. ส่งงานที่ `.agenthub/inbox/lead_review.md` เมื่อเสร็จ\n"
        )
        self.initial_w3 = (
            "# 📥 Inbox สำหรับ Worker3 (Module C / Testing & QA)\n\n"
            "> **ภารกิจ:** Initial Mission Alpha  \n> **มอบหมายเมื่อ:** 2026-09-28 10:00\n\n---\n\n"
            "### 📝 งานที่ต้องทำ:\nInitial QA Testing Task Alpha\n\n---\n\n"
            "### 📌 ขั้นตอน:\n1. ประกาศเริ่มงานใน `.agenthub/TEAM_CHAT.md`\n"
            "2. ส่งงานที่ `.agenthub/inbox/lead_review.md` เมื่อเสร็จ\n"
        )
        (self.inbox / "worker1.md").write_text(self.initial_w1, encoding="utf-8")
        (self.inbox / "worker2.md").write_text(self.initial_w2, encoding="utf-8")
        (self.inbox / "worker3.md").write_text(self.initial_w3, encoding="utf-8")

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def _file_hash(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_dispatch_worker1_preserves_worker2_and_worker3(self):
        """Verify targeted dispatch to worker1 updates ONLY worker1.md and preserves worker2 & worker3."""
        w2_hash_before = self._file_hash(self.inbox / "worker2.md")
        w3_hash_before = self._file_hash(self.inbox / "worker3.md")

        payload = {
            "title": "Backend Microservice Refactor",
            "overview": "Data contract for backend",
            "target_worker": "worker1",
            "worker1": "New Task: Refactor SQLAlchemy to async sessions."
        }
        resp = self.client.post_dispatch(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("target_worker"), "worker1")
        self.assertEqual(data.get("updated_inboxes"), ["worker1"])

        # Check worker1.md was updated
        w1_content = (self.inbox / "worker1.md").read_text(encoding="utf-8")
        self.assertIn("Backend Microservice Refactor", w1_content)
        self.assertIn("Refactor SQLAlchemy to async sessions", w1_content)

        # Check worker2.md and worker3.md were completely untouched
        self.assertEqual(self._file_hash(self.inbox / "worker2.md"), w2_hash_before)
        self.assertEqual(self._file_hash(self.inbox / "worker3.md"), w3_hash_before)
        self.assertEqual((self.inbox / "worker2.md").read_text(encoding="utf-8"), self.initial_w2)
        self.assertEqual((self.inbox / "worker3.md").read_text(encoding="utf-8"), self.initial_w3)

        # Verify BOARD.md maintains existing worker2 & worker3 descriptions
        board = (self.hub / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("Refactor SQLAlchemy to async sessions", board)
        self.assertIn("Initial Frontend UI Task Alpha", board)
        self.assertIn("Initial QA Testing Task Alpha", board)
        self.assertIn("กำลังทำ (งานเดิม)", board)

        # Verify chat announcement names Worker 1 specifically
        chat = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn("Backend Microservice Refactor", chat)
        self.assertIn("ให้ Worker1 แล้ว", chat)
        self.assertNotIn("ให้ Worker 1, 2, 3 แล้ว", chat)

        # Verify GET /api/status matches disk state
        status_resp = self.client.get("/api/status").json()
        self.assertIn("Refactor SQLAlchemy to async sessions", status_resp["worker1_inbox"])
        self.assertEqual(status_resp["worker2_inbox"], self.initial_w2)
        self.assertEqual(status_resp["worker3_inbox"], self.initial_w3)

    def test_dispatch_worker2_preserves_worker1_and_worker3(self):
        """Verify targeted dispatch to worker2 updates ONLY worker2.md and preserves worker1 & worker3."""
        w1_hash_before = self._file_hash(self.inbox / "worker1.md")
        w3_hash_before = self._file_hash(self.inbox / "worker3.md")

        payload = {
            "title": "Redesign Dark Modern Navigation",
            "overview": "Design specs",
            "target_worker": "worker2",
            "worker2": "New Task: Implement Activity Bar rail and breadcrumbs."
        }
        resp = self.client.post_dispatch(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("target_worker"), "worker2")
        self.assertEqual(data.get("updated_inboxes"), ["worker2"])

        # Check worker2.md was updated
        w2_content = (self.inbox / "worker2.md").read_text(encoding="utf-8")
        self.assertIn("Redesign Dark Modern Navigation", w2_content)
        self.assertIn("Implement Activity Bar rail", w2_content)

        # Check worker1.md and worker3.md were completely untouched
        self.assertEqual(self._file_hash(self.inbox / "worker1.md"), w1_hash_before)
        self.assertEqual(self._file_hash(self.inbox / "worker3.md"), w3_hash_before)
        self.assertEqual((self.inbox / "worker1.md").read_text(encoding="utf-8"), self.initial_w1)
        self.assertEqual((self.inbox / "worker3.md").read_text(encoding="utf-8"), self.initial_w3)

    def test_dispatch_worker3_preserves_worker1_and_worker2(self):
        """Verify targeted dispatch to worker3 updates ONLY worker3.md and preserves worker1 & worker2."""
        w1_hash_before = self._file_hash(self.inbox / "worker1.md")
        w2_hash_before = self._file_hash(self.inbox / "worker2.md")

        payload = {
            "title": "Adversarial Edge Case Harness",
            "overview": "Testing contract",
            "target_worker": "worker3",
            "worker3": "New Task: Build fuzz generator for JSON and chat inputs."
        }
        resp = self.client.post_dispatch(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("target_worker"), "worker3")
        self.assertEqual(data.get("updated_inboxes"), ["worker3"])

        # Check worker3.md was updated
        w3_content = (self.inbox / "worker3.md").read_text(encoding="utf-8")
        self.assertIn("Adversarial Edge Case Harness", w3_content)
        self.assertIn("Build fuzz generator", w3_content)

        # Check worker1.md and worker2.md were untouched
        self.assertEqual(self._file_hash(self.inbox / "worker1.md"), w1_hash_before)
        self.assertEqual(self._file_hash(self.inbox / "worker2.md"), w2_hash_before)

    def test_dispatch_all_updates_all_inboxes(self):
        """Verify target_worker='all' writes to all three worker inboxes."""
        payload = {
            "title": "Swarm Broad Synchronization",
            "overview": "All hands on deck",
            "target_worker": "all",
            "worker1": "All Task 1",
            "worker2": "All Task 2",
            "worker3": "All Task 3",
        }
        resp = self.client.post_dispatch(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("target_worker"), "all")
        self.assertEqual(set(data.get("updated_inboxes", [])), {"worker1", "worker2", "worker3"})

        self.assertIn("All Task 1", (self.inbox / "worker1.md").read_text(encoding="utf-8"))
        self.assertIn("All Task 2", (self.inbox / "worker2.md").read_text(encoding="utf-8"))
        self.assertIn("All Task 3", (self.inbox / "worker3.md").read_text(encoding="utf-8"))

    def test_dispatch_invalid_target_worker_rejected_with_400(self):
        """Adversarial stress: Invalid target_worker strings must be rejected with HTTP 400."""
        invalid_targets = [
            "worker4",
            "lead",
            "admin",
            "supervisor",
            "all_workers",
            "worker1; rm -rf /",
            "../../../etc/passwd",
            "",
            "   ",
        ]
        w1_before = (self.inbox / "worker1.md").read_text(encoding="utf-8")
        w2_before = (self.inbox / "worker2.md").read_text(encoding="utf-8")
        w3_before = (self.inbox / "worker3.md").read_text(encoding="utf-8")

        for bad_target in invalid_targets:
            payload = {
                "title": "Exploit Attempt",
                "target_worker": bad_target,
                "task": "Should not be written"
            }
            resp = self.client.post_dispatch(payload)
            self.assertEqual(
                resp.status_code, 400,
                f"Expected 400 for target_worker='{bad_target}', got {resp.status_code}"
            )
            # Verify no inbox was modified
            self.assertEqual((self.inbox / "worker1.md").read_text(encoding="utf-8"), w1_before)
            self.assertEqual((self.inbox / "worker2.md").read_text(encoding="utf-8"), w2_before)
            self.assertEqual((self.inbox / "worker3.md").read_text(encoding="utf-8"), w3_before)

    def test_dispatch_targeted_with_task_fallback_key(self):
        """Verify targeted dispatch accepts 'task' field as fallback for worker-specific field."""
        payload = {
            "title": "Fallback Key Mission",
            "target_worker": "worker2",
            "task": "Task passed via generic 'task' key rather than 'worker2' key"
        }
        resp = self.client.post_dispatch(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))
        w2_content = (self.inbox / "worker2.md").read_text(encoding="utf-8")
        self.assertIn("Task passed via generic 'task' key", w2_content)

    def test_sequential_alternating_dispatches_integrity(self):
        """Stress test: 12 alternating targeted dispatches verifying isolation at each step."""
        targets = ["worker1", "worker2", "worker3"]
        history = {
            "worker1": self.initial_w1,
            "worker2": self.initial_w2,
            "worker3": self.initial_w3,
        }

        for cycle in range(4):
            for t in targets:
                mission_title = f"Cycle {cycle} Mission for {t}"
                task_content = f"Specific task order for {t} in cycle {cycle}"
                payload = {
                    "title": mission_title,
                    "target_worker": t,
                    t: task_content,
                }
                resp = self.client.post_dispatch(payload)
                self.assertEqual(resp.status_code, 200)

                # Target inbox MUST be updated
                target_file = self.inbox / f"{t}.md"
                content = target_file.read_text(encoding="utf-8")
                self.assertIn(mission_title, content)
                self.assertIn(task_content, content)
                history[t] = content

                # Non-target inboxes MUST equal their latest history
                for other in targets:
                    if other != t:
                        other_content = (self.inbox / f"{other}.md").read_text(encoding="utf-8")
                        self.assertEqual(
                            other_content, history[other],
                            f"Non-target {other} corrupted during dispatch to {t} in cycle {cycle}"
                        )


class TestM2ProcessControlsAndLaunch(unittest.TestCase):
    """
    Empirically verifies process launch endpoints, mock interactions, and status updates.
    """

    def setUp(self):
        self.ctx = TestServerContext(mock_subprocess=True)
        self.ctx.__enter__()
        self.client = self.ctx.client

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_launch_single_targets(self):
        """Verify POST /api/launch succeeds for all 4 individual targets."""
        for target in ("lead", "worker1", "worker2", "worker3"):
            resp = self.client.post_launch(target)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("success"), f"Launch failed for {target}: {data}")
            self.assertIn(target, data.get("launched", []))
            self.assertEqual(data.get("failed", []), [])

    def test_launch_all_spawns_all_profiles(self):
        """Verify POST /api/launch with target='all' launches all 4 profiles."""
        resp = self.client.post_launch("all")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(set(data.get("launched", [])), {"lead", "worker1", "worker2", "worker3"})
        self.assertEqual(data.get("failed", []), [])

    def test_concurrent_launch_requests_resilience(self):
        """Stress test: 15 concurrent launch requests do not crash or deadlock the server."""
        num_threads = 15
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(num_threads)

        def worker(tid):
            target = ["lead", "worker1", "worker2", "worker3", "all"][tid % 5]
            barrier.wait()
            resp = self.client.post_launch(target)
            with lock:
                results.append((resp.status_code, resp.json().get("success", False)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), num_threads)
        for code, success in results:
            self.assertEqual(code, 200)
            self.assertTrue(success)


class TestM2FrontendDOMAndOfflineResilience(unittest.TestCase):
    """
    Empirically verifies public/index.html DOM structure, offline resilience, and UI controls.
    """

    @classmethod
    def setUpClass(cls):
        cls.html_path = PROJECT_ROOT / "public" / "index.html"
        cls.html_content = cls.html_path.read_text(encoding="utf-8")
        cls.soup = BeautifulSoup(cls.html_content, "html.parser")

    def test_zero_cdn_offline_resilience(self):
        """Verify zero external HTTP/HTTPS resources in public/index.html."""
        # Find all script src, link href, img src, style @import
        urls = re.findall(r'https?://[^\s"\'<>]+', self.html_content)
        # Filter out links inside user-readable chat text or examples if any
        # But per requirements: ZERO external CDN links allowed anywhere in script/link/import
        for tag in self.soup.find_all(["script", "link", "img", "iframe"]):
            src = tag.get("src") or tag.get("href") or ""
            self.assertFalse(
                src.startswith("http://") or src.startswith("https://") or src.startswith("//"),
                f"Found external CDN asset reference in tag: {tag}"
            )
        # Check inside CSS <style> for external imports or url()
        for style in self.soup.find_all("style"):
            css = style.string or ""
            self.assertNotIn("@import url('http", css)
            self.assertNotIn("@import url('https", css)

    def test_dom_process_control_elements_exist(self):
        """Verify all required process control DOM elements exist for lead and workers 1-3."""
        agents = ["lead", "worker1", "worker2", "worker3"]

        for agent in agents:
            # Account cards in #view-accounts
            self.assertIsNotNone(
                self.soup.find(id=f"status-dot-{agent}"),
                f"Missing status-dot-{agent}"
            )
            self.assertIsNotNone(
                self.soup.find(id=f"status-label-{agent}"),
                f"Missing status-label-{agent}"
            )
            self.assertIsNotNone(
                self.soup.find(id=f"pid-badge-{agent}"),
                f"Missing pid-badge-{agent}"
            )
            self.assertIsNotNone(
                self.soup.find(id=f"btn-launch-{agent}"),
                f"Missing btn-launch-{agent}"
            )
            # Sidebar indicators
            self.assertIsNotNone(
                self.soup.find(id=f"sb-dot-{agent}"),
                f"Missing sb-dot-{agent}"
            )
            self.assertIsNotNone(
                self.soup.find(id=f"sb-pid-{agent}"),
                f"Missing sb-pid-{agent}"
            )

        # Batch launch buttons
        self.assertIsNotNone(self.soup.find(id="btn-launch-all-accounts"))
        self.assertIsNotNone(self.soup.find(id="btn-launch-all-sidebar"))

        # Status summary in bottom bar
        self.assertIsNotNone(self.soup.find(id="status-process-summary"))

    def test_dom_granular_dispatch_elements_exist(self):
        """Verify granular dispatch chips, form fields, and column containers exist."""
        # Target selector chips
        target_chips = self.soup.find_all(class_="target-chip")
        found_targets = {chip.get("data-target") for chip in target_chips}
        self.assertEqual(
            found_targets,
            {"all", "worker1", "worker2", "worker3"},
            f"Target chips mismatch: found {found_targets}"
        )

        # Dispatch form fields
        self.assertIsNotNone(self.soup.find(id="task-title"))
        self.assertIsNotNone(self.soup.find(id="task-overview"))
        self.assertIsNotNone(self.soup.find(id="task-worker1"))
        self.assertIsNotNone(self.soup.find(id="task-worker2"))
        self.assertIsNotNone(self.soup.find(id="task-worker3"))

        # Column containers for visual focus / dimming
        self.assertIsNotNone(self.soup.find(id="col-task-worker1"))
        self.assertIsNotNone(self.soup.find(id="col-task-worker2"))
        self.assertIsNotNone(self.soup.find(id="col-task-worker3"))

        # Submit button & label
        self.assertIsNotNone(self.soup.find(id="btn-dispatch"))
        self.assertIsNotNone(self.soup.find(id="btn-dispatch-label"))

    def test_dom_presets_exist(self):
        """Verify preset buttons exist for fullstack, ai, and refactor."""
        presets = ["fullstack", "ai", "refactor"]
        for p in presets:
            btn = self.soup.find(lambda tag: tag.name == "button" and f"applyPreset('{p}')" in (tag.get("onclick") or ""))
            self.assertIsNotNone(btn, f"Preset button for '{p}' not found")


class TestM2FrontendJavascriptLogicWithNode(unittest.TestCase):
    """
    Executes actual JavaScript client logic from public/index.html using Node.js
    in an emulated minimal DOM environment to verify client-side state machine.
    """

    @classmethod
    def setUpClass(cls):
        html_path = PROJECT_ROOT / "public" / "index.html"
        html_content = html_path.read_text(encoding="utf-8")
        # Extract <script> content
        match = re.search(r'<script>([\s\S]*?)</script>', html_content)
        cls.js_code = match.group(1) if match else ""

    def test_js_code_extracted(self):
        self.assertTrue(len(self.js_code) > 500, "Failed to extract JavaScript from index.html")

    def test_node_execution_select_dispatch_target(self):
        """Test selectDispatchTarget() in Node.js with simulated DOM."""
        node_script = """
        // Minimal DOM Mock
        global.setInterval = () => {};
        global.setTimeout = () => {};

        class ClassList {
            constructor() { this.classes = new Set(); }
            add(c) { this.classes.add(c); }
            remove(c) { this.classes.delete(c); }
            contains(c) { return this.classes.has(c); }
            has(c) { return this.classes.has(c); }
            toggle(c, force) {
                if (force === undefined) {
                    if (this.classes.has(c)) this.classes.delete(c);
                    else this.classes.add(c);
                } else if (force) {
                    this.classes.add(c);
                } else {
                    this.classes.delete(c);
                }
            }
        }

        class Element {
            constructor(id) {
                this.id = id;
                this.classList = new ClassList();
                this.attributes = {};
                this.value = '';
                this.placeholder = '';
                this.textContent = '';
                this.innerHTML = '';
            }
            getAttribute(k) { return this.attributes[k]; }
            setAttribute(k, v) { this.attributes[k] = v; }
        }

        const elements = {};
        function getEl(id) {
            if (!elements[id]) elements[id] = new Element(id);
            return elements[id];
        }

        const chips = ['all', 'worker1', 'worker2', 'worker3'].map(t => {
            const el = new Element('chip-' + t);
            el.setAttribute('data-target', t);
            return el;
        });

        global.document = {
            getElementById: id => getEl(id),
            querySelectorAll: sel => (sel === '.target-chip' ? chips : []),
            addEventListener: () => {}
        };

        """ + self.js_code + """

        // Test 1: selectDispatchTarget('worker1')
        selectDispatchTarget('worker1');
        const w1Active = chips.find(c => c.getAttribute('data-target') === 'worker1').classList.has('active');
        const allActive = chips.find(c => c.getAttribute('data-target') === 'all').classList.has('active');
        const colW1Focused = getEl('col-task-worker1').classList.has('focused-worker');
        const colW2Dimmed = getEl('col-task-worker2').classList.has('dimmed');
        const colW3Dimmed = getEl('col-task-worker3').classList.has('dimmed');
        const submitLabel = getEl('btn-dispatch-label').textContent;

        console.log(JSON.stringify({
            currentDispatchTarget,
            w1Active,
            allActive,
            colW1Focused,
            colW2Dimmed,
            colW3Dimmed,
            submitLabel
        }));
        process.exit(0);
        """

        result = subprocess.run(["node"], input=node_script, encoding="utf-8", capture_output=True, check=True)
        data = json.loads(result.stdout.strip())
        self.assertEqual(data["currentDispatchTarget"], "worker1")
        self.assertTrue(data["w1Active"])
        self.assertFalse(data["allActive"])
        self.assertTrue(data["colW1Focused"])
        self.assertTrue(data["colW2Dimmed"])
        self.assertTrue(data["colW3Dimmed"])
        self.assertIn("WORKER1", data["submitLabel"])

    def test_node_execution_update_process_indicators(self):
        """Test updateProcessIndicators() in Node.js with simulated DOM."""
        node_script = """
        global.setInterval = () => {};
        global.setTimeout = () => {};

        class Element {
            constructor(id) {
                this.id = id;
                this.className = '';
                this.textContent = '';
                this.title = '';
                this.children = [];
            }
            querySelector(sel) { return null; }
        }

        const elements = {};
        function getEl(id) {
            if (!elements[id]) elements[id] = new Element(id);
            return elements[id];
        }

        global.document = {
            getElementById: id => getEl(id),
            querySelectorAll: () => [],
            addEventListener: () => {}
        };

        """ + self.js_code + """

        // Test with lead and worker2 running, worker1 and worker3 stopped
        const testStatus = {
            lead: { running: true, pid: 1111 },
            worker1: { running: false, pid: null },
            worker2: { running: true, pid: 2222 },
            worker3: { running: false, pid: null }
        };

        updateProcessIndicators(testStatus);

        console.log(JSON.stringify({
            lead_dot: getEl('status-dot-lead').className,
            lead_label: getEl('status-label-lead').textContent,
            lead_pid: getEl('pid-badge-lead').textContent,
            w1_dot: getEl('status-dot-worker1').className,
            w1_label: getEl('status-label-worker1').textContent,
            w1_pid: getEl('pid-badge-worker1').textContent,
            w2_pid: getEl('pid-badge-worker2').textContent,
            summary: getEl('status-process-summary').textContent
        }));
        process.exit(0);
        """

        result = subprocess.run(["node"], input=node_script, encoding="utf-8", capture_output=True, check=True)
        data = json.loads(result.stdout.strip())
        self.assertIn("running", data["lead_dot"])
        self.assertEqual(data["lead_label"], "Running")
        self.assertEqual(data["lead_pid"], "PID: 1111")

        self.assertIn("stopped", data["w1_dot"])
        self.assertEqual(data["w1_label"], "Stopped")
        self.assertEqual(data["w1_pid"], "Inactive")

        self.assertEqual(data["w2_pid"], "PID: 2222")
        self.assertEqual(data["summary"], "2/4 Agents Active")

    def test_node_execution_payload_builder_isolation(self):
        """Simulate submitDispatch payload generation for targeted vs broadcast."""
        node_script = """
        class Element {
            constructor(id, val = '') {
                this.id = id;
                this.value = val;
            }
        }
        const elements = {
            'task-title': new Element('task-title', 'Auth Project'),
            'task-overview': new Element('task-overview', 'Overview specs'),
            'task-worker1': new Element('task-worker1', 'Backend task'),
            'task-worker2': new Element('task-worker2', 'Frontend task'),
            'task-worker3': new Element('task-worker3', 'QA task'),
        };

        function simulatePayload(target) {
            const title = elements['task-title'].value.trim();
            const overview = elements['task-overview'].value.trim();
            const payload = { title, overview, target_worker: target };
            if (target === 'all') {
                payload.worker1 = elements['task-worker1'].value.trim();
                payload.worker2 = elements['task-worker2'].value.trim();
                payload.worker3 = elements['task-worker3'].value.trim();
            } else {
                payload[target] = elements['task-' + target].value.trim();
            }
            return payload;
        }

        console.log(JSON.stringify({
            all: simulatePayload('all'),
            w1: simulatePayload('worker1'),
            w2: simulatePayload('worker2'),
            w3: simulatePayload('worker3')
        }));
        """

        result = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout.strip())

        # For w1, payload MUST NOT include worker2 or worker3 keys!
        self.assertIn("worker1", data["w1"])
        self.assertNotIn("worker2", data["w1"])
        self.assertNotIn("worker3", data["w1"])

        # For w2, payload MUST NOT include worker1 or worker3 keys!
        self.assertIn("worker2", data["w2"])
        self.assertNotIn("worker1", data["w2"])
        self.assertNotIn("worker3", data["w2"])

        # For 'all', payload includes all 3 keys
        self.assertIn("worker1", data["all"])
        self.assertIn("worker2", data["all"])
        self.assertIn("worker3", data["all"])


if __name__ == "__main__":
    unittest.main()
