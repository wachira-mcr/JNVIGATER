#!/usr/bin/env python3
r"""
One Google account per agent, without separate Windows users.

Every Antigravity clone reads its token from the same Credential Manager entry
(gemini:antigravity), so whoever logged in last wins. We keep a copy per agent
(agenthub:<agent>) and put the right one back into the shared slot just before
that agent starts.

    python accounts.py save worker1   # right after logging in on worker1
    python accounts.py use worker1    # what launch does before starting worker1
    python accounts.py show
"""
import ctypes
import ctypes.wintypes as wt
import sys

import win32cred

SHARED = "gemini:antigravity"
GENERIC = win32cred.CRED_TYPE_GENERIC


def _read(target):
    try:
        return win32cred.CredRead(target, GENERIC)
    except Exception:
        return None


class _CRED(ctypes.Structure):
    _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
                ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
                ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", ctypes.c_void_p),
                ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                ("UserName", wt.LPWSTR)]


def _write(target, user, blob: bytes):
    # Raw CredWriteW: pywin32's CredWrite only takes str and stores it as UTF-16,
    # but Antigravity's token is UTF-8 bytes and must round-trip unchanged.
    buf = ctypes.create_string_buffer(blob, len(blob))
    c = _CRED(Type=GENERIC, TargetName=target, UserName=user,
              CredentialBlobSize=len(blob), CredentialBlob=ctypes.cast(buf, ctypes.c_void_p),
              Persist=win32cred.CRED_PERSIST_LOCAL_MACHINE)
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(c), 0):
        raise ctypes.WinError()


def save(agent):
    c = _read(SHARED)
    if not c:
        raise SystemExit(f"{SHARED} is empty - log in on {agent} first")
    _write(f"agenthub:{agent}", c["UserName"], c["CredentialBlob"])


def use(agent) -> bool:
    """Put agent's saved token in the shared slot. False if none saved."""
    c = _read(f"agenthub:{agent}")
    if not c:
        return False
    _write(SHARED, c["UserName"], c["CredentialBlob"])
    return True


def show():
    shared = (_read(SHARED) or {}).get("CredentialBlob")
    for a in ("lead", "worker1", "worker2", "worker3"):
        c = _read(f"agenthub:{a}")
        mark = " <- in shared slot" if c and c["CredentialBlob"] == shared else ""
        print(f"  {a:8} {'saved' if c else '-'}{mark}")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["show"]
    {"save": save, "use": use, "show": show}[cmd](*rest)
