# Modified by Kodem.
"""Kodem's entry point for the Code Map runtime: ``python -m graphify.kodem_main``.

The commands, their result line and their exit codes are the contract in
FORK.md. Kodem runs this module only; it never runs ``graphify`` itself, so the
upstream CLI's skill refresh, installers and LLM backends are never reached.

The runtime keeps three promises, enforced here before any graphify module is
imported:

- No LLM: every backend variable is removed and ``GRAPHIFY_BACKEND=none``.
- No network: sockets may only reach loopback addresses or Unix sockets.
- No writes outside ``--out``: ``GRAPHIFY_OUT`` is the absolute output folder.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
GRAPHIFY_VERSION = "0.9.96"
SCHEMA = 1
RESULT_PREFIX = "KODEM-CODEMAP-RESULT "

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_BUSY = 3

# Variables that select an LLM backend or carry its credentials. They are
# removed from the process so that no code path can pick a backend from them.
_BACKEND_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GRAPHIFY_ALLOW_LOCAL_PROVIDERS",
    "GRAPHIFY_CLAUDE_CLI_MODEL",
    "MOONSHOT_API_KEY",
    "OLLAMA_BASE_URL",
    "OLLAMA_HOST",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
)

# Settings that keep upstream features that Kodem does not use switched off.
_FIXED_SETTINGS = {
    "GRAPHIFY_BACKEND": "none",
    "GRAPHIFY_NO_AUTO_REFRESH": "1",
    "GRAPHIFY_NO_CTA": "1",
    "GRAPHIFY_NO_TIPS": "1",
    "GRAPHIFY_NO_VIZ": "1",
    "GRAPHIFY_QUERY_LOG_DISABLE": "1",
}

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"})

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo


class NetworkBlocked(OSError):
    """Raised when code in the runtime tries to reach a non-loopback address."""


def _address_is_local(family: int, address: Any) -> bool:
    """Returns whether a socket address is a Unix socket or a loopback address."""
    if family == getattr(socket, "AF_UNIX", None):
        return True
    if isinstance(address, tuple) and address:
        return str(address[0]) in _LOOPBACK_HOSTS
    return False


def _guarded_connect(self: socket.socket, address: Any) -> None:
    """``socket.connect`` that refuses any address except loopback and Unix sockets."""
    if not _address_is_local(self.family, address):
        raise NetworkBlocked(f"kodem-codemap does not use the network (blocked {address!r})")
    _original_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    """``socket.connect_ex`` that refuses any address except loopback and Unix sockets."""
    if not _address_is_local(self.family, address):
        raise NetworkBlocked(f"kodem-codemap does not use the network (blocked {address!r})")
    return _original_connect_ex(self, address)


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    """``socket.getaddrinfo`` that resolves only loopback names, so no DNS query leaves the machine."""
    if host is not None and str(host) not in _LOOPBACK_HOSTS:
        raise NetworkBlocked(f"kodem-codemap does not use the network (blocked lookup of {host!r})")
    return _original_getaddrinfo(host, *args, **kwargs)


def block_network() -> None:
    """Limits every socket of this process to loopback addresses and Unix sockets.

    asyncio still works: on Windows its self-pipe is a loopback socket pair.
    """
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]


def prepare_environment(out: Path | None) -> None:
    """Sets the variables that keep the runtime local, before any graphify module is imported."""
    for name in _BACKEND_VARIABLES:
        os.environ.pop(name, None)
    os.environ.update(_FIXED_SETTINGS)
    if out is not None:
        os.environ["GRAPHIFY_OUT"] = str(out)


def _pin_hash_seed(argv: list[str]) -> None:
    """Runs the command again with ``PYTHONHASHSEED=0`` so that node order and community IDs are stable.

    The seed is read only when the interpreter starts. On Windows ``os.exec*``
    does not replace the process, so the command runs as a child there and its
    exit status is passed on (upstream #3799).
    """
    if os.environ.get("PYTHONHASHSEED") == "0" or "PYTEST_CURRENT_TEST" in os.environ:
        return
    command = [sys.executable, "-m", "graphify.kodem_main", *argv]
    environment = {**os.environ, "PYTHONHASHSEED": "0"}
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        raise SystemExit(subprocess.run(command, env=environment).returncode)
    os.execve(sys.executable, command, environment)


def _absolute_folder(text: str, *, must_exist: bool) -> Path:
    """Returns a folder argument as an absolute real path, or exits with the usage code."""
    path = Path(text)
    if not path.is_absolute():
        fail(f"path must be absolute: {text}", EXIT_USAGE)
    if must_exist and not path.is_dir():
        fail(f"folder not found: {text}", EXIT_USAGE)
    # A symlinked scan root collapses source paths to basenames (upstream #4624).
    return path.resolve()


def fail(message: str, code: int = EXIT_ERROR) -> None:
    """Writes the error as the last stderr line and exits with ``code``."""
    sys.stdout.flush()
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(code)


def print_result(result: dict[str, Any]) -> None:
    """Prints the command's result as the last stdout line."""
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True), flush=True)


