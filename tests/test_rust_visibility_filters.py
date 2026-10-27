# Modified by Kodem.
"""Rust cross-file calls respect visibility and test scope (#2774).

A private `fn string()` in one file took the bare `string(..)` calls of other
files, and a `#[cfg(test)]` helper took the calls of production code. A call
now binds only to an item Rust lets the caller reach.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _calls(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str]]:
    paths = []
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    r = extract(paths, root=tmp_path, cache_root=tmp_path / "graphify-out")
    nodes = {n["id"]: n for n in r["nodes"]}
    return {
        (nodes[e["source"]]["label"], nodes[e["target"]]["label"],
         Path(str(nodes[e["target"]].get("source_file", ""))).as_posix())
        for e in r["edges"]
        if e["relation"] == "calls" and e["source"] in nodes and e["target"] in nodes
    }


def _hit(calls, caller: str, callee: str, in_file: str) -> bool:
    return any(s == caller and t == callee and f.endswith(in_file) for s, t, f in calls)


def test_private_fn_in_another_file_gets_no_edge(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod a;\npub mod b;\n",
        "src/a.rs": "fn string(v: u8) -> u8 { v }\npub fn integer(v: u8) -> u8 { v }\n",
        "src/b.rs": "pub fn go() -> u8 { string(1) + integer(2) }\n",
    })
    assert not _hit(calls, "go()", "string()", "src/a.rs"), calls
    assert _hit(calls, "go()", "integer()", "src/a.rs"), calls


def test_private_fn_is_visible_to_a_child_module_file(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod a;\n",
        "src/a.rs": "pub mod b;\nfn helper() -> u8 { 1 }\n",
        "src/a/b.rs": "pub fn go() -> u8 { super::helper() }\n",
    })
    assert _hit(calls, "go()", "helper()", "src/a.rs"), calls


def test_pub_crate_fn_stays_in_its_crate(tmp_path: Path):
    calls = _calls(tmp_path, {
        "crates/util/Cargo.toml": "[package]\nname = \"util\"\n",
        "crates/util/src/lib.rs": "pub(crate) fn helper() -> u8 { 1 }\npub fn open() -> u8 { 2 }\n",
        "crates/app/src/main.rs": "pub fn go() -> u8 { util::helper() + util::open() }\n",
    })
    assert not _hit(calls, "go()", "helper()", "crates/util/src/lib.rs"), calls
    assert _hit(calls, "go()", "open()", "crates/util/src/lib.rs"), calls


def test_test_only_helper_never_takes_a_production_call(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod engine;\npub mod app;\n",
        "src/engine.rs": (
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    pub fn consent() -> u8 { 1 }\n"
            "    #[test]\n"
            "    fn uses() { consent(); }\n"
            "}\n"
        ),
        "src/app.rs": "pub fn go() -> u8 { consent() }\n",
    })
    assert not _hit(calls, "go()", "consent()", "src/engine.rs"), calls
    assert _hit(calls, "uses()", "consent()", "src/engine.rs"), calls


def test_private_method_of_a_typed_receiver_in_another_module(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod m;\npub mod app;\n",
        "src/m.rs": "pub struct M;\nimpl M { fn secret(&self) -> u8 { 1 } pub fn open(&self) -> u8 { 2 } }\n",
        "src/app.rs": "use crate::m::M;\npub fn go(x: &M) -> u8 { x.secret() + x.open() }\n",
    })
    assert not _hit(calls, "go()", ".secret()", "src/m.rs"), calls
    assert _hit(calls, "go()", ".open()", "src/m.rs"), calls
