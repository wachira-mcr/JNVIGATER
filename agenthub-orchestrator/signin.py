#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Sign one agent in without the other agents stealing its token.

Every clone asks Google for the same redirect, antigravity://oauth-success/, so
Windows hands the callback to whichever exe owns that scheme — normally the Lead.
That is why all four agents end up on one email.

This points antigravity:// at the agent that is signing in, waits for its own log
to confirm the callback landed, then puts the original handler back.

    python signin.py worker1
    python signin.py --show        # print the current handler, change nothing

Run one agent at a time; the scheme can only belong to one of them at once.
"""
import argparse
import subprocess
import sys
import time
import winreg
from pathlib import Path

PROGRAMS = Path(r"C:\Users\MBx13\AppData\Local\Programs")
ROAMING = Path(r"C:\Users\MBx13\AppData\Roaming")
KEY = r"SOFTWARE\Classes\antigravity\shell\open\command"

AGENTS = {
    "lead":    (PROGRAMS / "antigravity" / "Antigravity.exe",          ROAMING / "Antigravity"),
    "worker1": (PROGRAMS / "antigravity-worker1" / "Worker1.exe",      ROAMING / "Antigravity Worker 1"),
    "worker2": (PROGRAMS / "antigravity-worker2" / "Worker2.exe",      ROAMING / "Antigravity Worker 2"),
    "worker3": (PROGRAMS / "antigravity-worker3" / "Worker3.exe",      ROAMING / "Antigravity Worker 3"),
}


def get_handler() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            return winreg.QueryValueEx(k, "")[0]
    except FileNotFoundError:
        return ""


def set_handler(command: str):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, command)


def callbacks(log: Path) -> int:
    """How many oauth-success deep links this agent has received."""
    if not log.is_file():
        return 0
    return log.read_text(encoding="utf-8", errors="ignore").count("oauth-success")


def signin(agent: str, timeout: float = 300.0) -> bool:
    exe, userdata = AGENTS[agent]
    if not exe.is_file():
        print(f"[ERR] not installed: {exe}")
        return False

    log = userdata / "logs" / "main.log"
    before = callbacks(log)
    original = get_handler()
    print(f"[reg] antigravity:// was -> {original or '(unset)'}")

    set_handler(f'"{exe}" "%1"')
    print(f"[reg] antigravity:// now -> {exe}")
    try:
        subprocess.Popen([str(exe)], close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 8))
        print(f"[.] {agent} launched — sign in with ITS OWN Google account in the browser.")
        print(f"[.] waiting up to {int(timeout)}s for the callback to reach {agent}...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            if callbacks(log) > before:
                print(f"[OK] {agent} received its own oauth callback.")
                return True
        print(f"[!] no callback seen in {agent}'s log — sign-in not finished.")
        return False
    finally:
        if original:
            set_handler(original)
            print(f"[reg] antigravity:// restored -> {original}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", nargs="?", choices=sorted(AGENTS))
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args()

    if a.show or not a.agent:
        print("antigravity:// ->", get_handler() or "(unset)")
        for name, (exe, ud) in AGENTS.items():
            print(f"  {name:8} callbacks={callbacks(ud / 'logs' / 'main.log')}  installed={exe.is_file()}")
        return
    sys.exit(0 if signin(a.agent, a.timeout) else 1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
