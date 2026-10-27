# Modified by Kodem.
"""Contract tests of Kodem's Code Map entry point (FORK.md, "Kodem contract").

Each command runs in a child process, as Kodem runs it, so that the
environment that the runtime sets before importing graphify is exercised.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from graphify import kodem_main
from graphify.kodem_viewer import ViewerError, adapt_page

RESULT = kodem_main.RESULT_PREFIX


def _run(*arguments: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Runs one runtime command the way Kodem does."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST")}
    environment.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "graphify.kodem_main", *arguments],
        capture_output=True,
        text=True,
        env=environment,
        cwd=cwd,
        timeout=300,
    )


def _result(process: subprocess.CompletedProcess[str]) -> dict:
    """Returns the JSON of the result line, which must be the last stdout line."""
    lines = process.stdout.strip().splitlines()
    assert lines, process.stderr
    assert lines[-1].startswith(RESULT), process.stdout
    return json.loads(lines[-1][len(RESULT):])


def _snapshot(folder: Path) -> dict[str, float]:
    """Returns every file below ``folder`` with its modification time."""
    return {str(path.relative_to(folder)): path.stat().st_mtime_ns for path in folder.rglob("*")}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A small git repository with Rust and Python code."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "lib.rs").write_text(
        "pub struct Matcher;\nimpl Matcher {\n    pub fn find(&self) -> usize { helper() }\n}\n"
        "fn helper() -> usize { 1 }\n",
        encoding="utf-8",
    )
    (root / "tool.py").write_text("def run():\n    return walk()\n\ndef walk():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    return root


def test_version_prints_the_contract_versions() -> None:
    process = _run("version")
    assert process.returncode == 0
    assert _result(process) == {"version": kodem_main.VERSION, "graphify": kodem_main.GRAPHIFY_VERSION, "schema": 1}


def test_build_update_and_html_write_only_into_the_out_folder(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "map"
    home = tmp_path / "home"
    home.mkdir()
    before = _snapshot(repo)
    env = {"HOME": str(home), "USERPROFILE": str(home)}

    built = _run("build", str(repo), "--out", str(out), env=env, cwd=tmp_path)
    assert built.returncode == 0, built.stderr
    summary = _result(built)
    assert summary["nodes"] > 0 and summary["edges"] > 0 and summary["files"] == 2
    assert len(summary["commit"]) == 40
    assert (out / "graph.json").is_file()
    assert not (out / "graph.html").exists()

    updated = _run("update", str(repo), "--out", str(out), env=env, cwd=tmp_path)
    assert updated.returncode == 0, updated.stderr
    assert _result(updated) == summary

    page = _run("html", "--out", str(out), "--theme", "light", env=env, cwd=tmp_path)
    assert page.returncode == 0, page.stderr
    html = Path(_result(page)["html"]).read_text(encoding="utf-8")
    assert "https://unpkg.com" not in html
    assert "<title>Code Map</title>" in html
    assert "kodem-codemap://open?path=" in html
    assert "'light'" in html

    assert _snapshot(repo) == before
    assert list(home.iterdir()) == []
    assert not (tmp_path / "graphify-out").exists()


def test_update_sees_a_new_function(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "map"
    first = _result(_run("build", str(repo), "--out", str(out)))
    with (repo / "tool.py").open("a", encoding="utf-8") as source:
        source.write("\ndef added():\n    return run()\n")
    second = _result(_run("update", str(repo), "--out", str(out)))
    assert second["nodes"] == first["nodes"] + 1
    assert second["edges"] > first["edges"]


def test_update_without_a_map_builds_one(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "map"
    process = _run("update", str(repo), "--out", str(out))
    assert process.returncode == 0, process.stderr
    assert _result(process)["nodes"] > 0


def test_a_copied_map_updates_against_another_checkout(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "map"
    summary = _result(_run("build", str(repo), "--out", str(out)))
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "worktree", "add", "-q", str(worktree)], cwd=repo, check=True)
    copied = tmp_path / "task-map"
    subprocess.run(["cp", "-R", str(out), str(copied)], check=True)
    process = _run("update", str(worktree), "--out", str(copied))
    assert process.returncode == 0, process.stderr
    assert _result(process)["nodes"] == summary["nodes"]
    data = json.loads((copied / "graph.json").read_text(encoding="utf-8"))
    assert all(not str(node.get("source_file", "")).startswith("/") for node in data["nodes"])


def test_backend_variables_and_a_claude_binary_are_never_used(repo: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "claude-ran"
    claude = bin_dir / "claude"
    claude.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    (repo / "README.md").write_text("# Notes\n\nA document that a semantic pass would send to an LLM.\n", encoding="utf-8")
    env = {
        "ANTHROPIC_API_KEY": "sk-test",
        "OPENAI_API_KEY": "sk-test",
        "AWS_PROFILE": "default",
        "GRAPHIFY_BACKEND": "claude",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    out = tmp_path / "map"
    process = _run("build", str(repo), "--out", str(out), env=env)
    assert process.returncode == 0, process.stderr
    assert not marker.exists()
    assert not (out / "cost.json").exists()


def test_bad_arguments_use_the_usage_code(tmp_path: Path) -> None:
    assert _run("build").returncode == kodem_main.EXIT_USAGE
    assert _run("build", "relative/path", "--out", str(tmp_path)).returncode == kodem_main.EXIT_USAGE
    assert _run("nonsense").returncode == kodem_main.EXIT_USAGE
    missing = _run("build", str(tmp_path / "missing"), "--out", str(tmp_path / "out"))
    assert missing.returncode == kodem_main.EXIT_USAGE
    assert "folder not found" in missing.stderr.strip().splitlines()[-1]


def test_html_without_a_map_fails_with_a_message(tmp_path: Path) -> None:
    process = _run("html", "--out", str(tmp_path))
    assert process.returncode == kodem_main.EXIT_ERROR
    assert process.stderr.strip().splitlines()[-1].startswith("no map in")


def test_the_network_guard_allows_only_loopback() -> None:
    script = (
        "import socket\n"
        "from graphify import kodem_main\n"
        "kodem_main.block_network()\n"
        "server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(1)\n"
        "client = socket.socket(); client.connect(server.getsockname()); client.close(); server.close()\n"
        "try:\n"
        "    socket.create_connection(('example.com', 443), timeout=1)\n"
        "except kodem_main.NetworkBlocked:\n"
        "    print('blocked-name')\n"
        "try:\n"
        "    socket.socket().connect(('93.184.215.14', 443))\n"
        "except kodem_main.NetworkBlocked:\n"
        "    print('blocked-address')\n"
    )
    process = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr
    assert process.stdout.split() == ["blocked-name", "blocked-address"]


def test_the_guarded_connect_rejects_remote_addresses_in_process() -> None:
    assert kodem_main._address_is_local(socket.AF_INET, ("127.0.0.1", 80))
    assert kodem_main._address_is_local(socket.AF_INET6, ("::1", 80, 0, 0))
    assert not kodem_main._address_is_local(socket.AF_INET, ("10.0.0.1", 80))


def test_adapt_page_fails_loudly_when_upstream_changes_the_page() -> None:
    with pytest.raises(ViewerError):
        adapt_page("<html><head><title>x</title></head><body></body></html>", theme="dark")
