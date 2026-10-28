# Modified by Kodem.
"""Linked maps: the code of other folders that a project links, served in one graph.

A project can use code that lives outside its repository, such as a Unity
package referenced as ``file:../../other-repo/Packages/x`` or a Cargo path
dependency. Kodem maps each linked folder on its own and starts the server with
``serve <graph> --linked <graph> ...``; the server then answers from the
project's map and every linked map together (FORK.md, "Linked maps").

The merge keeps the project's map as it is and adds each linked map:

- Its node identifiers get the name of its folder as a prefix, so that two maps
  with the same relative paths do not collide.
- Its paths become absolute, so that an agent in the project can open them.
- Its communities follow the project's, so that their numbers stay apart.

A name that a map could not resolve becomes a node without a file. When exactly
one other map defines that name as a type or a function, and the name's own map
does not, the merge points the name's edges at that definition, so that a call
from the project into a linked folder connects them. Namespaces, fields, tests,
mocks and folders that Unity hides (``Name~``) never count as definitions.
Member calls into a linked folder, which the extractors park on the caller,
are finished by upstream's cross-repository pass (graphify/cross_repo_calls.py).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import networkx as nx

LINKED_VARIABLE = "GRAPHIFY_KODEM_LINKED"
ROOT_MARKER = ".graphify_root"
# The repository tag of the project's own nodes, which upstream's cross-repository pass reads.
PROJECT_REPO = "kodem-project"
# Edges that a merge points at a definition in another map carry this mark.
LINKED_EDGE = "_kodem_linked"
# Folders whose code is no definition that a project calls: tests and stand-ins.
_SKIPPED_FOLDERS = frozenset(("test", "tests", "testing", "__tests__", "mock", "mocks", "__mocks__", "fixtures"))


def linked_graphs() -> list[str]:
    """Returns the linked maps that ``GRAPHIFY_KODEM_LINKED`` names, in its order."""
    raw = os.environ.get(LINKED_VARIABLE, "")
    return [part for part in raw.split(os.pathsep) if part]


def cache_key(paths: list[str]) -> tuple:
    """Returns the size and modification time of each linked map, or None for a missing one,
    so that the server loads the graph again when a linked map changes."""
    key = []
    for path in paths:
        try:
            stat = Path(path).stat()
            key.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            key.append(None)
    return tuple(key)


def _root(graph_path: Path) -> Path | None:
    """Returns the folder that a map was made from, which its build writes next to it."""
    try:
        text = (graph_path.parent / ROOT_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


def _absolute(root: Path, source: object) -> object:
    """Returns a path of a linked map as an absolute path; an empty or absolute one stays."""
    if not isinstance(source, str) or not source or os.path.isabs(source):
        return source
    return str(root / source)


def _prefix(root: Path, taken: set[str]) -> str:
    """Returns a prefix for the identifiers of a linked map that no other map uses."""
    base = root.name or "linked"
    name, number = base, 2
    while name in taken:
        name = f"{base}-{number}"
        number += 1
    taken.add(name)
    return f"{name}:"


def merge(graph: nx.Graph, paths: list[str], read: Callable[[str], nx.Graph]) -> nx.Graph:
    """Adds the linked maps at ``paths`` to ``graph`` and connects names across them.

    ``read`` loads one map. A linked map that is missing, unreadable or has no
    recorded folder is left out, so that a project's own map always serves.
    """
    owner: dict[str, int] = {node: 0 for node in graph.nodes}
    communities = [data.get("community") for _, data in graph.nodes(data=True)]
    offset = max((int(c) for c in communities if isinstance(c, int)), default=-1) + 1
    taken: set[str] = set()
    for index, path in enumerate(paths, start=1):
        root = _root(Path(path))
        if root is None:
            continue
        try:
            linked = read(path)
        except Exception:  # noqa: BLE001 - a broken linked map must not stop the server
            continue
        prefix = _prefix(root, taken)
        highest = -1
        for node, data in linked.nodes(data=True):
            data = dict(data)
            data["source_file"] = _absolute(root, data.get("source_file"))
            community = data.get("community")
            if isinstance(community, int):
                highest = max(highest, community)
                data["community"] = community + offset
            data["repo"] = prefix[:-1]
            graph.add_node(prefix + node, **data)
            owner[prefix + node] = index
        edges = linked.edges(keys=True, data=True) if linked.is_multigraph() else linked.edges(data=True)
        for edge in edges:
            source, target, data = edge[0], edge[1], dict(edge[-1])
            data["source_file"] = _absolute(root, data.get("source_file"))
            graph.add_edge(prefix + source, prefix + target, **data)
        offset += highest + 1
    if len(set(owner.values())) > 1:
        for node, index in owner.items():
            if index == 0:
                graph.nodes[node]["repo"] = PROJECT_REPO
        _connect(graph, owner)
        from graphify.cross_repo_calls import link_cross_repo_member_calls

        link_cross_repo_member_calls(graph)
    return graph


def _name(data: dict) -> str:
    """Returns a node's name without the ``()`` that marks a function."""
    name = data.get("norm_label")
    if not isinstance(name, str):
        return ""
    return name.removesuffix("()").lstrip(".")


def _connect(graph: nx.Graph, owner: dict[str, int]) -> None:
    """Points each name that its map left without a file at the one definition of that name
    in another map, when the name's own map has none."""
    definitions: dict[str, set[int]] = {}
    defined_in: dict[tuple[str, int], list[str]] = {}
    unresolved: list[str] = []
    for node, data in graph.nodes(data=True):
        name = _name(data)
        if not name:
            continue
        if data.get("source_file"):
            if _definition(data):
                definitions.setdefault(name, set()).add(owner[node])
                defined_in.setdefault((name, owner[node]), []).append(node)
        else:
            unresolved.append(node)
    for node in unresolved:
        name = _name(graph.nodes[node])
        maps = definitions.get(name, set())
        if owner[node] in maps:
            continue
        others = [defined_in[(name, index)] for index in maps]
        if len(others) != 1 or len(others[0]) != 1:
            continue
        _redirect(graph, node, others[0][0])


def _definition(data: dict) -> bool:
    """Reports whether a node defines a type or a function that other code can call."""
    if data.get("file_type") != "code" or not data.get("_callable"):
        return False
    folders = Path(str(data.get("source_file"))).parts[:-1]
    return not any(part.endswith("~") or part.lower() in _SKIPPED_FOLDERS for part in folders)


def _redirect(graph: nx.Graph, node: str, target: str) -> None:
    """Moves every edge of ``node`` to ``target`` and removes ``node``."""
    multi = graph.is_multigraph()
    incoming = list(graph.in_edges(node, keys=True, data=True) if multi else graph.in_edges(node, data=True))
    outgoing = list(graph.out_edges(node, keys=True, data=True) if multi else graph.out_edges(node, data=True))
    for edge in incoming:
        if edge[0] != node:
            graph.add_edge(edge[0], target, **{**edge[-1], LINKED_EDGE: True})
    for edge in outgoing:
        if edge[1] != node:
            graph.add_edge(target, edge[1], **{**edge[-1], LINKED_EDGE: True})
    graph.remove_node(node)
