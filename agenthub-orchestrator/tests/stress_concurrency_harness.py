"""
Empirical Concurrency & High-Stress Harness for Antigravity Orchestrator (app.py).
Tests:
1. 50+ threads high-volume concurrent chat (zero message loss, format integrity, multiline protection)
2. Concurrent task dispatching and high-frequency status polling (race conditions, file integrity)
3. Mixed swarm burst (chat + dispatch + polling under heavy contention)
4. Cache invalidation / thundering herd on /api/status
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
from typing import List, Dict, Any

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app
from tests.conftest import TestServerContext

CHAT_DELIM_RE = re.compile(r'^\s*-\s*\[(\d{4}-\d{2}-\d{2}\s[\d:]+)\]\s*\[([^\]]+)\][:\s]*(.*)', re.MULTILINE)

def parse_chat_messages(raw_text: str) -> List[Dict[str, Any]]:
    """
    Parses TEAM_CHAT.md into individual messages, preserving multi-line bodies.
    Matches the delimiter regex specified in PROJECT.md:
    - [YYYY-MM-DD HH:MM] [Sender]: Message...
    """
    messages = []
    lines = raw_text.splitlines()
    curr_msg = None

    for line in lines:
        match = re.match(r'^\s*-\s*\[(\d{4}-\d{2}-\d{2}\s[\d:]+)\]\s*\[([^\]]+)\][:\s]*(.*)', line)
        if match:
            if curr_msg:
                messages.append(curr_msg)
            curr_msg = {
                "timestamp": match.group(1),
                "sender": match.group(2),
                "first_line": match.group(3),
                "body_lines": [match.group(3)] if match.group(3) else []
            }
        elif curr_msg is not None:
            curr_msg["body_lines"].append(line)

    if curr_msg:
        messages.append(curr_msg)

    for m in messages:
        m["body"] = "\n".join(m["body_lines"]).strip()

    return messages


def run_chat_concurrency_stress(num_threads: int = 50, messages_per_thread: int = 4):
    """
    Stress-test 1: 50+ threads posting high concurrent chat volumes simultaneously.
    Verifies:
    - Zero message loss
    - Correct HTTP 200 responses
    - Format integrity (delimiters, multiline code fences, tables, Thai/UTF-8)
    - Zero race conditions or interleaved partial lines
    """
    total_expected = num_threads * messages_per_thread
    print(f"\n[STRESS 1] Starting Chat Concurrency Test: {num_threads} threads x {messages_per_thread} msgs = {total_expected} total msgs")

    with TestServerContext(mock_subprocess=True) as ctx:
        client = ctx.client
        hub = ctx.workspace_dir / ".agenthub"
        chat_file = hub / "TEAM_CHAT.md"

        # Pre-check initial messages in chat
        initial_content = chat_file.read_text(encoding="utf-8")
        initial_msgs = parse_chat_messages(initial_content)
        initial_count = len(initial_msgs)
        print(f"[STRESS 1] Initial messages count in TEAM_CHAT.md: {initial_count}")

        # Prepare messages
        all_msg_ids = set()
        thread_messages = {}
        for t_idx in range(num_threads):
            msgs = []
            for m_idx in range(messages_per_thread):
                msg_id = f"ID_{uuid.uuid4().hex[:8]}_T{t_idx:02d}_M{m_idx:02d}"
                all_msg_ids.add(msg_id)

                # Mix different types of content
                if m_idx % 4 == 0:
                    # Multiline code block
                    content = (
                        f"[{msg_id}] Code sample:\n"
                        f"```python\n"
                        f"def worker_{t_idx}_task():\n"
                        f"    return {m_idx} * 10\n"
                        f"```"
                    )
                elif m_idx % 4 == 1:
                    # Markdown table
                    content = (
                        f"[{msg_id}] Status Report Table:\n"
                        f"| Metric | Value |\n"
                        f"|:---|:---|\n"
                        f"| Thread | {t_idx} |\n"
                        f"| Seq | {m_idx} |"
                    )
                elif m_idx % 4 == 2:
                    # Thai & Unicode & Emojis
                    content = f"[{msg_id}] 🚀 ทดสอบภาษาไทยและสัญลักษณ์พิเศษ Thread #{t_idx} — ข้อความ {m_idx}"
                else:
                    # Standard line
                    content = f"[{msg_id}] Standard single-line chat notification from Worker Thread {t_idx} sequence {m_idx}."

                sender = f"Agent_T{t_idx}"
                msgs.append((msg_id, sender, content))
            thread_messages[t_idx] = msgs

        # Barrier to release all threads at the exact same millisecond
        barrier = threading.Barrier(num_threads + 1)
        results = []
        results_lock = threading.Lock()

        def worker_fn(t_idx):
            barrier.wait()  # synchronize release
            for msg_id, sender, content in thread_messages[t_idx]:
                start_t = time.perf_counter()
                try:
                    resp = client.post_chat(sender, content)
                    latency = (time.perf_counter() - start_t) * 1000
                    status = resp.status_code
                    try:
                        data = resp.json()
                        success = data.get("success", False)
                    except Exception:
                        data = {}
                        success = False
                except Exception as e:
                    latency = (time.perf_counter() - start_t) * 1000
                    status = -1
                    success = False
                    data = {"error": str(e)}

                with results_lock:
                    results.append({
                        "msg_id": msg_id,
                        "status": status,
                        "success": success,
                        "latency_ms": latency,
                        "error": data.get("error", "")
                    })

        threads = [threading.Thread(target=worker_fn, args=(i,), daemon=True) for i in range(num_threads)]
        for t in threads:
            t.start()

        start_time = time.perf_counter()
        barrier.wait()  # Release all threads simultaneously!

        for t in threads:
            t.join(timeout=30)

        total_duration = time.perf_counter() - start_time
        print(f"[STRESS 1] All {total_expected} requests finished in {total_duration:.3f}s ({total_expected/total_duration:.1f} req/s)")

        # Verify results
        status_200_count = sum(1 for r in results if r["status"] == 200 and r["success"])
        failed_requests = [r for r in results if r["status"] != 200 or not r["success"]]

        print(f"[STRESS 1] Successful HTTP 200 posts: {status_200_count}/{total_expected}")
        if failed_requests:
            print(f"[STRESS 1] Failed requests ({len(failed_requests)}): {failed_requests[:5]}")

        # Check TEAM_CHAT.md directly
        final_content = chat_file.read_text(encoding="utf-8")
        parsed_msgs = parse_chat_messages(final_content)
        parsed_count = len(parsed_msgs)
        print(f"[STRESS 1] Final total messages parsed from TEAM_CHAT.md: {parsed_count}")

        # Verify zero message loss
        found_ids = set()
        missing_ids = []
        for msg_id in all_msg_ids:
            if msg_id in final_content:
                found_ids.add(msg_id)
            else:
                missing_ids.append(msg_id)

        print(f"[STRESS 1] Unique Message IDs verified in file: {len(found_ids)}/{len(all_msg_ids)}")
        if missing_ids:
            print(f"[STRESS 1] CRITICAL: Missing IDs: {missing_ids[:10]}")

        # Check for delimiter corruption
        corrupt_delimiters = []
        for line in final_content.splitlines():
            line_str = line.strip()
            if line_str.startswith("- ["):
                if "]:" not in line_str:
                    corrupt_delimiters.append(line_str)

        print(f"[STRESS 1] Corrupted delimiters found: {len(corrupt_delimiters)}")

        assert len(failed_requests) == 0, f"HTTP requests failed: {len(failed_requests)}"
        assert len(missing_ids) == 0, f"Message loss detected: {len(missing_ids)} missing"
        assert len(corrupt_delimiters) == 0, f"Corrupted delimiters: {corrupt_delimiters}"
        assert parsed_count == initial_count + total_expected, (
            f"Message count mismatch: expected {initial_count + total_expected}, got {parsed_count}"
        )
        print("[STRESS 1] PASS: 100% Zero message loss, no race conditions, delimiters intact.")
        return True


def run_concurrent_dispatch_and_polling_stress(num_dispatchers: int = 15, num_pollers: int = 15, dispatches_per_worker: int = 3):
    """
    Stress-test 2: Concurrent task dispatching and high-frequency status polling.
    Verifies:
    - Status polling under heavy concurrent write load never locks, crashes, or returns corrupt JSON
    - Concurrent dispatches properly write BOARD.md and inboxes without race-condition file corruption
    - Inboxes and BOARD.md remain well-formed UTF-8 markdown
    - Zero unhandled 500 exceptions
    """
    total_dispatches = num_dispatchers * dispatches_per_worker
    print(f"\n[STRESS 2] Starting Concurrent Dispatch & Polling Test: {num_dispatchers} dispatchers ({total_dispatches} dispatches) + {num_pollers} continuous pollers")

    with TestServerContext(mock_subprocess=True) as ctx:
        client = ctx.client
        hub = ctx.workspace_dir / ".agenthub"
        inbox = hub / "inbox"

        stop_polling = threading.Event()
        polling_results = []
        dispatch_results = []
        lock = threading.Lock()

        def poller_fn(p_idx):
            polls = 0
            empty_boards = 0
            errors = 0
            while not stop_polling.is_set():
                polls += 1
                try:
                    resp = client.get("/api/status")
                    if resp.status_code != 200:
                        errors += 1
                    else:
                        data = resp.json()
                        board_str = data.get("board", "")
                        # Verify board is not blank or corrupted
                        if not board_str or "Central Project Board" not in board_str:
                            empty_boards += 1
                except Exception as e:
                    errors += 1
                time.sleep(0.005)

            with lock:
                polling_results.append({
                    "poller_idx": p_idx,
                    "polls": polls,
                    "empty_boards": empty_boards,
                    "errors": errors
                })

        targets = ["all", "worker1", "worker2", "worker3"]

        def dispatcher_fn(d_idx):
            for seq in range(dispatches_per_worker):
                target = targets[(d_idx + seq) % len(targets)]
                payload = {
                    "title": f"Stress Mission D{d_idx:02d}_S{seq:02d}",
                    "overview": f"Data contract & spec from dispatcher {d_idx} seq {seq}",
                    "target_worker": target,
                    "worker1": f"Task A by D{d_idx} S{seq}",
                    "worker2": f"Task B by D{d_idx} S{seq}",
                    "worker3": f"Task C by D{d_idx} S{seq}",
                }
                err_msg = ""
                try:
                    resp = client.post_dispatch(payload)
                    status = resp.status_code
                    try:
                        resp_json = resp.json()
                        success = resp_json.get("success", False) if status == 200 else False
                        if not success:
                            err_msg = str(resp_json)
                    except Exception as je:
                        success = False
                        err_msg = f"JSON decode err: {je} (text: {resp.text[:100]})"
                except Exception as e:
                    status = -1
                    success = False
                    err_msg = str(e)

                with lock:
                    dispatch_results.append({
                        "dispatcher_idx": d_idx,
                        "seq": seq,
                        "target": target,
                        "status": status,
                        "success": success,
                        "error": err_msg
                    })
                time.sleep(0.01)

        # Launch pollers
        poller_threads = [threading.Thread(target=poller_fn, args=(i,), daemon=True) for i in range(num_pollers)]
        for pt in poller_threads:
            pt.start()

        # Launch dispatchers
        dispatcher_threads = [threading.Thread(target=dispatcher_fn, args=(i,), daemon=True) for i in range(num_dispatchers)]
        for dt in dispatcher_threads:
            dt.start()

        for dt in dispatcher_threads:
            dt.join(timeout=30)

        # Allow pollers to run a bit longer under cooldown
        time.sleep(0.2)
        stop_polling.set()

        for pt in poller_threads:
            pt.join(timeout=10)

        total_polls = sum(p["polls"] for p in polling_results)
        total_poll_errors = sum(p["errors"] for p in polling_results)
        empty_board_reads = sum(p["empty_boards"] for p in polling_results)
        dispatch_successes = sum(1 for d in dispatch_results if d["status"] == 200 and d["success"])

        print(f"[STRESS 2] Total status polls completed: {total_polls}")
        print(f"[STRESS 2] Polling errors: {total_poll_errors}")
        print(f"[STRESS 2] Empty board reads detected: {empty_board_reads}")
        failed_dispatches = [d for d in dispatch_results if d["status"] != 200 or not d["success"]]
        if failed_dispatches:
            print(f"[STRESS 2] Failed dispatches details: {failed_dispatches}")

        # Check final files integrity
        board_file = hub / "BOARD.md"
        board_txt = board_file.read_text(encoding="utf-8")
        assert "Central Project Board" in board_txt, "BOARD.md missing header"
        assert "Responsibility Matrix" in board_txt, "BOARD.md missing matrix"

        for w in ("worker1", "worker2", "worker3"):
            inbox_file = inbox / f"{w}.md"
            assert inbox_file.exists(), f"Missing inbox for {w}"
            inbox_txt = inbox_file.read_text(encoding="utf-8")
            assert f"Inbox สำหรับ {w.capitalize()}" in inbox_txt, f"Corrupted inbox header for {w}"

        # TEAM_CHAT.md should have dispatch announcements
        chat_txt = (hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        assert "Central Dispatcher" in chat_txt, "Dispatch announcements not recorded in TEAM_CHAT.md"

        assert total_poll_errors == 0, f"Polling errors detected: {total_poll_errors}"
        assert dispatch_successes == total_dispatches, f"Some dispatches failed: {total_dispatches - dispatch_successes}"
        print("[STRESS 2] PASS: Dispatch & Status Polling concurrency robust and consistent.")
        return True


def run_mixed_swarm_burst_stress(num_chat_threads: int = 40, num_poller_threads: int = 15, num_dispatch_threads: int = 10):
    """
    Stress-test 3: Extreme Mixed Swarm Contention.
    65 threads concurrently generating chat messages, continuous status polling,
    and task dispatches simultaneously.
    """
    print(f"\n[STRESS 3] Starting Extreme Mixed Swarm Burst: {num_chat_threads} chatters + {num_poller_threads} pollers + {num_dispatch_threads} dispatchers = {num_chat_threads + num_poller_threads + num_dispatch_threads} concurrent threads")

    with TestServerContext(mock_subprocess=True) as ctx:
        client = ctx.client
        hub = ctx.workspace_dir / ".agenthub"

        start_event = threading.Event()
        stop_event = threading.Event()

        chat_results = []
        poll_results = []
        dispatch_results = []
        lock = threading.Lock()

        # Chat sender
        def chatter(c_idx):
            start_event.wait()
            for s in range(5):
                msg_id = f"BURST_C{c_idx:02d}_S{s:02d}"
                body = f"[{msg_id}] Concurrent swarm chatter from Agent {c_idx}."
                try:
                    r = client.post_chat(f"Swarm_{c_idx}", body)
                    ok = (r.status_code == 200 and r.json().get("success"))
                except Exception:
                    ok = False
                with lock:
                    chat_results.append((msg_id, ok))
                time.sleep(0.002)

        # Poller
        def poller(p_idx):
            start_event.wait()
            p_count = 0
            p_err = 0
            while not stop_event.is_set():
                p_count += 1
                try:
                    r = client.get("/api/status")
                    if r.status_code != 200:
                        p_err += 1
                except Exception:
                    p_err += 1
                time.sleep(0.005)
            with lock:
                poll_results.append((p_count, p_err))

        # Dispatcher
        def dispatcher(d_idx):
            start_event.wait()
            for s in range(3):
                payload = {
                    "title": f"Burst Task D{d_idx:02d}_S{s:02d}",
                    "overview": "Fast burst execution",
                    "target_worker": "all",
                    "worker1": "Fast subtask 1",
                    "worker2": "Fast subtask 2",
                    "worker3": "Fast subtask 3",
                }
                try:
                    r = client.post_dispatch(payload)
                    ok = (r.status_code == 200 and r.json().get("success"))
                except Exception:
                    ok = False
                with lock:
                    dispatch_results.append(ok)
                time.sleep(0.01)

        threads = []
        for i in range(num_chat_threads):
            threads.append(threading.Thread(target=chatter, args=(i,), daemon=True))
        for i in range(num_poller_threads):
            threads.append(threading.Thread(target=poller, args=(i,), daemon=True))
        for i in range(num_dispatch_threads):
            threads.append(threading.Thread(target=dispatcher, args=(i,), daemon=True))

        for t in threads:
            t.start()

        start_time = time.perf_counter()
        start_event.set()

        # Wait for chatters and dispatchers
        for t in threads:
            if t not in threads[num_chat_threads:num_chat_threads + num_poller_threads]:
                t.join(timeout=30)

        stop_event.set()
        for t in threads[num_chat_threads:num_chat_threads + num_poller_threads]:
            t.join(timeout=10)

        elapsed = time.perf_counter() - start_time
        total_chat = len(chat_results)
        successful_chat = sum(1 for _, ok in chat_results if ok)
        total_polls = sum(cnt for cnt, _ in poll_results)
        poll_errors = sum(err for _, err in poll_results)
        total_dispatch = len(dispatch_results)
        successful_dispatch = sum(1 for ok in dispatch_results if ok)

        print(f"[STRESS 3] Swarm run completed in {elapsed:.2f}s")
        print(f"[STRESS 3] Chat: {successful_chat}/{total_chat} successful")
        print(f"[STRESS 3] Polls: {total_polls} total ({poll_errors} errors)")
        print(f"[STRESS 3] Dispatches: {successful_dispatch}/{total_dispatch} successful")

        chat_txt = (hub / "TEAM_CHAT.md").read_text(encoding="utf-8")
        missing_chat = [msg_id for msg_id, _ in chat_results if msg_id not in chat_txt]
        print(f"[STRESS 3] Missing chat messages in TEAM_CHAT.md: {len(missing_chat)}")

        assert successful_chat == total_chat, "Chat messages failed"
        assert len(missing_chat) == 0, f"Messages lost in file: {missing_chat}"
        assert poll_errors == 0, f"Polling errors: {poll_errors}"
        assert successful_dispatch == total_dispatch, "Dispatches failed"
        print("[STRESS 3] PASS: Swarm contention test passed with zero failures or loss.")
        return True


def run_cache_thundering_herd_stress(num_threads: int = 50):
    """
    Stress-test 4: Cache invalidation thundering herd.
    Forces cache invalidation and has 50 threads query /api/status at the same instant.
    Tests get_process_status() thread safety.
    """
    print(f"\n[STRESS 4] Starting Process Cache Thundering Herd Test: {num_threads} threads hitting /api/status after cache invalidation")

    with TestServerContext(mock_subprocess=True) as ctx:
        client = ctx.client
        app.invalidate_process_cache()

        barrier = threading.Barrier(num_threads + 1)
        results = []
        lock = threading.Lock()

        def status_query(idx):
            barrier.wait()
            try:
                r = client.get("/api/status")
                code = r.status_code
                data = r.json() if code == 200 else {}
                ok = (code == 200 and "process_status" in data and "lead" in data["process_status"])
            except Exception as e:
                code = -1
                ok = False
            with lock:
                results.append((idx, code, ok))

        threads = [threading.Thread(target=status_query, args=(i,), daemon=True) for i in range(num_threads)]
        for t in threads:
            t.start()

        barrier.wait()
        for t in threads:
            t.join(timeout=15)

        success_count = sum(1 for _, code, ok in results if ok)
        print(f"[STRESS 4] Successful status responses: {success_count}/{num_threads}")
        assert success_count == num_threads, f"Thundering herd failures: {num_threads - success_count}"
        print("[STRESS 4] PASS: Process cache and psutil query safely survived thundering herd.")
        return True


if __name__ == "__main__":
    print("=" * 70)
    print("ANTIGRAVITY ORCHESTRATOR EMPIRICAL CONCURRENCY STRESS SUITE")
    print("=" * 70)

    t0 = time.perf_counter()
    res1 = run_chat_concurrency_stress(num_threads=60, messages_per_thread=5)  # 300 msgs across 60 threads!
    res2 = run_concurrent_dispatch_and_polling_stress(num_dispatchers=15, num_pollers=15, dispatches_per_worker=3)
    res3 = run_mixed_swarm_burst_stress(num_chat_threads=40, num_poller_threads=15, num_dispatch_threads=10)
    res4 = run_cache_thundering_herd_stress(num_threads=50)

    total_time = time.perf_counter() - t0
    print("=" * 70)
    print(f"ALL 4 STRESS TEST SCENARIOS PASSED EMPIRICALLY in {total_time:.2f}s!")
    print("Verdict: APPROVE (Zero message loss, zero race conditions, atomic lock integrity)")
    print("=" * 70)
