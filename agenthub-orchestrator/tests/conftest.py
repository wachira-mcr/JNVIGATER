"""
Test fixtures, server lifecycle manager, and workspace isolation for Antigravity Teamwork Orchestrator.
Supports both pytest fixtures and unittest test cases.
"""
import os
import sys
import json
import time
import shutil
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
import requests
import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app


class HubApiClient:
    """Helper client for executing REST requests against test server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def get(self, path: str, **kwargs):
        return self.session.get(f"{self.base_url}{path}", **kwargs)

    def post(self, path: str, json_data=None, **kwargs):
        return self.session.post(f"{self.base_url}{path}", json=json_data, **kwargs)

    def get_status(self):
        return self.get("/api/status")

    def post_chat(self, sender: str, message: str):
        return self.post("/api/chat", {"sender": sender, "message": message})

    def post_dispatch(self, payload: dict):
        return self.post("/api/dispatch", payload)

    def post_launch(self, target: str):
        return self.post("/api/launch", {"target": target})

    def post_set_workspace(self, workspace_path: str):
        return self.post("/api/set_workspace", {"workspace": workspace_path})


class TestServerContext:
    __test__ = False

    # Spins up the hub HTTP server on a random port against a throwaway workspace
    # and config.json, optionally mocking subprocess.Popen so no real agent starts.

    def __init__(self, mock_subprocess=True):
        self.mock_subprocess = mock_subprocess
        self.temp_dir = None
        self.workspace_dir = None
        self.config_path = None
        self.server = None
        self.server_thread = None
        self.port = 0
        self.base_url = ""
        self.client = None
        self.orig_config_file = app.CONFIG_FILE
        self.orig_default_workspace = app.DEFAULT_WORKSPACE
        self.popen_mock = None
        self.popen_patcher = None
        self.launched_cmds = []

    def __enter__(self):
        # Isolated temp workspace + config
        self.temp_dir = tempfile.mkdtemp(prefix="agenthub_test_")
        self.workspace_dir = Path(self.temp_dir) / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(self.temp_dir) / "config.json"

        # Write config pointing at the isolated workspace
        self.config_path.write_text(
            json.dumps({"workspace": str(self.workspace_dir)}, ensure_ascii=False),
            encoding="utf-8",
        )

        # Redirect app globals
        app.CONFIG_FILE = self.config_path
        app.DEFAULT_WORKSPACE = self.workspace_dir

        # Seed .agenthub structure
        app._ensure_agenthub(self.workspace_dir)

        # Mock process spawning
        if self.mock_subprocess:
            self.launched_cmds = []

            def fake_popen(cmd, *args, **kwargs):
                self.launched_cmds.append((cmd, args, kwargs))
                m = MagicMock()
                m.pid = 99999 + len(self.launched_cmds)
                m.poll.return_value = None
                return m

            self.popen_patcher = patch("subprocess.Popen", side_effect=fake_popen)
            self.popen_mock = self.popen_patcher.start()

        # Start the server on an ephemeral port
        self.server = app.RobustHTTPServer(("127.0.0.1", 0), app.HubHandler)
        self.port = self.server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.client = HubApiClient(self.base_url)

        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        # Give the server a moment to come up
        time.sleep(0.05)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Stop server
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass

        # Stop Popen mock
        if self.popen_patcher:
            try:
                self.popen_patcher.stop()
            except Exception:
                pass

        # Restore app globals
        app.CONFIG_FILE = self.orig_config_file
        app.DEFAULT_WORKSPACE = self.orig_default_workspace

        # Clean temp dir
        if self.temp_dir and Path(self.temp_dir).exists():
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass


@pytest.fixture
def test_server():
    """Running hub server with mocked subprocess."""
    with TestServerContext(mock_subprocess=True) as ctx:
        yield ctx


@pytest.fixture
def isolated_workspace():
    """Throwaway workspace with a seeded .agenthub folder."""
    temp_dir = tempfile.mkdtemp(prefix="agenthub_ws_")
    ws = Path(temp_dir)
    app._ensure_agenthub(ws)
    yield ws
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch):
    """Launch tests must never swap the real Google token in Credential Manager."""
    import accounts
    monkeypatch.setattr(accounts, "use", lambda agent: False)
