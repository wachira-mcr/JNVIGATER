#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Antigravity Multi-Agent Hub Server
A lightweight, zero-dependency controller to orchestrate 4 Antigravity accounts/instances
and dispatch tasks synchronously or asynchronously.
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.parse
from datetime import datetime

PORT = 8989
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = BASE_DIR
CONFIG_FILE = BASE_DIR / "config.json"
IDE_EXE = r"C:\Users\MBx13\AppData\Local\Programs\Antigravity IDE\Antigravity IDE.exe"
PROFILES_ROOT = Path(r"C:\Users\MBx13\.ag-profiles")

PROFILES = {
    "lead": {
        "id": "lead",
        "name": "Team Lead (เมลหลัก)",
        "role": "Orchestrator & Final QA",
        "icon": "crown",
        "color": "amber",
        "dir": str(PROFILES_ROOT / "lead")
    },
    "worker1": {
        "id": "worker1",
        "name": "Worker 1 (เมลที่ 2)",
        "role": "Module A / Backend & Core",
        "icon": "database",
        "color": "blue",
        "dir": str(PROFILES_ROOT / "worker1")
    },
    "worker2": {
        "id": "worker2",
        "name": "Worker 2 (เมลที่ 3)",
        "role": "Module B / Frontend & UI",
        "icon": "layout",
        "color": "emerald",
        "dir": str(PROFILES_ROOT / "worker2")
    },
    "worker3": {
        "id": "worker3",
        "name": "Worker 3 (เมลที่ 4)",
        "role": "Module C / Testing & Docs",
        "icon": "flask-conical",
        "color": "purple",
        "dir": str(PROFILES_ROOT / "worker3")
    }
}

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"workspace": str(DEFAULT_WORKSPACE)}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_workspace():
    cfg = load_config()
    ws = Path(cfg.get("workspace", str(DEFAULT_WORKSPACE)))
    ensure_agenthub(ws)
    return ws

def ensure_agenthub(ws: Path):
    hub = ws / ".agenthub"
    inbox = hub / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    
    board = hub / "BOARD.md"
    if not board.exists():
        board.write_text("# 📋 Central Project Board\n\nสถานะ: พร้อมรับงาน\n", encoding="utf-8")
        
    chat = hub / "TEAM_CHAT.md"
    if not chat.exists():
        chat.write_text("# 💬 Swarm Team Live Chat\n\n- `[System]`: ห้องแชทพร้อมทำงาน\n", encoding="utf-8")
        
    review = inbox / "lead_review.md"
    if not review.exists():
        review.write_text("# 🔍 Inbox สำหรับ Team Lead: ตรวจงาน\n\n*(ยังไม่มีงานส่งเข้ามา)*\n", encoding="utf-8")
        
    for w in ["worker1", "worker2", "worker3"]:
        w_file = inbox / f"{w}.md"
        if not w_file.exists():
            w_file.write_text(f"# 📥 Inbox สำหรับ {w}\n\n*(รอรับคำสั่งใหม่)*\n", encoding="utf-8")

def launch_instance(target: str, workspace_path: str):
    targets = ["lead", "worker1", "worker2", "worker3"] if target == "all" else [target]
    launched = []
    
    for t in targets:
        if t in PROFILES:
            p_dir = PROFILES[t]["dir"]
            Path(p_dir).mkdir(parents=True, exist_ok=True)
            cmd = [
                IDE_EXE,
                "--user-data-dir", p_dir,
                "-n", workspace_path
            ]
            try:
                DETACHED_PROCESS = 0x00000008
                subprocess.Popen(
                    cmd,
                    creationflags=DETACHED_PROCESS,
                    close_fds=True
                )
                launched.append(t)
            except Exception as e:
                print(f"Failed to launch {t}: {e}")
    return launched

def read_file_safe(path: Path, default=""):
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            try:
                return path.read_text(encoding="cp874", errors="ignore")
            except Exception:
                return default
    return default

class AgentHubHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence routine 200 / 304 polling logs
        if "GET /api/status" in args[0] or "GET /favicon.ico" in args[0]:
            return
        super().log_message(format, *args)

    def _set_headers(self, content_type="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers()

    def do_GET(self):
        try:
            url = urllib.parse.urlparse(self.path)
            path = url.path

            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return

            if path == "/" or path == "/index.html":
                index_path = BASE_DIR / "public" / "index.html"
                if index_path.exists():
                    self._set_headers("text/html")
                    self.wfile.write(index_path.read_bytes())
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"index.html not found")
                return

            if path == "/api/status":
                ws = get_workspace()
                hub = ws / ".agenthub"
                inbox = hub / "inbox"
                
                data = {
                    "workspace": str(ws),
                    "profiles": PROFILES,
                    "board": read_file_safe(hub / "BOARD.md"),
                    "chat": read_file_safe(hub / "TEAM_CHAT.md"),
                    "review": read_file_safe(inbox / "lead_review.md"),
                    "worker1_inbox": read_file_safe(inbox / "worker1.md"),
                    "worker2_inbox": read_file_safe(inbox / "worker2.md"),
                    "worker3_inbox": read_file_safe(inbox / "worker3.md"),
                }
                self._set_headers()
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
                return

            self.send_response(404)
            self.end_headers()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass

    def do_POST(self):
        try:
            url = urllib.parse.urlparse(self.path)
            path = url.path
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                req = json.loads(body)
            except Exception:
                req = {}

            ws = get_workspace()
            hub = ws / ".agenthub"
            inbox = hub / "inbox"

            if path == "/api/launch":
                target = req.get("target", "lead")
                launched = launch_instance(target, str(ws))
                self._set_headers()
                self.wfile.write(json.dumps({"success": True, "launched": launched}).encode("utf-8"))
                return

            if path == "/api/set_workspace":
                new_ws = req.get("workspace", "").strip()
                if new_ws and Path(new_ws).exists():
                    cfg = load_config()
                    cfg["workspace"] = str(Path(new_ws).resolve())
                    save_config(cfg)
                    ensure_agenthub(Path(cfg["workspace"]))
                    self._set_headers()
                    self.wfile.write(json.dumps({"success": True, "workspace": cfg["workspace"]}).encode("utf-8"))
                else:
                    self._set_headers()
                    self.wfile.write(json.dumps({"success": False, "error": "โฟลเดอร์ไม่ถูกต้องหรือไม่มีอยู่จริง"}).encode("utf-8"))
                return

            if path == "/api/dispatch":
                title = req.get("title", "ภารกิจใหม่").strip()
                overview = req.get("overview", "").strip()
                w1_task = req.get("worker1", "").strip()
                w2_task = req.get("worker2", "").strip()
                w3_task = req.get("worker3", "").strip()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

                board_content = f"""# 📋 Central Project Board & Data Contracts

> **ภารกิจปัจจุบัน:** {title}  
> **อัปเดตล่าสุด:** {now_str}  
> **สถานะ:** 🚀 กำลังดำเนินการ (In Progress)  

---

## 🎯 1. ภาพรวมภารกิจและข้อตกลง (Overview & Contract)
{overview if overview else "ไม่มีรายละเอียดเพิ่มเติม"}

---

## 📊 2. กระดานความรับผิดชอบ (Responsibility Matrix)

| ส่วนงาน | ผู้รับผิดชอบ | ขอบเขตงาน | สถานะ |
| :--- | :--- | :--- | :--- |
| **👑 Team Lead** | เมลหลัก | กำกับดูแล, วาง Architecture, ตรวจสอบรอบสุดท้าย | 🟢 วางแผนแล้ว |
| **🛠️ Worker 1** | เมลที่ 2 (Module A) | {w1_task if w1_task else "รอมอบหมาย"} | 🟡 กำลังทำ |
| **🎨 Worker 2** | เมลที่ 3 (Module B) | {w2_task if w2_task else "รอมอบหมาย"} | 🟡 กำลังทำ |
| **🧪 Worker 3** | เมลที่ 4 (Module C) | {w3_task if w3_task else "รอมอบหมาย"} | 🟡 กำลังทำ |

---

## 📌 กฎเหล็กของทีม
1. ก่อนเริ่มทำหรือสร้างฟังก์ชัน/โมเดล ให้ประกาศลง `.agenthub/TEAM_CHAT.md`
2. อ่านข้อความของเพื่อนในแชทเสมอ เพื่อไม่ให้โค้ดขัดแย้งกัน
3. ส่งมอบงานลงใน `.agenthub/inbox/lead_review.md` เมื่อเสร็จ
"""
                (hub / "BOARD.md").write_text(board_content, encoding="utf-8")

                w1_content = f"""# 📥 ใบสั่งงานสำหรับ Worker 1 (Module A / Backend)

> **ภารกิจ:** {title}  
> **มอบหมายเมื่อ:** {now_str}  
> **สถานะ:** 🟡 กำลังดำเนินการ  

---

### 📝 รายละเอียดงานที่ต้องทำ:
{w1_task if w1_task else "ยังไม่มีคำสั่งเฉพาะ"}

---

### 📌 กฎและขั้นตอน:
1. ประกาศ Function / API Signature ลงใน `.agenthub/TEAM_CHAT.md`
2. เมื่อทำเสร็จ ให้สรุปและส่งงานใน `.agenthub/inbox/lead_review.md`
"""
                (inbox / "worker1.md").write_text(w1_content, encoding="utf-8")

                w2_content = f"""# 📥 ใบสั่งงานสำหรับ Worker 2 (Module B / Frontend)

> **ภารกิจ:** {title}  
> **มอบหมายเมื่อ:** {now_str}  
> **สถานะ:** 🟡 กำลังดำเนินการ  

---

### 📝 รายละเอียดงานที่ต้องทำ:
{w2_task if w2_task else "ยังไม่มีคำสั่งเฉพาะ"}

---

### 📌 กฎและขั้นตอน:
1. ตรวจสอบ `.agenthub/TEAM_CHAT.md` เพื่อดู Endpoint หรือ Interface จาก Worker 1
2. เมื่อทำเสร็จ ให้สรุปและส่งงานใน `.agenthub/inbox/lead_review.md`
"""
                (inbox / "worker2.md").write_text(w2_content, encoding="utf-8")

                w3_content = f"""# 📥 ใบสั่งงานสำหรับ Worker 3 (Module C / Testing & QA)

> **ภารกิจ:** {title}  
> **มอบหมายเมื่อ:** {now_str}  
> **สถานะ:** 🟡 กำลังดำเนินการ  

---

### 📝 รายละเอียดงานที่ต้องทำ:
{w3_task if w3_task else "ยังไม่มีคำสั่งเฉพาะ"}

---

### 📌 กฎและขั้นตอน:
1. เตรียม Unit Test / Integration Test หรือตรวจเช็ค Security / Docs
2. เมื่อทำเสร็จ ให้สรุปและส่งงานใน `.agenthub/inbox/lead_review.md`
"""
                (inbox / "worker3.md").write_text(w3_content, encoding="utf-8")

                chat_file = hub / "TEAM_CHAT.md"
                existing_chat = read_file_safe(chat_file)
                chat_announcement = f"- `[{now_str}] [📢 Central Dispatcher]`: แจกจ่ายงานใหม่: **{title}** ให้ Worker 1, 2, 3 แล้ว! กรุณาตรวจสอบ inbox ของตนเอง\n"
                chat_file.write_text(existing_chat + chat_announcement, encoding="utf-8")

                self._set_headers()
                self.wfile.write(json.dumps({"success": True, "message": "กระจายงานไปยัง 3 เมลสำเร็จแล้ว!"}).encode("utf-8"))
                return

            if path == "/api/chat":
                sender = req.get("sender", "You").strip()
                msg = req.get("message", "").strip()
                if msg:
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                    chat_file = hub / "TEAM_CHAT.md"
                    existing = read_file_safe(chat_file)
                    entry = f"- `[{now_str}] [{sender}]`: {msg}\n"
                    chat_file.write_text(existing + entry, encoding="utf-8")
                    self._set_headers()
                    self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                    return
                self._set_headers()
                self.wfile.write(json.dumps({"success": False, "error": "ข้อความว่างเปล่า"}).encode("utf-8"))
                return

            self.send_response(404)
            self.end_headers()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass

class RobustServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            return
        super().handle_error(request, client_address)

def run_server():
    os.chdir(BASE_DIR)
    ws = get_workspace()
    print("=" * 60)
    print(f" Antigravity Multi-Agent Commander running on http://localhost:{PORT}")
    print(f" Active Project Workspace: {ws}")
    print(" Profiles Directory: C:\\Users\\MBx13\\.ag-profiles")
    print("=" * 60)
    while True:
        try:
            httpd = RobustServer(("127.0.0.1", PORT), AgentHubHandler)
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server...")
            break
        except Exception as e:
            time.sleep(1)

if __name__ == "__main__":
    run_server()
