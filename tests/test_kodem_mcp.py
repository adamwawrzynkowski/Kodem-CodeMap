# Modified by Kodem.
"""Tests of the MCP server that Kodem starts with ``graphify.kodem_main serve``.

The server runs in a child process and is driven by the MCP SDK's own stdio
client, as an agent would drive it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from graphify import serve  # noqa: E402

# The whole tool list an agent receives, in characters of JSON. Upstream's ten
# tools with the project_path parameter came to about 6,500.
TOOL_LIST_LIMIT = 1500


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A built map of a small Python project with several files."""
    root = tmp_path_factory.mktemp("mcp")
    repo = root / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "service.py").write_text(
        "from app.store import save\n\n"
        "def handle(request):\n    return validate(request) and save(request)\n\n"
        "def validate(request):\n    return bool(request)\n",
        encoding="utf-8",
    )
    (repo / "app" / "store.py").write_text(
        "def save(item):\n    return write(item)\n\ndef write(item):\n    return item\n",
        encoding="utf-8",
    )
    out = root / "map"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST")}
    subprocess.run(
        [sys.executable, "-m", "graphify.kodem_main", "build", str(repo), "--out", str(out)],
        check=True,
        capture_output=True,
        env=environment,
        timeout=300,
    )
    return out / "graph.json"


