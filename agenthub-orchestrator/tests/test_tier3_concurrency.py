"""
Tier 3: Concurrency, Race Condition, and Error Path Test Suite.
Validates multi-threaded chat appends, status polling under write load,
simultaneous dispatches, malformed payloads, and edge-case resilience.
"""

import sys
import json
import time
import concurrent.futures
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app
from tests.conftest import TestServerContext


class TestTier3ConcurrencyAndEdgeCases(unittest.TestCase):
    """Tier 3: Concurrency, race condition, and robustness validation."""

    def setUp(self):
        self.ctx = TestServerContext(mock_subprocess=True)
        self.ctx.__enter__()
        self.client = self.ctx.client
        self.workspace = self.ctx.workspace_dir
        self.hub = self.workspace / ".agenthub"

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    # ── Multi-Threaded Chat Appends ───────────────────────────────────────────

    def test_f04_concurrent_chat_appends_lossless(self):
        """F04: Verifies concurrent chat messages from multiple agents are all persisted."""
        num_messages = 12
        agents = ["lead", "worker1", "worker2", "worker3"]

        def send_msg(idx):
            sender = agents[idx % len(agents)]
            msg = f"Concurrent message sequence #{idx} from {sender}"
            res = self.client.post_chat(sender, msg)
            return idx, res.status_code, res.json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(send_msg, i) for i in range(num_messages)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All requests should return 200 OK
        for idx, status, body in results:
            self.assertEqual(status, 200)
            self.assertTrue(body.get("success"), f"Request #{idx} failed: {body}")

        # Check TEAM_CHAT.md content
        chat_content = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        for i in range(num_messages):
            self.assertIn(f"Concurrent message sequence #{i}", chat_content)

    def test_f04_concurrent_chat_delimiter_integrity(self):
        """F04: Verifies chat line delimiters are preserved under concurrent writes."""
        num_messages = 8

        def send_line(idx):
            return self.client.post_chat(f"Worker{idx}", f"Strict line test message #{idx}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(send_line, range(num_messages)))

        chat_lines = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8").splitlines()
        # Ensure all lines starting with "- [" or "#" are well-formed
        for line in chat_lines:
            line_s = line.strip()
            if line_s.startswith("- ["):
                self.assertIn("]:", line_s, f"Corrupted delimiter format in line: {line_s}")

    # ── Status Polling Under Write Load ───────────────────────────────────────

    def test_f06_concurrent_chat_poll_race(self):
        """F06: Verifies rapid status polling does not lock or crash during active chat writes."""
        import threading
        stop_event = threading.Event()
        poll_errors = []

        def poller():
            while not stop_event.is_set():
                try:
                    resp = self.client.get("/api/status")
                    if resp.status_code != 200:
                        poll_errors.append(f"HTTP {resp.status_code}")
                except Exception as e:
                    poll_errors.append(str(e))
                time.sleep(0.01)

        def writer(idx):
            return self.client.post_chat(f"Writer_{idx}", f"High frequency update {idx}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # Start 2 pollers
            poll_future1 = executor.submit(poller)
            poll_future2 = executor.submit(poller)

            # Send 10 messages concurrently
            write_futures = [executor.submit(writer, i) for i in range(10)]
            for wf in concurrent.futures.as_completed(write_futures):
                wf.result()

            stop_event.set()
            poll_future1.result()
            poll_future2.result()

        self.assertEqual(len(poll_errors), 0, f"Encountered polling errors during writes: {poll_errors}")

    # ── Concurrent Dispatches ─────────────────────────────────────────────────

    def test_f02_concurrent_board_dispatches(self):
        """F02: Verifies concurrent dispatch calls do not corrupt BOARD.md or crash server."""
        def dispatch_task(idx):
            payload = {
                "title": f"Concurrent Mission #{idx}",
                "overview": f"Mission specification #{idx}",
                "worker1": f"Task 1 for mission #{idx}",
                "worker2": f"Task 2 for mission #{idx}",
                "worker3": f"Task 3 for mission #{idx}",
            }
            res = self.client.post_dispatch(payload)
            return res.status_code, res.json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(dispatch_task, i) for i in range(4)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for status, body in results:
            self.assertEqual(status, 200)
            self.assertTrue(body.get("success"))

        # Verify board file is intact UTF-8
        board_content = (self.hub / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("Central Project Board", board_content)
        self.assertIn("Responsibility Matrix", board_content)

    # ── Error Cascading & Malformed Payloads ───────────────────────────────────

    def test_edge_post_chat_invalid_json(self):
        """Verifies posting invalid JSON to /api/chat does not crash server."""
        resp = self.client.session.post(
            f"{self.ctx.base_url}/api/chat",
            data="this is definitely not json",
            headers={"Content-Type": "application/json"}
        )
        # Should return 4xx or 500 error gracefully without terminating server
        self.assertIn(resp.status_code, [400, 500])

        # Server must still be alive and responsive
        status_resp = self.client.get("/api/status")
        self.assertEqual(status_resp.status_code, 200)

    def test_edge_post_dispatch_invalid_json(self):
        """Verifies posting invalid JSON to /api/dispatch handled safely."""
        resp = self.client.session.post(
            f"{self.ctx.base_url}/api/dispatch",
            data="{malformed_json: missing_quotes",
            headers={"Content-Type": "application/json"}
        )
        self.assertIn(resp.status_code, [400, 500])

        # Verify subsequent request works
        self.assertEqual(self.client.get("/api/status").status_code, 200)

    def test_edge_oversized_chat_message(self):
        """Verifies posting a large 50KB chat message is handled properly."""
        large_text = "A" * 50000
        resp = self.client.post_chat("StressAgent", large_text)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        chat_content = (self.hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        self.assertIn(large_text[:100], chat_content)

    def test_f09_concurrent_launch_all_requests(self):
        """F09: Verifies multiple rapid launch requests are handled without deadlocking."""
        def trigger_launch():
            return self.client.post_launch("all")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(trigger_launch) for _ in range(2)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for res in results:
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json().get("success"))


if __name__ == "__main__":
    unittest.main()
