# Modified by Kodem.
"""C# calls to methods named like other languages' builtins (#2313).

`_LANGUAGE_BUILTIN_GLOBALS` (JS `Set`/`Map`, Swift `Data`/`Color`) was checked
for every language, so a C# sibling call `Set()` got no edge in-file and no
raw call for the cross-file pass. C# now gates only `nameof`.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _calls(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    paths = []
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    r = extract(paths, root=tmp_path, cache_root=tmp_path / "graphify-out")
    nodes = {n["id"]: n for n in r["nodes"]}
    return {
        (nodes[e["source"]]["label"], nodes[e["target"]]["label"])
        for e in r["edges"]
        if e["relation"] == "calls" and e["source"] in nodes and e["target"] in nodes
    }


def test_sibling_methods_named_like_builtins(tmp_path: Path):
    calls = _calls(tmp_path, {
        "Store.cs": (
            "namespace App {\n"
            "    public class Store {\n"
            "        void Set() {}\n"
            "        void Map() {}\n"
            "        void Data() {}\n"
            "        void Color() {}\n"
            "        public void Run() { Set(); Map(); Data(); Color(); }\n"
            "    }\n"
            "}\n"
        ),
    })
    for name in ("Set", "Map", "Data", "Color"):
        assert (".Run()", f".{name}()") in calls, (name, calls)


def test_set_call_across_partial_class_halves(tmp_path: Path):
    calls = _calls(tmp_path, {
        "StoreA.cs": "namespace App {\n    public partial class Store {\n        void Set() {}\n    }\n}\n",
        "StoreB.cs": "namespace App {\n    public partial class Store {\n        public void Run() { Set(); }\n    }\n}\n",
    })
    assert (".Run()", ".Set()") in calls, calls


def test_nameof_is_never_a_call(tmp_path: Path):
    calls = _calls(tmp_path, {
        "Store.cs": (
            "namespace App {\n"
            "    public class Store {\n"
            "        string nameof(int x) { return \"\"; }\n"
            "        public string Run() { return nameof(Run); }\n"
            "    }\n"
            "}\n"
        ),
    })
    assert (".Run()", ".nameof()") not in calls, calls
