#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto-kick: send the opening message to an Antigravity agent over Chrome DevTools
Protocol, so nobody has to type "เริ่มงาน" into three windows by hand.

The agent must have been launched with --remote-debugging-port=<port>; app.py
does that for every worker.

    python agent_kick.py --probe 9333          # list candidate chat inputs
    python agent_kick.py --port 9333 --text "เริ่มงาน"
"""
import argparse
import asyncio
import json
import sys
import urllib.request

import websockets

# JS run inside the agent's renderer: find the chat box, fill it, fire the events
# a React/Vue editor needs to notice the change. Returns a short status string.
FILL_JS = r"""
(() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    return r.width > 120 && r.height > 12 && getComputedStyle(el).visibility !== 'hidden';
  };
  const cands = [...document.querySelectorAll(
    'textarea, [contenteditable="true"], [role="textbox"], input[type="text"]'
  )].filter(vis);
  if (!cands.length) return 'NO_INPUT';
  // The chat box is the lowest one on screen (composer sits at the bottom).
  const el = cands.sort((a, b) =>
    b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom)[0];
  el.focus();
  const text = %TEXT%;
  if (el.isContentEditable) {
    document.execCommand('selectAll', false, null);
    document.execCommand('insertText', false, text);
  } else {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return 'FILLED:' + (el.tagName + (el.getAttribute('placeholder') || ''));
})()
"""

PROBE_JS = r"""
(() => [...document.querySelectorAll(
  'textarea, [contenteditable="true"], [role="textbox"], input[type="text"]'
)].map(el => {
  const r = el.getBoundingClientRect();
  return {tag: el.tagName, ce: el.isContentEditable, role: el.getAttribute('role'),
          ph: el.getAttribute('placeholder'), aria: el.getAttribute('aria-label'),
          box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]};
}))()
"""


def page_targets(port: int) -> list:
    """Renderer pages of the agent, newest first, onboarding/devtools filtered out."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
        targets = json.load(r)
    return [t for t in targets
            if t.get("type") == "page"
            and not t.get("url", "").startswith(("devtools://", "data:"))]


class CDP:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def send(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def eval(self, expr):
        res = await self.send("Runtime.evaluate", expression=expr,
                              returnByValue=True, awaitPromise=True)
        return res.get("result", {}).get("value")

    async def key(self, text=None, **kw):
        for t in ("keyDown", "keyUp"):
            await self.send("Input.dispatchKeyEvent", type=t, **kw)


async def _connect(port: int, url_filter: str = ""):
    targets = page_targets(port)
    if url_filter:
        targets = [t for t in targets if url_filter in t.get("url", "")] or targets
    if not targets:
        raise RuntimeError(f"no renderer page on CDP port {port}")
    return targets[0], await websockets.connect(targets[0]["webSocketDebuggerUrl"],
                                                max_size=None, open_timeout=10)


async def probe(port: int):
    target, ws = await _connect(port)
    async with ws:
        print(f"page: {target['url'][:100]}")
        for el in (await CDP(ws).eval(PROBE_JS)) or []:
            print("  ", el)


async def kick(port: int, text: str) -> str:
    _, ws = await _connect(port)
    async with ws:
        cdp = CDP(ws)
        status = await cdp.eval(FILL_JS.replace("%TEXT%", json.dumps(text)))
        if status == "NO_INPUT":
            return "NO_INPUT"
        # Enter must be a real key event — setting .value alone never submits.
        await cdp.key(key="Enter", code="Enter", windowsVirtualKeyCode=13,
                      nativeVirtualKeyCode=13, text="\r")
        return status


def run_kick(port: int, text: str) -> str:
    """Blocking wrapper for callers that are not async (app.py's launcher thread)."""
    return asyncio.run(kick(port, text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, help="CDP port of the agent")
    ap.add_argument("--text", default="เริ่มงาน")
    ap.add_argument("--probe", type=int, metavar="PORT")
    a = ap.parse_args()
    if a.probe:
        asyncio.run(probe(a.probe))
    elif a.port:
        print(asyncio.run(kick(a.port, a.text)))
    else:
        ap.error("need --port or --probe")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