async def _session_calls(
    graph_path: Path, calls: list[tuple[str, dict]], options: tuple[str, ...] = ()
) -> tuple[object, list, list[str]]:
    """Lists the tools and runs ``calls`` against a served map; returns the init result, tools and texts."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST")}
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphify.kodem_main", "serve", str(graph_path), *options],
        env=environment,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = (await session.list_tools()).tools
            texts = []
            for name, arguments in calls:
                result = await session.call_tool(name, arguments)
                texts.append("\n".join(part.text for part in result.content if hasattr(part, "text")))
            return initialized, tools, texts


def test_the_server_offers_four_small_tools_and_instructions(graph: Path) -> None:
    initialized, tools, _ = asyncio.run(_session_calls(graph, []))
    assert [tool.name for tool in tools] == list(serve.KODEM_TOOL_NAMES)
    size = len(json.dumps([tool.model_dump(mode="json", exclude_none=True) for tool in tools]))
    assert size < TOOL_LIST_LIMIT, size
    assert all("project_path" not in json.dumps(tool.model_dump(mode="json")) for tool in tools)
    assert initialized.server_info.name == serve.KODEM_SERVER_NAME
    assert initialized.instructions == serve.KODEM_INSTRUCTIONS


def test_answers_are_compact(graph: Path) -> None:
    _, _, texts = asyncio.run(
        _session_calls(
            graph,
            [
                ("query_graph", {"question": "how does handle save a request"}),
                ("get_neighbors", {"label": "save"}),
                ("shortest_path", {"source": "handle", "target": "write"}),
                ("get_node", {"label": "validate"}),
            ],
        )
    )
    query, neighbors, path, node = texts
    node_lines = [line for line in query.splitlines() if line.startswith("NODE ")]
    assert node_lines, query
    assert "src=" not in query and "community=" not in query
    assert "write" in path
    assert "save" in neighbors
    assert "validate" in node


def test_the_activity_log_records_each_call_and_what_it_asked(graph: Path, tmp_path: Path) -> None:
    activity = tmp_path / "activity.jsonl"
    before = int(time.time() * 1000)
    asyncio.run(
        _session_calls(
            graph,
            [("get_node", {"label": "validate"}), ("shortest_path", {"source": "handle", "target": "write"})],
            ("--activity", str(activity)),
        )
    )
    lines = [json.loads(line) for line in activity.read_text(encoding="utf-8").splitlines()]
    assert [(line["tool"], line["busy"]) for line in lines] == [
        ("get_node", True),
        ("get_node", False),
        ("shortest_path", True),
        ("shortest_path", False),
    ]
    assert lines[0]["subject"] == "validate" and lines[2]["subject"] == "handle → write"
    assert lines[0]["call"] == lines[1]["call"] != lines[2]["call"]
    assert all(line["at"] >= before for line in lines)
    assert [path.name for path in tmp_path.iterdir()] == ["activity.jsonl"]


def test_a_full_activity_log_moves_aside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    activity = tmp_path / "activity.jsonl"
    activity.write_text("x" * (serve._KODEM_ACTIVITY_LIMIT + 1), encoding="utf-8")
    monkeypatch.setenv("GRAPHIFY_KODEM_ACTIVITY", str(activity))
    serve.record_activity("1:1", "query_graph", "a " * 200, True)
    assert (tmp_path / "activity.jsonl.1").stat().st_size > serve._KODEM_ACTIVITY_LIMIT
    assert json.loads(activity.read_text(encoding="utf-8"))["tool"] == "query_graph"
    assert len(serve.activity_subject("query_graph", {"question": "a " * 200})) == serve._KODEM_SUBJECT_LIMIT


def test_hidden_tools_leave_no_activity(graph: Path, tmp_path: Path) -> None:
    activity = tmp_path / "activity.jsonl"
    asyncio.run(_session_calls(graph, [("list_prs", {})], ("--activity", str(activity))))
    assert not activity.exists()


def test_the_activity_path_must_be_absolute(graph: Path) -> None:
    from graphify import kodem_main

    process = subprocess.run(
        [sys.executable, "-m", "graphify.kodem_main", "serve", str(graph), "--activity", "relative.jsonl"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == kodem_main.EXIT_USAGE


def test_the_tools_ask_claude_code_to_load_them_up_front(graph: Path) -> None:
    _, tools, _ = asyncio.run(_session_calls(graph, []))
    assert all((tool.meta or {}).get("anthropic/alwaysLoad") is True for tool in tools)


def test_an_ambiguous_name_answers_for_each_place(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "a").mkdir(parents=True)
    (repo / "b").mkdir(parents=True)
    (repo / "a" / "one.py").write_text("def start():\n    return go()\n\ndef go():\n    return 1\n", encoding="utf-8")
    (repo / "b" / "two.py").write_text("def start():\n    return stop()\n\ndef stop():\n    return 2\n", encoding="utf-8")
    out = tmp_path / "map"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST")}
    subprocess.run(
        [sys.executable, "-m", "graphify.kodem_main", "build", str(repo), "--out", str(out)],
        check=True, capture_output=True, env=environment, timeout=300,
    )
    _, _, texts = asyncio.run(_session_calls(out / "graph.json", [("get_neighbors", {"label": "start"})]))
    answer = texts[0]
    assert "a/one.py" in answer and "b/two.py" in answer
    assert "go()" in answer and "stop()" in answer
    assert not answer.startswith("Ambiguous")


def test_hidden_upstream_tools_cannot_be_called(graph: Path) -> None:
    _, _, texts = asyncio.run(_session_calls(graph, [("list_prs", {}), ("graph_stats", {})]))
    assert texts == ["Unknown tool: list_prs", "Unknown tool: graph_stats"]


def test_compact_nodes_are_grouped_by_file_after_the_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import networkx as nx

    monkeypatch.setenv("GRAPHIFY_KODEM_MCP", "1")
    G = nx.Graph()
    for nid, source, line in (("a", "f1.py", 1), ("b", "f2.py", 2), ("c", "f1.py", 3), ("d", "f2.py", 4)):
        G.add_node(nid, label=nid, source_file=source, source_location=f"L{line}", community=0)
    G.add_edge("a", "b", relation="calls", confidence="EXTRACTED", source_file="f1.py", source_location="L1")
    G.add_edge("b", "c", relation="calls", confidence="INFERRED", source_file="f2.py", source_location="L2")
    G.add_edge("c", "d", relation="calls", confidence="EXTRACTED", source_file="f1.py", source_location="L3")
    text = serve._subgraph_to_text(G, {"a", "b", "c", "d"}, [("a", "b"), ("b", "c"), ("c", "d")], seeds=["a"])
    assert text.splitlines() == [
        "NODE a f1.py:L1",
        "NODE c L3",
        "NODE b f2.py:L2",
        "NODE d L4",
        "EDGE a --calls--> b L1",
        "EDGE b --calls--> c L2 [INFERRED]",
        "EDGE c --calls--> d L3",
    ]
