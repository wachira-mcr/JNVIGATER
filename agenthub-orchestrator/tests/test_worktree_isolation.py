"""Checks for per-worker git worktree isolation and the generated rules file."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def test_worker_gets_own_worktree_and_subdir_mapping(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "proj").mkdir(parents=True)
    (repo / "proj" / "f.txt").write_text("x")
    _run(["git", "init", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "t"], repo)
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-m", "init"], repo)

    monkeypatch.setattr(app, "WORKTREE_ROOT", tmp_path / "wt")
    ws = repo / "proj"

    lead = app.ensure_worktree("lead", ws)
    assert lead == ws, "lead must stay on the main workspace"

    w1 = app.ensure_worktree("worker1", ws)
    w2 = app.ensure_worktree("worker2", ws)
    assert w1 != ws and w2 != ws and w1 != w2
    assert w1.name == "proj", "subdir workspace must map to the same subdir in the worktree"
    assert (w1 / "f.txt").is_file()

    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=str(w1), capture_output=True, text=True).stdout.strip()
    assert branch == "agent/worker1"

    assert app.ensure_worktree("worker1", ws) == w1, "must be idempotent"


def test_non_git_workspace_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "WORKTREE_ROOT", tmp_path / "wt")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert app.ensure_worktree("worker1", plain) == plain


def test_rules_file_points_at_shared_hub(tmp_path):
    agent_dir = tmp_path / "w1"
    agent_dir.mkdir()
    hub = tmp_path / "main" / ".agenthub"

    app.write_agent_rules("worker1", agent_dir, hub)

    for name in ("AGENTS.md", "GEMINI.md"):
        text = (agent_dir / name).read_text(encoding="utf-8")
        assert str(hub / "inbox" / "worker1.md") in text
        assert str(hub / "TEAM_CHAT.md") in text
        assert "agent/worker1" in text
