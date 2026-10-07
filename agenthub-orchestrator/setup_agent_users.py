#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Wire the worker agents to their own Windows accounts.

Antigravity stores its Google token in Windows Credential Manager under one key
that every clone shares, so two clones running as the same Windows user are always
the same Google account. A separate Windows account is the only thing that gives a
worker a credential store of its own.

This does the parts that need no admin rights: grants the agent accounts access to
the folders they must read and write, and records the mapping in config.json.
Creating the accounts themselves needs an elevated prompt — run --show first and
it prints the two commands for that.

    python setup_agent_users.py --show
    python setup_agent_users.py                      # defaults: agent1, agent2
    python setup_agent_users.py --users agent1 agent2
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
PROGRAMS = Path(r"C:\Users\MBx13\AppData\Local\Programs")
WORKTREE_ROOT = Path(r"D:\agents\worktrees")
REPO = Path(r"C:\Users\MBx13\.gemini\antigravity\scratch")

WORKERS = ["worker1", "worker2"]


def paths_for(worker: str) -> list[Path]:
    """Everything one worker account must be able to touch."""
    return [
        PROGRAMS / f"antigravity-{worker}",   # its own cloned binary
        WORKTREE_ROOT / worker,               # its branch checkout
        REPO / ".git",                        # worktrees need the main repo's git dir
        REPO / "agenthub-orchestrator" / ".agenthub",   # the shared hub files
    ]


def grant(user: str, path: Path) -> bool:
    if not path.exists():
        print(f"  [skip] missing: {path}")
        return False
    r = subprocess.run(
        ["icacls", str(path), "/grant", f"{user}:(OI)(CI)M", "/T", "/C"],
        capture_output=True, text=True, shell=True)
    ok = r.returncode == 0
    print(f"  [{'ok' if ok else 'FAIL'}] {user} -> {path}")
    if not ok:
        print("        ", (r.stderr or r.stdout).strip().splitlines()[-1:])
    return ok


def user_exists(user: str) -> bool:
    return subprocess.run(["net", "user", user], capture_output=True,
                          shell=True).returncode == 0


def show(users: list[str]):
    print("Accounts needed (create these in an ADMIN PowerShell):\n")
    for u in users:
        if user_exists(u):
            print(f"  {u}: already exists")
        else:
            print(f'  New-LocalUser -Name "{u}" -Description "Antigravity agent" '
                  f'-Password (Read-Host -AsSecureString "Password for {u}") '
                  f'-PasswordNeverExpires -UserMayNotChangePassword')
    print("\nThen re-run this script without --show.")
    print("\nFolders each account will be granted modify access to:")
    for w, u in zip(WORKERS, users):
        print(f"  {w} -> {u}")
        for p in paths_for(w):
            print(f"      {p}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", nargs="+", default=["agent1", "agent2"])
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    users = a.users[:len(WORKERS)]
    if len(users) < len(WORKERS):
        ap.error(f"need one account per worker: {WORKERS}")

    if a.show:
        show(users)
        return 0

    missing = [u for u in users if not user_exists(u)]
    if missing:
        print(f"[!] these accounts do not exist yet: {', '.join(missing)}")
        print("    Run with --show to get the commands, then create them as admin.")
        return 1

    print("Granting folder access")
    ok = True
    for worker, user in zip(WORKERS, users):
        for p in paths_for(worker):
            ok &= grant(user, p)

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    cfg["run_as"] = dict(zip(WORKERS, users))
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nconfig.json run_as = {cfg['run_as']}")

    print("\nNext: launch each worker once from the Commander and sign it in with")
    print("its own Google account. The first launch asks for that account's Windows")
    print("password (runas /savecred remembers it afterwards).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
