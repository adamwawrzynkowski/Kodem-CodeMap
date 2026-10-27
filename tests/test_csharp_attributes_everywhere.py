# Modified by Kodem.
"""C# attributes on types, fields, properties and parameters.

Only method attributes were read. Unity and DOTS code puts most of its
attributes elsewhere: `[RequireComponent(typeof(Rigidbody))]` and
`[UpdateInGroup(typeof(SimulationSystemGroup))]` on types, `[SerializeField]`
on fields. Each is now a `references` edge (context "attribute"), each
`typeof(X)` argument one more (context "attribute_arg"), and `[Foo]` binds to a
declared `FooAttribute`.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract

_FILES = {
    "Attrs.cs": (
        "using System;\n"
        "namespace Game {\n"
        "    public class CachedAttribute : Attribute {}\n"
        "    public class TrackedAttribute : Attribute {}\n"
        "    public class NoteAttribute : Attribute {}\n"
        "    public class FromQueryAttribute : Attribute {}\n"
        "    public class SimulationSystemGroup {}\n"
        "    public class Rigidbody {}\n"
        "}\n"
    ),
    "Player.cs": (
        "namespace Game {\n"
        "    [Cached, RequireComponent(typeof(Rigidbody))]\n"
        "    [UpdateInGroup(typeof(SimulationSystemGroup))]\n"
        "    public class Player {\n"
        "        [Tracked] private int _hp;\n"
        "        [Note] public int Score { get; set; }\n"
        "        public void Hit([FromQuery] int amount) {}\n"
        "    }\n"
        "}\n"
    ),
}


def _refs(tmp_path: Path) -> set[tuple[str, str, str]]:
    paths = []
    for name, body in _FILES.items():
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    r = extract(paths, root=tmp_path, cache_root=tmp_path / "graphify-out")
    nodes = {n["id"]: n for n in r["nodes"]}
    return {
        (nodes[e["source"]]["label"], e.get("context", ""), nodes[e["target"]]["label"])
        for e in r["edges"]
        if e["relation"] == "references" and e["source"] in nodes and e["target"] in nodes
        and e.get("context") in ("attribute", "attribute_arg")
    }


def test_type_attributes_and_typeof_arguments(tmp_path: Path):
    refs = _refs(tmp_path)
    assert ("Player", "attribute", "CachedAttribute") in refs, refs
    assert ("Player", "attribute", "RequireComponent") in refs, refs
    assert ("Player", "attribute", "UpdateInGroup") in refs, refs
    assert ("Player", "attribute_arg", "Rigidbody") in refs, refs
    assert ("Player", "attribute_arg", "SimulationSystemGroup") in refs, refs


def test_field_property_and_parameter_attributes(tmp_path: Path):
    refs = _refs(tmp_path)
    assert ("Player", "attribute", "TrackedAttribute") in refs, refs
    assert ("Score", "attribute", "NoteAttribute") in refs, refs
    assert (".Hit()", "attribute", "FromQueryAttribute") in refs, refs
