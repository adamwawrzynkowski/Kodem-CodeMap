# Modified by Kodem.
"""Rust items inside inline `mod` blocks get their own ids (#2774, #4285).

walk() had no `mod_item` branch, so `mod inner { fn helper }` and a top-level
`fn helper` shared one node, and a `#[cfg(test)]` helper absorbed the calls of
the production function of the same name. Items now carry their mod path in
the id, and `_rust_mod_path`, `_rust_vis` and `_rust_cfg_test` as metadata.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract
from graphify.extractors.rust import extract_rust

_SOURCE = (
    "pub fn helper() -> u8 { 1 }\n"
    "pub(crate) struct Thing;\n"
    "mod inner {\n"
    "    fn helper() -> u8 { 2 }\n"
    "    pub fn call_own() -> u8 { helper() }\n"
    "    pub fn call_parent() -> u8 { super::helper() }\n"
    "}\n"
    "#[cfg(test)]\n"
    "mod tests {\n"
    "    use super::*;\n"
    "    fn fixture() -> u8 { helper() }\n"
    "    #[test]\n"
    "    fn it_works() { fixture(); }\n"
    "}\n"
    "pub fn top() -> u8 { inner::call_own() }\n"
)


def _nodes(tmp_path: Path) -> tuple[dict, list[dict]]:
    p = tmp_path / "src" / "lib.rs"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_SOURCE, encoding="utf-8")
    r = extract([p], root=tmp_path, cache_root=tmp_path / "graphify-out")
    return {n["id"]: n for n in r["nodes"]}, r["edges"]


def test_mod_item_and_top_level_helper_are_two_nodes(tmp_path: Path):
    nodes, _edges = _nodes(tmp_path)
    helpers = [n for n in nodes.values() if n["label"] == "helper()"]
    assert len(helpers) == 2, helpers
    assert sorted(n.get("_rust_mod_path", "") for n in helpers) == ["", "inner"]


def test_calls_bind_by_module_scope(tmp_path: Path):
    nodes, edges = _nodes(tmp_path)
    calls = {
        (nodes[e["source"]]["label"], nodes[e["source"]].get("_rust_mod_path", ""),
         nodes[e["target"]]["label"], nodes[e["target"]].get("_rust_mod_path", ""))
        for e in edges if e["relation"] == "calls"
    }
    assert ("call_own()", "inner", "helper()", "inner") in calls, calls
    assert ("call_parent()", "inner", "helper()", "") in calls, calls
    assert ("fixture()", "tests", "helper()", "") in calls, calls
    assert ("top()", "", "call_own()", "inner") in calls, calls


def test_visibility_and_test_metadata(tmp_path: Path):
    p = tmp_path / "lib.rs"
    p.write_text(_SOURCE, encoding="utf-8")
    by_label: dict[str, list[dict]] = {}
    for n in extract_rust(p)["nodes"]:
        by_label.setdefault(n["label"], []).append(n)
    top_helper = next(n for n in by_label["helper()"] if not n.get("_rust_mod_path"))
    inner_helper = next(n for n in by_label["helper()"] if n.get("_rust_mod_path"))
    assert top_helper["_rust_vis"] == "pub"
    assert inner_helper["_rust_vis"] == "priv"
    assert by_label["Thing"][0]["_rust_vis"] == "pub(crate)"
    assert by_label["fixture()"][0].get("_rust_cfg_test") is True
    assert by_label["it_works()"][0].get("_rust_cfg_test") is True
    assert not top_helper.get("_rust_cfg_test")