def graph_summary(graph_path: Path) -> dict[str, Any]:
    """Returns the node, edge and file counts and the commit of a written ``graph.json``."""
    data = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])
    edges = data.get("links", data.get("edges", []))
    files = {node.get("source_file") for node in nodes if node.get("source_file")}
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "files": len(files),
        "commit": data.get("built_at_commit"),
    }


def _rebuild(repo: Path, out: Path, *, full: bool) -> None:
    """Builds or updates the map of ``repo`` in ``out`` and prints the result.

    Both commands use the update pipeline, so that a build and a later update
    see the same files. A full build accepts a smaller graph than the one it
    replaces; an update keeps upstream's guard against silent shrinking.
    """
    from graphify.watch import _rebuild_code, _rebuild_lock

    out.mkdir(parents=True, exist_ok=True)
    with _rebuild_lock(out, blocking=False) as free:
        if not free:
            fail(f"another process is updating the map in {out}", EXIT_BUSY)
    with contextlib.redirect_stdout(sys.stderr):
        ok = _rebuild_code(repo, force=full, block_on_lock=True)
    graph_path = out / "graph.json"
    if not ok or not graph_path.is_file():
        fail(f"mapping {repo} failed; see the lines above")
    print_result(graph_summary(graph_path))


def command_build(arguments: argparse.Namespace) -> None:
    """Maps a repository from the beginning."""
    repo = _absolute_folder(arguments.repo, must_exist=True)
    out = _absolute_folder(arguments.out, must_exist=False)
    _rebuild(repo, out, full=True)


def command_update(arguments: argparse.Namespace) -> None:
    """Updates a map for the changes in a repository, or builds it when there is no map yet."""
    repo = _absolute_folder(arguments.repo, must_exist=True)
    out = _absolute_folder(arguments.out, must_exist=False)
    _rebuild(repo, out, full=not (out / "graph.json").is_file())


def command_html(arguments: argparse.Namespace) -> None:
    """Writes the interactive viewer page for a map."""
    from graphify.kodem_viewer import write_viewer

    out = _absolute_folder(arguments.out, must_exist=True)
    graph_path = out / "graph.json"
    if not graph_path.is_file():
        fail(f"no map in {out}")
    with contextlib.redirect_stdout(sys.stderr):
        html_path = write_viewer(graph_path, theme=arguments.theme, node_limit=arguments.node_limit)
    print_result({"html": str(html_path)})


def command_serve(arguments: argparse.Namespace) -> None:
    """Runs the MCP server for a map on stdio."""
    from graphify.serve import serve

    graph_path = Path(arguments.graph)
    if not graph_path.is_absolute():
        fail(f"path must be absolute: {arguments.graph}", EXIT_USAGE)
    serve(str(graph_path))


def command_version(arguments: argparse.Namespace) -> None:
    """Prints the runtime, graphify and contract versions."""
    print_result({"version": VERSION, "graphify": GRAPHIFY_VERSION, "schema": SCHEMA})


def build_parser() -> argparse.ArgumentParser:
    """Returns the parser for the commands of the contract."""
    parser = argparse.ArgumentParser(prog="kodem-codemap", description="Kodem Code Map runtime.")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="map a repository from the beginning")
    build.add_argument("repo")
    build.add_argument("--out", required=True)
    build.set_defaults(handler=command_build)

    update = commands.add_parser("update", help="update a map for the changes in a repository")
    update.add_argument("repo")
    update.add_argument("--out", required=True)
    update.set_defaults(handler=command_update)

    html = commands.add_parser("html", help="write the viewer page of a map")
    html.add_argument("--out", required=True)
    html.add_argument("--theme", choices=("dark", "light"), default="dark")
    html.add_argument("--node-limit", type=int, default=5000)
    html.set_defaults(handler=command_html)

    serve = commands.add_parser("serve", help="run the MCP server of a map on stdio")
    serve.add_argument("graph")
    serve.set_defaults(handler=command_serve)

    version = commands.add_parser("version", help="print the versions")
    version.set_defaults(handler=command_version)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Runs one command of the contract."""
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as exit_:
        raise SystemExit(EXIT_OK if exit_.code in (0, None) else EXIT_USAGE) from None
    if arguments.command in ("build", "update", "html"):
        _pin_hash_seed(argv)
    out = getattr(arguments, "out", None)
    prepare_environment(_absolute_folder(out, must_exist=False) if out else None)
    block_network()
    try:
        arguments.handler(arguments)
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - the contract reports every failure the same way
        fail(f"{type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
