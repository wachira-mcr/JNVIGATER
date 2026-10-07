#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Antigravity Multi-Agent Commander — Desktop App
Bundles the HTTP server + pywebview window into a single app.py

Usage:
  python app.py
"""

import os
import sys
import re
import json
import time
import threading
_browser_lock = threading.Lock()
import subprocess
import mimetypes
import tkinter as tk
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.parse
import accounts
from datetime import datetime

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("[WARN] psutil not found – process monitoring disabled")

try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False
    print("[INFO] pywebview not found – falling back to browser mode")

# Pre-register web MIME types to prevent Windows registry quirks
mimetypes.init()
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/ttf", ".ttf")

# ── Configuration ──────────────────────────────────────────────────────────────
PORT = 8989
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = BASE_DIR
CONFIG_FILE = BASE_DIR / "config.json"
PROGRAMS_DIR = Path(r"C:\Users\MBx13\AppData\Local\Programs")

# We use the original Antigravity for Lead, and the clones for Workers
PROFILES = {
    "lead":    {"id": "lead",    "name": "Team Lead (เมลหลัก)", "role": "Orchestrator & Final QA",   "color": "amber",   "exe": str(PROGRAMS_DIR / "antigravity" / "Antigravity.exe")},
    "worker1": {"id": "worker1", "name": "Worker 1 (เมลที่ 2)", "role": "Backend & Core",            "color": "blue",    "exe": str(PROGRAMS_DIR / "antigravity-worker1" / "Worker1.exe")},
    "worker2": {"id": "worker2", "name": "Worker 2 (เมลที่ 3)", "role": "Frontend & UI",             "color": "emerald", "exe": str(PROGRAMS_DIR / "antigravity-worker2" / "Worker2.exe")},
    "worker3": {"id": "worker3", "name": "Worker 3 (เมลที่ 4)", "role": "Testing & Docs",            "color": "purple",  "exe": str(PROGRAMS_DIR / "antigravity-worker3" / "Worker3.exe")},
}

# ── Process Caching & Monitoring ──────────────────────────────────────────────
_process_cache = {"time": 0.0, "data": None}
_process_cache_lock = threading.Lock()

def invalidate_process_cache():
    """Forces next get_process_status call to perform a fresh OS process scan."""
    global _process_cache
    with _process_cache_lock:
        _process_cache["time"] = 0.0
        _process_cache["data"] = None

def get_process_status(max_age_seconds: float = 1.0) -> dict:
    """
    Returns live process status for all defined agent profiles using psutil:
    {
        "lead": {"running": bool, "pid": Optional[int]},
        "worker1": {"running": bool, "pid": Optional[int]},
        "worker2": {"running": bool, "pid": Optional[int]},
        "worker3": {"running": bool, "pid": Optional[int]}
    }
    Identifies the main process (excluding Electron child/renderer/utility workers).
    """
    global _process_cache
    now = time.time()
    with _process_cache_lock:
        if _process_cache["data"] is not None and (now - _process_cache["time"]) < max_age_seconds:
            return _process_cache["data"]

    status = {
        agent_id: {"running": False, "pid": None}
        for agent_id in PROFILES
    }

    if not HAS_PSUTIL:
        return status

    # Build lookup maps for normalized absolute paths and binary basenames
    target_exes = {}
    target_names = {}
    for agent_id, prof in PROFILES.items():
        exe_str = prof.get("exe", "")
        if exe_str:
            target_exes[agent_id] = os.path.normcase(os.path.abspath(exe_str))
            target_names[agent_id] = Path(exe_str).name.lower()

    candidates = {agent_id: [] for agent_id in PROFILES}

    try:
        for proc in psutil.process_iter(["pid", "name", "exe", "ppid", "create_time", "cmdline"]):
            try:
                p_info = proc.info
                p_exe = p_info.get("exe")
                p_name = p_info.get("name") or ""
                norm_exe = os.path.normcase(os.path.abspath(p_exe)) if p_exe else ""

                for agent_id in PROFILES:
                    matched = False
                    if norm_exe and norm_exe == target_exes.get(agent_id):
                        matched = True
                    elif not norm_exe and p_name.lower() == target_names.get(agent_id):
                        matched = True

                    if matched:
                        candidates[agent_id].append(p_info)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as e:
        print(f"[WARN] Error scanning processes with psutil: {e}")

    # Select the main process PID for each agent profile
    for agent_id, procs in candidates.items():
        if not procs:
            status[agent_id] = {"running": False, "pid": None}
            continue

        # Strategy 1: Candidate without '--type=' in cmdline
        main_candidates = [
            p for p in procs
            if p.get("cmdline") and not any(arg.startswith("--type=") for arg in p["cmdline"])
        ]

        if main_candidates:
            chosen = min(main_candidates, key=lambda x: x.get("create_time") or float("inf"))
        else:
            # Strategy 2: If cmdline inaccessible, candidate whose ppid is not another candidate
            all_pids = {p["pid"] for p in procs}
            root_candidates = [p for p in procs if p.get("ppid") not in all_pids]
            if root_candidates:
                chosen = min(root_candidates, key=lambda x: x.get("create_time") or float("inf"))
            else:
                # Strategy 3: Earliest creation time
                chosen = min(procs, key=lambda x: x.get("create_time") or float("inf"))

        status[agent_id] = {
            "running": True,
            "pid": chosen["pid"]
        }

    with _process_cache_lock:
        _process_cache["time"] = time.time()
        _process_cache["data"] = status

    return status

# ── Synchronization & File IPC ────────────────────────────────────────────────
_chat_lock = threading.Lock()

def read_file(path: Path, default="") -> str:
    """Read file with UTF-8 encoding and fallback replacement characters."""
    if path.exists():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            pass
    return default

def append_chat(hub: Path, sender: str, message: str, now: str = None) -> tuple[bool, str]:
    """
    Thread-safe atomic append to TEAM_CHAT.md.
    Guarantees:
    - Serialized writes across concurrent request threads via threading.Lock
    - Append mode ('a') with UTF-8 encoding (no file truncation or lost updates)
    - Delimiter formatted as: - [YYYY-MM-DD HH:MM] [Sender]: Message
    - Multiline messages properly preserved
    - Immediate OS-level flush and fsync
    """
    if not message or not message.strip():
        return False, "Empty message"

    if not now:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

    clean_msg = message.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not clean_msg.strip():
        return False, "Empty message"

    clean_sender = re.sub(r'[\r\n\[\]]', '', sender).strip() if sender else "You"
    if not clean_sender:
        clean_sender = "You"

    if clean_msg.startswith("```") or clean_msg.startswith("|") or clean_msg.startswith("#"):
        entry = f"- [{now}] [{clean_sender}]:\n{clean_msg}\n"
    else:
        entry = f"- [{now}] [{clean_sender}]: {clean_msg}\n"

    chat_file = hub / "TEAM_CHAT.md"

    with _chat_lock:
        try:
            chat_file.parent.mkdir(parents=True, exist_ok=True)

            needs_newline = False
            if chat_file.exists() and chat_file.stat().st_size > 0:
                try:
                    with open(chat_file, "rb") as rf:
                        rf.seek(-1, os.SEEK_END)
                        if rf.read(1) != b"\n":
                            needs_newline = True
                except Exception:
                    pass

            with open(chat_file, "a", encoding="utf-8") as f:
                if needs_newline:
                    f.write("\n")
                f.write(entry)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass
            return True, ""
        except (PermissionError, OSError) as e:
            return False, str(e)

# ── Config helpers ─────────────────────────────────────────────────────────────
def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"workspace": str(DEFAULT_WORKSPACE)}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

def get_workspace() -> Path:
    ws = Path(load_config().get("workspace", str(DEFAULT_WORKSPACE)))
    _ensure_agenthub(ws)
    return ws

def _ensure_agenthub(ws: Path):
    """Safely initializes .agenthub and inboxes with default templates."""
    try:
        if not ws:
            return
        ws = Path(ws).resolve()
        hub = ws / ".agenthub"
        inbox = hub / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        defaults = {
            "BOARD.md": (
                "# 📋 Central Project Board\n\n"
                "> **สถานะ:** 🟢 พร้อมรับงาน\n"
                f"> **อัปเดต:** {now}\n\n"
                "---\n\n"
                "## 📐 Data Contract & Overview\n"
                "*(ยังไม่มีคำสั่ง — กำหนดงานผ่านหน้า Dispatch Tasks)*\n\n"
                "---\n\n"
                "## 📊 Responsibility Matrix\n\n"
                "| Agent | บัญชี | ขอบเขตงาน | สถานะ |\n"
                "|:---|:---|:---|:---|\n"
                "| 👑 Team Lead | เมลหลัก | กำกับดูแล, ตรวจสอบ | ⚪ รอเริ่มงาน |\n"
                "| 🛠️ Worker 1 | เมลที่ 2 | Backend & Core | ⚪ รอคำสั่ง |\n"
                "| 🎨 Worker 2 | เมลที่ 3 | Frontend & UI | ⚪ รอคำสั่ง |\n"
                "| 🧪 Worker 3 | เมลที่ 4 | Testing & Docs | ⚪ รอคำสั่ง |\n"
            ),
            "TEAM_CHAT.md": (
                "# 💬 Swarm Team Live Chat (ห้องคุยงานระหว่าง Worker 1, 2, 3 และ Lead)\n\n"
                "> **วิธีใช้งานสำหรับ Agent:**\n"
                "> - เขียนบันทึกข้อความต่อท้ายไฟล์นี้เสมอเมื่อต้องการแจ้งเพื่อน ถามคำถาม หรือแชร์ Function Signature / Model\n"
                "> - รูปแบบ: - [YYYY-MM-DD HH:MM] [ชื่อผู้ส่ง]: ข้อความ\n"
                "> - ห้ามลบข้อความเก่าของเพื่อน\n\n"
                "---\n\n"
                "### 📜 ประวัติการสนทนา (Chat Log)\n\n"
                f"- [{now}] [System]: เริ่มต้นระบบห้องแชทกลาง Multi-Agent Swarm พร้อมทำงาน\n"
            ),
            "inbox/lead_review.md": (
                "# 🔍 Final Review Inbox สำหรับ Team Lead\n\n"
                "*(ยังไม่มีงานส่งเข้ามา — เมื่อ Worker ทำงานเสร็จ ให้สรุปผลงานส่งที่ไฟล์นี้)*\n"
            ),
        }
        for name, content in defaults.items():
            p = hub / name
            if not p.exists() or p.stat().st_size == 0:
                p.write_text(content, encoding="utf-8")

        roles = {
            "worker1": "Backend & Core",
            "worker2": "Frontend & UI",
            "worker3": "Testing & QA"
        }
        for w, role in roles.items():
            p = inbox / f"{w}.md"
            if not p.exists() or p.stat().st_size == 0:
                p.write_text(
                    f"# 📥 Inbox สำหรับ {w.capitalize()} ({role})\n\n"
                    f"> **สถานะ:** ⚪ รอรับคำสั่งใหม่  \n"
                    f"> **มอบหมายเมื่อ:** {now}\n\n---\n\n"
                    f"### 📝 งานที่ต้องทำ:\n*(ยังไม่มีคำสั่งเฉพาะ — รอ Lead สั่งการ)*\n\n---\n\n"
                    f"### 📌 ขั้นตอน:\n"
                    f"1. ประกาศเริ่มงานใน `.agenthub/TEAM_CHAT.md`\n"
                    f"2. ส่งงานที่ `.agenthub/inbox/lead_review.md` เมื่อเสร็จ\n",
                    encoding="utf-8")
    except (PermissionError, OSError) as e:
        print(f"[ERROR] Failed to ensure .agenthub at {ws}: {e}")

    return False

# Each agent exposes DevTools on its own port (agent_kick.py --probe <port>).
ACCOUNT_SWAP_WAIT = 15  # seconds
CDP_PORTS = {"lead": 9331, "worker1": 9332, "worker2": 9333, "worker3": 9334}

# ── Git Worktree Isolation ────────────────────────────────────────────────────
# Off the user profile on purpose: each worker runs as its own Windows account,
# and granting them access here is cleaner than opening up C:\Users\MBx13.
WORKTREE_ROOT = Path(r"D:\agents\worktrees")

def _git(args, cwd) -> subprocess.CompletedProcess:
    """Run git, never raise. Any failure (git missing, timeout, patched subprocess
    in tests) comes back as a non-zero result so callers fall back to the plain workspace."""
    kwargs = {"cwd": str(cwd), "capture_output": True, "text": True, "timeout": 120}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        return subprocess.run(["git", *args], **kwargs)
    except Exception as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))

def ensure_worktree(agent_id: str, ws: Path) -> Path:
    """
    Give each worker its own git worktree on branch agent/<id>, so three agents can
    edit the same project without clobbering each other. Returns the directory the
    agent should open — the worktree's equivalent of `ws`, or `ws` itself when the
    workspace is not a git repo (or git fails).
    """
    ws = Path(ws)
    if agent_id == "lead":
        return ws
    r = _git(["rev-parse", "--show-toplevel"], ws)
    if r.returncode != 0:
        return ws
    repo = Path(r.stdout.strip())
    try:
        rel = ws.resolve().relative_to(repo.resolve())
    except ValueError:
        rel = Path(".")

    wt = WORKTREE_ROOT / agent_id
    if not wt.is_dir():
        wt.parent.mkdir(parents=True, exist_ok=True)
        r = _git(["worktree", "add", "-B", f"agent/{agent_id}", str(wt)], repo)
        if r.returncode != 0 or not wt.is_dir():
            print(f"[WARN] worktree for {agent_id} failed: {r.stderr.strip()}")
            return ws

    target = (wt / rel) if str(rel) != "." else wt
    return target if target.is_dir() else wt

def write_agent_rules(agent_id: str, agent_dir: Path, hub: Path):
    """
    Drop a rules file in the agent's workspace so it reads its own inbox on the
    first message instead of waiting to be briefed by hand. Hub paths are absolute
    because the worktree does not contain the shared .agenthub.
    """
    prof = PROFILES.get(agent_id, {})
    body = (
        f"# คำสั่งประจำตัว: {prof.get('name', agent_id)}\n\n"
        f"คุณคือ **{agent_id}** หน้าที่ **{prof.get('role', '')}** ในทีม Multi-Agent Swarm\n\n"
        f"## เริ่มงานทุกครั้ง ให้ทำตามนี้ก่อนเสมอ\n"
        f"1. อ่านกระดานงานกลาง: `{hub / 'BOARD.md'}`\n"
        f"2. อ่านใบสั่งงานของคุณ: `{hub / 'inbox' / (agent_id + '.md')}`\n"
        f"3. อ่านห้องแชททีม: `{hub / 'TEAM_CHAT.md'}` เพื่อดูว่าเพื่อนทำอะไรไปแล้ว\n"
        f"4. ประกาศเริ่มงานโดย **ต่อท้าย** ไฟล์แชท ด้วยรูปแบบ\n"
        f"   `- [YYYY-MM-DD HH:MM] [{agent_id}]: ข้อความ`\n"
        f"   (ห้ามลบหรือแก้ข้อความเก่าของคนอื่น)\n\n"
        f"## ระหว่างทำงาน\n"
        f"- แก้โค้ดได้เฉพาะในโฟลเดอร์นี้ (`{agent_dir}`) ซึ่งอยู่บน branch `agent/{agent_id}` ของคุณเอง\n"
        f"- ก่อนสร้าง function / interface ใหม่ที่คนอื่นต้องใช้ ให้ประกาศใน TEAM_CHAT.md ก่อน\n"
        f"- commit ลง branch `agent/{agent_id}` เท่านั้น ห้าม checkout branch อื่น\n\n"
        f"## เมื่อเสร็จ\n"
        f"สรุปผลงาน (ไฟล์ที่แก้ + วิธีทดสอบ) ต่อท้ายไฟล์ `{hub / 'inbox' / 'lead_review.md'}`\n"
        f"แล้วแจ้งใน TEAM_CHAT.md ว่าส่งงานแล้ว\n"
    )
    for name in ("AGENTS.md", "GEMINI.md"):
        try:
            (agent_dir / name).write_text(body, encoding="utf-8")
        except OSError as e:
            print(f"[WARN] Cannot write {name} for {agent_id}: {e}")

# ── Agent Web URL & Port Discovery ───────────────────────────────────────────
def get_agent_web_url(agent_id: str):
    """Discovers the active loopback web UI URL for an agent profile from its main.log."""
    folder_map = {
        "lead": "Antigravity",
        "worker1": "Antigravity Worker 1",
        "worker2": "Antigravity Worker 2",
        "worker3": "Antigravity Worker 3",
    }
    folder_name = folder_map.get(agent_id)
    if not folder_name:
        return None
    # An agent started under its own Windows account logs into that account's profile.
    win_user = load_config().get("run_as", {}).get(agent_id)
    if win_user:
        appdata = str(Path(os.environ["SystemDrive"] + "\\Users") / win_user / "AppData" / "Roaming")
    else:
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    log_file = Path(appdata) / folder_name / "logs" / "main.log"
    if log_file.exists():
        try:
            text = log_file.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(r"Local:\s+(https?://127\.0\.0\.1:\d+/)", text)
            if matches:
                return matches[-1]
        except Exception:
            pass
    return None

def get_all_agent_web_urls() -> dict:
    """Returns a dict of all discovered web UI URLs for active agent profiles."""
    return {agent_id: get_agent_web_url(agent_id) for agent_id in PROFILES}

# ── Launch Antigravity (agent app) instances ──────────────────────────────────
def launch_instance(target: str, workspace: str, open_browser: bool = False) -> dict:
    """
    Open separate independent cloned Antigravity agent apps with workspace.
    Passes workspace directory as command-line argument and sets process cwd.
    Triggers double-launch window activation after 1.5s delay.
    Optionally opens the agent's web UI directly in the default browser.
    Returns status dict matching the PROJECT.md REST API contract.
    """
    ws_str = str(workspace) if workspace else str(BASE_DIR)
    creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

    # If unknown target is requested, safely return empty launched list per test_f07_launch_invalid_target_handled_safely
    if target != "all" and target not in PROFILES:
        return {
            "success": True,
            "launched": [],
            "failed": [target],
            "errors": {target: f"Unknown target '{target}'. Valid options are {list(PROFILES.keys())} or 'all'"},
            "web_urls": get_all_agent_web_urls()
        }

    # "all" means the agents that are actually signed in — config key "agents".
    active = [a for a in load_config().get("agents", list(PROFILES)) if a in PROFILES]
    targets = (active or list(PROFILES)) if target == "all" else [target]
    launched = []
    failed = []
    errors = {}

    import webbrowser

    for t in targets:
        exe_path = PROFILES[t]["exe"]
        exe_file = Path(exe_path)

        if not exe_file.is_file():
            failed.append(t)
            errors[t] = f"Executable not found at {exe_path}"
            continue

        # Each worker gets its own worktree/branch; lead stays on the main workspace.
        agent_dir = ensure_worktree(t, Path(ws_str))
        write_agent_rules(t, agent_dir, Path(ws_str) / ".agenthub")
        agent_ws = str(agent_dir)
        # The debugging port is how we send the agent its opening message later.
        cmd = [str(exe_file), f"--remote-debugging-port={CDP_PORTS[t]}", agent_ws]

        # Antigravity keeps its Google token in Windows Credential Manager under a
        # single key shared by every clone, so separate logins need separate Windows
        # accounts. config.json "run_as" maps an agent to the account that runs it.
        win_user = load_config().get("run_as", {}).get(t)
        if win_user:
            cmd = ["runas", "/savecred", f"/user:{win_user}",
                   subprocess.list2cmdline(cmd)]

        try:
            kwargs = {"cwd": agent_ws, "close_fds": True}
            if sys.platform == "win32":
                kwargs["creationflags"] = creation_flags

            # Shared token slot gets this agent's own Google account (accounts.py).
            swapped = accounts.use(t)

            # Launch 1: Starts the agent with workspace argument and cwd
            subprocess.Popen(cmd, **kwargs)

            # Launch 2: Trigger the 'second-instance' event to force the window to pop up!
            def _force_show_window(launch_cmd, target_cwd, agent_key, should_open_browser):
                time.sleep(1.5)
                try:
                    sub_kwargs = {"cwd": target_cwd, "close_fds": True}
                    if sys.platform == "win32":
                        sub_kwargs["creationflags"] = creation_flags
                    subprocess.Popen(launch_cmd, **sub_kwargs)
                except Exception as ex:
                    print(f"[WARN] Second-instance activation failed for {launch_cmd[0]}: {ex}")

                # If open_browser is requested or native window didn't steal focus, open URL in browser
                if should_open_browser:
                    # Wait up to 5 seconds for the URL to appear in logs
                    url = None
                    for _ in range(10):
                        time.sleep(0.5)
                        url = get_agent_web_url(agent_key)
                        if url: break
                        
                    if url:
                        # Map agent keys to specific Chrome profiles for separate tokens
                        profile_map = {
                            "lead": "Default",       # jimmy551
                            "worker1": "Profile 5",  # love
                            "worker2": "Profile 4",  # wachira
                            "worker3": "Profile 4"   # wachira (purple)
                        }
                        profile_dir = profile_map.get(agent_key, "Default")
                        
                        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
                        if not os.path.exists(chrome_path):
                            chrome_path = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                            
                        def launch_browser():
                            # We import threading here if not global, but it is global in app.py
                            with getattr(sys.modules[__name__], '_browser_lock', threading.Lock()):
                                if os.path.exists(chrome_path):
                                    try:
                                        subprocess.Popen([chrome_path, f"--profile-directory={profile_dir}", url])
                                    except Exception as e:
                                        print(f"[WARN] Failed to open Chrome: {e}")
                                        webbrowser.open(url)
                                else:
                                    webbrowser.open(url)
                                time.sleep(2.0) # Prevent concurrent Chrome profile launches from clashing
                                
                        threading.Thread(target=launch_browser, daemon=True).start()

            threading.Thread(
                target=_force_show_window,
                args=(cmd, agent_ws, t, open_browser),
                daemon=True
            ).start()

            launched.append(t)
            invalidate_process_cache()

            if swapped:
                # Hold this agent's token in the slot until both launches above (the
                # second fires at +1.5s and can restart the app) have read it.
                # ponytail: fixed wait; raise it if an agent opens on the wrong account
                time.sleep(ACCOUNT_SWAP_WAIT)
            elif len(targets) > 1:
                time.sleep(0.4)

        except Exception as e:
            failed.append(t)
            errors[t] = f"Failed to spawn process {exe_path}: {e}"
            print(f"[WARN] Failed to launch {t}: {e}")

    accounts.use("lead")  # leave the shared slot on the lead's account
    success = (len(failed) == 0) and (len(launched) > 0)
    return {
        "success": success,
        "launched": launched,
        "failed": failed,
        "errors": errors,
        "web_urls": get_all_agent_web_urls()
    }

# ── HTTP Request Handler ───────────────────────────────────────────────────────
class HubHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler – serves the HTML UI and the REST API."""

    def log_message(self, fmt, *args):
        code = str(args[1]) if len(args) > 1 else ""
        if "/api/status" in str(args[0]) or "/favicon.ico" in str(args[0]):
            return
        line = f"[HTTP] {args[0]} -> {code}\n"
        sys.stdout.buffer.write(line.encode("utf-8", errors="replace"))

    def _send_bytes(self, body: bytes, content_type: str = "application/json; charset=utf-8", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, body: bytes, content_type="application/json; charset=utf-8"):
        self._send_bytes(body, content_type=content_type, status=200)

    def _json(self, obj, status: int = 200):
        self._send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), content_type="application/json; charset=utf-8", status=status)

    def _error(self, status: int, message: str):
        self._json({"success": False, "error": message}, status=status)

    def do_OPTIONS(self):
        self._ok(b"")

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            # 1. API Endpoints
            if path == "/api/status":
                ws = get_workspace()
                hub = ws / ".agenthub"
                inbox = hub / "inbox"
                self._json({
                    "workspace": str(ws),
                    "profiles": PROFILES,
                    "process_status": get_process_status(),
                    "web_urls": get_all_agent_web_urls(),
                    "board":         read_file(hub / "BOARD.md"),
                    "chat":          read_file(hub / "TEAM_CHAT.md"),
                    "review":        read_file(inbox / "lead_review.md"),
                    "worker1_inbox": read_file(inbox / "worker1.md"),
                    "worker2_inbox": read_file(inbox / "worker2.md"),
                    "worker3_inbox": read_file(inbox / "worker3.md"),
                })
                return

            if path.startswith("/api/"):
                self.send_response(404)
                self.end_headers()
                return

            # Favicon fast-path if not on disk
            if path == "/favicon.ico":
                fav_file = BASE_DIR / "public" / "favicon.ico"
                if not fav_file.is_file():
                    self.send_response(204)
                    self.end_headers()
                    return

            # 2. Static Asset Resolution & Normalization
            if path in ("/", "/index.html"):
                target_rel = "index.html"
            elif path.startswith("/public/"):
                target_rel = path[len("/public/"):]
            else:
                target_rel = path.lstrip("/")

            if not target_rel:
                target_rel = "index.html"

            # 3. Path Traversal & Security Validation
            unquoted = urllib.parse.unquote(target_rel)
            if "\x00" in unquoted:
                self._error(400, "Bad Request: Invalid path characters")
                return

            public_dir = (BASE_DIR / "public").resolve()
            clean_rel = Path(unquoted.lstrip("/\\"))
            if clean_rel.is_absolute() or clean_rel.drive:
                self._error(403, "Forbidden: Invalid path")
                return

            try:
                target_file = (public_dir / clean_rel).resolve()
            except Exception:
                self._error(403, "Forbidden: Invalid path")
                return

            if not target_file.is_relative_to(public_dir):
                self._error(403, "Forbidden: Directory traversal denied")
                return

            # If directory requested, check for index.html inside it
            if target_file.is_dir():
                target_file = (target_file / "index.html").resolve()
                if not target_file.is_relative_to(public_dir):
                    self._error(403, "Forbidden: Directory traversal denied")
                    return

            if not target_file.is_file():
                self.send_response(404)
                self.end_headers()
                return

            # 4. MIME Type Detection & Encoding
            mime_type, _ = mimetypes.guess_type(str(target_file))
            if not mime_type:
                ext = target_file.suffix.lower()
                mime_fallbacks = {
                    ".html": "text/html",
                    ".htm": "text/html",
                    ".css": "text/css",
                    ".js": "application/javascript",
                    ".mjs": "application/javascript",
                    ".json": "application/json",
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".ico": "image/x-icon",
                    ".woff2": "font/woff2",
                    ".woff": "font/woff",
                    ".ttf": "font/ttf",
                }
                mime_type = mime_fallbacks.get(ext, "application/octet-stream")
            elif mime_type == "text/javascript":
                mime_type = "application/javascript"

            if mime_type.startswith("text/") or mime_type in ("application/javascript", "application/json"):
                content_type = f"{mime_type}; charset=utf-8"
            else:
                content_type = mime_type

            # 5. Serve File Content
            self._ok(target_file.read_bytes(), content_type=content_type)
            return

        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass
        except Exception as e:
            print(f"[ERROR] GET {self.path}: {e}")
            self.send_response(500)
            self.end_headers()

    def do_POST(self):
        try:
            path = urllib.parse.urlparse(self.path).path

            length_hdr = self.headers.get("Content-Length", "0")
            try:
                length = int(length_hdr)
                if length < 0:
                    self._error(400, "Invalid Content-Length")
                    return
            except ValueError:
                self._error(400, "Invalid Content-Length header")
                return

            raw_bytes = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body_str = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                self._error(400, "Invalid UTF-8 encoding")
                return

            try:
                body = json.loads(body_str) if body_str.strip() else {}
            except json.JSONDecodeError:
                self._error(400, "Malformed JSON payload")
                return

            if not isinstance(body, dict):
                self._error(400, "Request body must be a JSON object")
                return

            ws = get_workspace()
            hub = ws / ".agenthub"
            inbox = hub / "inbox"
            now = datetime.now().strftime("%Y-%m-%d %H:%M")

            if path == "/api/launch":
                target = body.get("target", "lead")
                open_browser = bool(body.get("open_browser", False))
                res = launch_instance(target, str(ws), open_browser=open_browser)
                self._json(res)
                return

            if path == "/api/set_workspace":
                raw_ws = body.get("workspace", "")
                if not isinstance(raw_ws, str):
                    self._json({"success": False, "error": "โฟลเดอร์ไม่ถูกต้อง"})
                    return
                new_ws = raw_ws.strip().strip('"').strip("'")
                if not new_ws:
                    self._json({"success": False, "error": "โฟลเดอร์ไม่ถูกต้อง"})
                    return

                p = Path(new_ws).resolve()
                if not p.exists() or not p.is_dir():
                    self._json({"success": False, "error": "โฟลเดอร์ไม่ถูกต้อง"})
                    return

                try:
                    cfg = load_config()
                    cfg["workspace"] = str(p)
                    save_config(cfg)
                except Exception as e:
                    self._error(500, f"Failed to save configuration: {e}")
                    return

                try:
                    _ensure_agenthub(p)
                except Exception as e:
                    self._error(500, f"Cannot initialize .agenthub: {e}")
                    return

                self._json({"success": True, "workspace": str(p)})
                return

            if path == "/api/dispatch":
                title = body.get("title", "").strip() or "ภารกิจใหม่"
                overview = body.get("overview", "").strip()
                raw_target = str(body.get("target_worker", "all")).strip().lower()
                valid_targets = ("all", "worker1", "worker2", "worker3")
                if raw_target not in valid_targets:
                    self._error(400, f"Invalid target_worker: '{raw_target}'. Must be one of {list(valid_targets)}.")
                    return

                target_worker = raw_target
                target_workers = ["worker1", "worker2", "worker3"] if target_worker == "all" else [target_worker]

                roles = {
                    "worker1": "Module A / Backend & Core",
                    "worker2": "Module B / Frontend & UI",
                    "worker3": "Module C / Testing & QA"
                }

                def _get_worker_task(w: str) -> tuple[str, str]:
                    val = body.get(w, "").strip()
                    if not val and target_worker == w:
                        val = body.get("task", "").strip()
                    if val:
                        return val, "🟡 กำลังทำ"

                    if w in target_workers:
                        return "ยังไม่มีคำสั่งเฉพาะ (รอรับมอบหมาย)", "⚪ รอมอบหมาย"

                    # Worker was not targeted: preserve existing task from inbox
                    inbox_file = inbox / f"{w}.md"
                    if inbox_file.exists():
                        try:
                            content = inbox_file.read_text(encoding="utf-8")
                            if "### 📝 งานที่ต้องทำ:" in content:
                                extracted = content.split("### 📝 งานที่ต้องทำ:", 1)[1].split("\n\n---", 1)[0].strip()
                                if extracted and not extracted.startswith("*(รอรับคำสั่งใหม่)*"):
                                    return extracted, "🟡 กำลังทำ (งานเดิม)"
                        except Exception:
                            pass
                    return "รอมอบหมาย", "⚪ รอมอบหมาย"

                # Update targeted inboxes
                for w in target_workers:
                    task_text, _ = _get_worker_task(w)
                    (inbox / f"{w}.md").write_text(
                        f"# 📥 Inbox สำหรับ {w.capitalize()} ({roles[w]})\n\n"
                        f"> **ภารกิจ:** {title}  \n> **มอบหมายเมื่อ:** {now}\n\n---\n\n"
                        f"### 📝 งานที่ต้องทำ:\n{task_text}\n\n---\n\n"
                        f"### 📌 ขั้นตอน:\n1. ประกาศเริ่มงานใน `.agenthub/TEAM_CHAT.md`\n"
                        f"2. ส่งงานที่ `.agenthub/inbox/lead_review.md` เมื่อเสร็จ\n",
                        encoding="utf-8")

                # Update BOARD.md
                matrix_rows = []
                matrix_rows.append("| 👑 Team Lead | เมลหลัก | กำกับดูแล, ตรวจสอบ | 🟢 วางแผนแล้ว |")
                for w in ("worker1", "worker2", "worker3"):
                    t_desc, t_status = _get_worker_task(w)
                    display_name = {"worker1": "🛠️ Worker 1", "worker2": "🎨 Worker 2", "worker3": "🧪 Worker 3"}[w]
                    matrix_rows.append(f"| {display_name} | {w} | {t_desc} | {t_status} |")
                matrix_table = "\n".join(matrix_rows)

                (hub / "BOARD.md").write_text(f"""# 📋 Central Project Board

> **ภารกิจปัจจุบัน:** {title}
> **อัปเดต:** {now}  |  **สถานะ:** 🚀 In Progress

---

## 📐 Data Contract & Overview
{overview or "ไม่มีรายละเอียด"}

---

## 📊 Responsibility Matrix

| Agent | บัญชี | ขอบเขตงาน | สถานะ |
|:---|:---|:---|:---|
{matrix_table}

---

## 📌 กฎเหล็ก
1. ก่อนสร้าง Function / Interface ใหม่ → ประกาศใน TEAM_CHAT.md
2. ส่งงานเสร็จ → เขียนสรุปใน inbox/lead_review.md
""", encoding="utf-8")

                # Announce in TEAM_CHAT.md via thread-safe atomic append
                target_label = "Worker 1, 2, 3" if target_worker == "all" else target_worker.capitalize()
                chat_announcement = f"แจกจ่ายงาน **{title}** ให้ {target_label} แล้ว — ตรวจสอบ inbox ของตนเอง"
                append_chat(hub, "📢 Central Dispatcher", chat_announcement, now)

                self._json({
                    "success": True,
                    "target_worker": target_worker,
                    "updated_inboxes": target_workers,
                    "message": f"กระจายงานไปยัง {target_label} สำเร็จแล้ว!"
                })
                return

            if path == "/api/chat":
                raw_msg = body.get("message")
                if raw_msg is None or not isinstance(raw_msg, str):
                    self._json({"success": False, "error": "Empty message"})
                    return

                clean_msg = raw_msg.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
                if not clean_msg.strip():
                    self._json({"success": False, "error": "Empty message"})
                    return

                raw_sender = body.get("sender")
                if not raw_sender or not isinstance(raw_sender, str) or not raw_sender.strip():
                    sender = "You"
                else:
                    sender = re.sub(r'[\r\n\[\]]', '', raw_sender).strip() or "You"

                ok, err = append_chat(hub, sender, clean_msg, now)
                if not ok:
                    self._error(500, f"Failed to append message: {err}")
                    return

                self._json({"success": True})
                return

            self.send_response(404)
            self.end_headers()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass
        except Exception as e:
            print(f"[ERROR] POST {self.path}: {e}")
            self.send_response(500)
            self.end_headers()

class RobustHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)

# ── pywebview JS API ───────────────────────────────────────────────────────────
class JSApi:
    """Exposed to JavaScript via window.pywebview.api.*"""

    def pick_folder(self, current_path: str = "") -> str:
        """Open a native folder picker dialog and return the chosen path."""
        try:
            result = webview.windows[0].create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=current_path if current_path else str(Path.home())
            )
            if result:
                return result[0]
        except Exception:
            pass
        return ""

# ── Server runner ─────────────────────────────────────────────────────────────
def _run_server():
    os.chdir(BASE_DIR)
    while True:
        try:
            srv = RobustHTTPServer(("127.0.0.1", PORT), HubHandler)
            print(f"[SERVER] Listening on http://127.0.0.1:{PORT}")
            srv.serve_forever()
        except OSError as e:
            if "address already in use" in str(e).lower():
                print(f"[SERVER] Port {PORT} already in use — skipping server start")
                break
            print(f"[SERVER] Error: {e}")
            time.sleep(1)
        except KeyboardInterrupt:
            break

# ── Main entry point ──────────────────────────────────────────────────────────
def main():
    # 1. Start HTTP server in background thread
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    # Give server a moment to bind
    time.sleep(0.5)

    url = f"http://127.0.0.1:{PORT}"

    import webbrowser

    if HAS_WEBVIEW:
        # Probe: try to launch webview in a thread; if it fails quickly (WebView2 locked),
        # fall back to opening the browser while keeping the server alive.
        webview_ok = threading.Event()
        webview_fail = threading.Event()

        def _try_webview():
            try:
                storage = str(BASE_DIR / ".webview_data")
                Path(storage).mkdir(exist_ok=True)
                api = JSApi()
                webview.create_window(
                    title="Antigravity Multi-Agent Commander",
                    url=url,
                    js_api=api,
                    width=1280,
                    height=820,
                    min_size=(900, 600),
                    background_color="#1a1a1a",
                    frameless=False,
                    text_select=True,
                    confirm_close=False,
                )
                webview_ok.set()
                webview.start(
                    gui="edgechromium",
                    debug=False,
                    private_mode=False,
                    storage_path=storage,
                )
            except Exception:
                webview_fail.set()

        wv_thread = threading.Thread(target=_try_webview, daemon=True)
        wv_thread.start()

        # Wait up to 5 s for webview to either succeed or fail
        for _ in range(50):
            if webview_ok.is_set() or webview_fail.is_set():
                break
            time.sleep(0.1)

        if webview_fail.is_set() or (not webview_ok.is_set()):
            print("[APP] Native window unavailable — falling back to browser mode")
            webbrowser.open(url)
            try:
                wv_thread.join()  # wait for webview thread to end
            except Exception:
                pass
            print(f"[APP] Commander running at {url}  (browser mode)")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("[APP] Shutting down.")
        else:
            # webview started OK — block here until window closed
            wv_thread.join()
    else:
        webbrowser.open(url)
        print(f"[APP] Browser mode — {url}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[APP] Shutting down.")

if __name__ == "__main__":
    main()
