# Modified by Kodem.
"""Tests of linked maps: ``serve <graph> --linked <graph>`` answers from both maps."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import networkx as nx
import pytest

from graphify import kodem_linked

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def _environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("PYTEST")}


def _build(repo: Path, out: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "graphify.kodem_main", "build", str(repo), "--out", str(out)],
        check=True,
        capture_output=True,
        env=_environment(),
        timeout=300,
    )
    return out / "graph.json"


@pytest.fixture(scope="module")
def maps(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """A C# project that uses a class of a linked folder, and the maps of both."""
    root = tmp_path_factory.mktemp("linked")
    project = root / "project"
    (project / "Assets").mkdir(parents=True)
    (project / "Assets" / "Boot.cs").write_text(
        "using Auth;\n"
        "namespace Game {\n"
        "public class Boot {\n"
        "    public void Start() {\n"
        "        LoginService service = new LoginService();\n"
        "        service.Login();\n"
        "    }\n"
        "}\n"
        "}\n",
        encoding="utf-8",
    )
    library = root / "library"
    (library / "Packages" / "auth").mkdir(parents=True)
    (library / "Packages" / "auth" / "LoginService.cs").write_text(
        "namespace Auth {\n"
        "public class LoginService {\n"
        "    public void Login() { Refresh(); }\n"
        "    private void Refresh() { }\n"
        "}\n"
        "}\n",
        encoding="utf-8",
    )
    return _build(project, root / "project-map"), _build(library, root / "library-map"), library


async def _call(graph: Path, options: list[str], name: str, arguments: dict) -> str:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphify.kodem_main", "serve", str(graph), *options],
        env=_environment(),
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            return "\n".join(part.text for part in result.content if hasattr(part, "text"))


def test_a_call_into_a_linked_folder_is_a_neighbor(maps: tuple[Path, Path, Path]) -> None:
    project, library, _ = maps
    text = asyncio.run(_call(project, ["--linked", str(library)], "get_neighbors", {"label": "Start"}))
    assert "Login() [calls]" in text


def test_a_member_call_into_a_linked_folder_is_a_call(maps: tuple[Path, Path, Path]) -> None:
    project, library, library_root = maps
    text = asyncio.run(_call(project, ["--linked", str(library)], "get_neighbors", {"label": "Login"}))
    assert "Start" in text
    assert "Refresh" in text
    assert str(library_root / "Packages" / "auth" / "LoginService.cs") in text


def test_without_linked_maps_the_call_stays_unresolved(maps: tuple[Path, Path, Path]) -> None:
    project, _, library_root = maps
    text = asyncio.run(_call(project, [], "get_neighbors", {"label": "Start"}))
    assert "Login" not in text
    assert str(library_root) not in text


def test_a_linked_path_must_be_absolute(maps: tuple[Path, Path, Path]) -> None:
    project, _, _ = maps
    finished = subprocess.run(
        [sys.executable, "-m", "graphify.kodem_main", "serve", str(project), "--linked", "relative/graph.json"],
        capture_output=True,
        env=_environment(),
        timeout=60,
    )
    assert finished.returncode == 2


def _graph(nodes: list[tuple[str, dict]], edges: list[tuple[str, str, dict]]) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for node, data in nodes:
        graph.add_node(node, **data)
    for source, target, data in edges:
        graph.add_edge(source, target, **data)
    return graph


def _linked_map(tmp_path: Path, graph: nx.MultiDiGraph, root: str) -> tuple[Path, dict[str, nx.MultiDiGraph]]:
    folder = tmp_path / root
    folder.mkdir()
    (folder / kodem_linked.ROOT_MARKER).write_text(str(tmp_path / "repos" / root), encoding="utf-8")
    path = folder / "graph.json"
    return path, {str(path): graph}


def _code(source: str, name: str, **extra: object) -> dict:
    return {"source_file": source, "norm_label": name, "label": name, "file_type": "code", "community": 0, **extra}


def test_names_link_only_to_one_callable_definition_outside_tests(tmp_path: Path) -> None:
    project = _graph(
        [
            ("caller", _code("app/main.cs", "caller", _callable=True)),
            ("session", {"source_file": "", "norm_label": "session", "label": "Session", "file_type": "code"}),
            ("engine", {"source_file": "", "norm_label": "engine", "label": "Engine", "file_type": "code"}),
            ("mocked", {"source_file": "", "norm_label": "mocked", "label": "Mocked", "file_type": "code"}),
            ("twice", {"source_file": "", "norm_label": "twice", "label": "Twice", "file_type": "code"}),
        ],
        [("caller", target, {"relation": "references"}) for target in ("session", "engine", "mocked", "twice")],
    )
    linked = _graph(
        [
            ("session", _code("Auth/Session.cs", "session", _callable=True, _callable_class=True)),
            ("engine", _code("Auth/Engine.cs", "engine")),
            ("mocked", _code("Gen/Source~/Mock/Mocked.cs", "mocked", _callable=True)),
            ("twice-a", _code("A/Twice.cs", "twice", _callable=True)),
            ("twice-b", _code("B/Twice.cs", "twice", _callable=True)),
        ],
        [],
    )
    path, graphs = _linked_map(tmp_path, linked, "library")
    merged = kodem_linked.merge(project, [str(path)], graphs.__getitem__)
    assert merged.has_edge("caller", "library:session")
    assert "session" not in merged
    assert merged.nodes["library:session"]["source_file"] == str(tmp_path / "repos" / "library" / "Auth" / "Session.cs")
    for unresolved in ("engine", "mocked", "twice"):
        assert unresolved in merged


def test_a_linked_map_without_its_folder_is_left_out(tmp_path: Path) -> None:
    project = _graph([("caller", _code("app/main.cs", "caller", _callable=True))], [])
    folder = tmp_path / "unknown"
    folder.mkdir()
    path = folder / "graph.json"
    merged = kodem_linked.merge(project, [str(path)], {str(path): _graph([], [])}.__getitem__)
    assert list(merged.nodes) == ["caller"]


def test_two_linked_folders_with_one_name_get_distinct_prefixes(tmp_path: Path) -> None:
    project = _graph([("caller", _code("app/main.cs", "caller", _callable=True))], [])
    graphs: dict[str, nx.MultiDiGraph] = {}
    paths = []
    for index in (1, 2):
        folder = tmp_path / f"map{index}"
        folder.mkdir()
        (folder / kodem_linked.ROOT_MARKER).write_text(str(tmp_path / f"place{index}" / "shared"), encoding="utf-8")
        path = folder / "graph.json"
        graphs[str(path)] = _graph([("node", _code("x.cs", f"name{index}", community=3))], [])
        paths.append(str(path))
    merged = kodem_linked.merge(project, paths, graphs.__getitem__)
    assert {"shared:node", "shared-2:node"} <= set(merged.nodes)
    assert merged.nodes["shared:node"]["community"] != merged.nodes["shared-2:node"]["community"]
