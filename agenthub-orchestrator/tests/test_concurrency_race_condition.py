"""
Targeted Empirical Concurrency & Race Condition Verification for app.py.
Demonstrates:
1. test_chat_50_threads_zero_loss: Confirms _chat_lock guarantees zero message loss under 50+ threads.
2. test_concurrent_dispatch_board_race_condition: Empirically reproduces race condition on BOARD.md
   due to lack of synchronization and in-place truncation in Path.write_text().
"""

import sys
import os
import re
import time
import uuid
import json
import threading
import concurrent.futures
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app
from tests.conftest import TestServerContext

def test_chat_50_threads_zero_loss():
    """Verify that pure chat with 50 threads has zero message loss."""
    num_threads = 50
    msgs_per_thread = 4
    total_msgs = num_threads * msgs_per_thread
    print(f"\n[TEST 1] Chat 50 Threads Concurrency: {num_threads} threads x {msgs_per_thread} = {total_msgs} messages")

    with TestServerContext(mock_subprocess=True) as ctx:
        client = ctx.client
        chat_file = ctx.workspace_dir / ".agenthub" / "TEAM_CHAT.md"

        barrier = threading.Barrier(num_threads + 1)
        results = []
        lock = threading.Lock()
        sent_ids = set()

        def sender(t_id):
            barrier.wait()
            for m_id in range(msgs_per_thread):
                msg_tag = f"MSG_T{t_id:02d}_M{m_id:02d}_{uuid.uuid4().hex[:6]}"
                with lock:
                    sent_ids.add(msg_tag)
                resp = client.post_chat(f"Worker_{t_id}", f"[{msg_tag}] Message payload.")
                with lock:
                    results.append((msg_tag, resp.status_code, resp.json().get("success", False)))

        threads = [threading.Thread(target=sender, args=(i,), daemon=True) for i in range(num_threads)]
        for t in threads:
            t.start()

        barrier.wait()
        for t in threads:
            t.join(timeout=20)

        success_count = sum(1 for _, code, ok in results if code == 200 and ok)
        final_chat = chat_file.read_text(encoding="utf-8")
        found_in_file = sum(1 for tag in sent_ids if tag in final_chat)

        print(f"[TEST 1] Successful HTTP 200 responses: {success_count}/{total_msgs}")
        print(f"[TEST 1] Messages verified in TEAM_CHAT.md: {found_in_file}/{total_msgs}")

        assert success_count == total_msgs, f"Failed chat requests: {total_msgs - success_count}"
        assert found_in_file == total_msgs, f"Lost chat messages: {total_msgs - found_in_file}"
        print("[TEST 1] RESULT: PASS - Zero message loss verified.")


def test_concurrent_dispatch_board_race_condition():
    """
    Empirically reproduce the race condition on BOARD.md during concurrent dispatches & status polling.
    Under concurrent dispatches, absence of synchronization locks on BOARD.md causes:
    1. Empty board reads by /api/status (due to Path.write_text opening file with mode 'w' which truncates before writing)
    2. Overlapping write operations corrupting UTF-8 multi-byte sequences or overwriting state
    """
    num_dispatchers = 10
    num_pollers = 10
    dispatches_per_thread = 3
    total_dispatches = num_dispatchers * dispatches_per_thread

    print(f"\n[TEST 2] Reproducing BOARD.md Race Condition: {num_dispatchers} dispatchers + {num_pollers} pollers")

    with TestServerContext(mock_subprocess=True) as ctx:
        client = ctx.client
        board_file = ctx.workspace_dir / ".agenthub" / "BOARD.md"

        stop_event = threading.Event()
        empty_board_reads = []
        poll_count = [0]
        lock = threading.Lock()

        def poller():
            while not stop_event.is_set():
                try:
                    resp = client.get("/api/status")
                    if resp.status_code == 200:
                        with lock:
                            poll_count[0] += 1
                        board = resp.json().get("board", "")
                        if not board or len(board.strip()) == 0 or "Central Project Board" not in board:
                            with lock:
                                empty_board_reads.append(board)
                except Exception:
                    pass
                time.sleep(0.005)

        targets = ["all", "worker1", "worker2", "worker3"]

        def dispatcher(d_id):
            for seq in range(dispatches_per_thread):
                target = targets[(d_id + seq) % len(targets)]
                payload = {
                    "title": f"Mission {d_id}-{seq}",
                    "overview": f"Spec for mission {d_id}-{seq} with Thai text ภารกิจเร่งด่วน",
                    "target_worker": target,
                    "worker1": f"Subtask 1 for {d_id}-{seq}",
                    "worker2": f"Subtask 2 for {d_id}-{seq}",
                    "worker3": f"Subtask 3 for {d_id}-{seq}",
                }
                client.post_dispatch(payload)
                time.sleep(0.01)

        poll_threads = [threading.Thread(target=poller, daemon=True) for _ in range(num_pollers)]
        for pt in poll_threads:
            pt.start()

        disp_threads = [threading.Thread(target=dispatcher, args=(i,), daemon=True) for i in range(num_dispatchers)]
        for dt in disp_threads:
            dt.start()

        for dt in disp_threads:
            dt.join(timeout=20)

        time.sleep(0.1)
        stop_event.set()

        for pt in poll_threads:
            pt.join(timeout=5)

        print(f"[TEST 2] Total status polls completed: {poll_count[0]}")
        print(f"[TEST 2] Empty / truncated board reads observed by client: {len(empty_board_reads)}")

        # Check if BOARD.md on disk is valid UTF-8
        try:
            disk_content = board_file.read_text(encoding="utf-8")
            valid_utf8 = True
        except UnicodeDecodeError as ude:
            valid_utf8 = False
            print(f"[TEST 2] CRITICAL BUG: BOARD.md corrupted on disk: {ude}")

        print(f"[TEST 2] BOARD.md valid UTF-8 on disk: {valid_utf8}")

        if len(empty_board_reads) > 0 or not valid_utf8:
            print(f"[TEST 2] VULNERABILITY CONFIRMED: Discovered race condition! Clients received empty/corrupted board {len(empty_board_reads)} times.")
            return False, len(empty_board_reads), valid_utf8
        else:
            print("[TEST 2] No race condition observed in this specific run.")
            return True, 0, True


if __name__ == "__main__":
    test_chat_50_threads_zero_loss()
    race_detected, empty_count, valid_utf8 = test_concurrent_dispatch_board_race_condition()
    if not race_detected or empty_count > 0:
        print("\nSUMMARY: EMPIRICAL STRESS TESTING REVEALS CONCURRENCY BUG IN app.py:")
        print(f"- Empty board reads during polling: {empty_count}")
        print(f"- Valid UTF-8 on disk: {valid_utf8}")
        sys.exit(1)
    else:
        sys.exit(0)
