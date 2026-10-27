# Modified by Kodem.
"""Rust calls to user functions named like other languages' builtins (#2313).

The shared `_LANGUAGE_BUILTIN_GLOBALS` (Python/JS/Swift names such as `len`,
`map`, `set`) dropped Rust calls to user items of those names, and the
`_RUST_TRAIT_METHOD_BLOCKLIST` dropped `self.run()` even though `self` types the
receiver. Only Rust's own prelude stays gated, and a bare name defined more than
once binds only through a `use` of one of them.
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


def test_free_fn_named_len_called_from_another_file(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod size;\npub mod app;\n",
        "src/size.rs": "pub fn len(v: &[u8]) -> usize { v.iter().count() }\n",
        "src/app.rs": "use crate::size::len;\npub fn go() -> usize { len(&[1, 2]) }\n",
    })
    assert _hit(calls, "go()", "len()", "src/size.rs"), calls


def test_free_fn_named_map_in_the_same_file(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "fn map(x: u8) -> u8 { x }\npub fn go() -> u8 { map(1) }\n",
    })
    assert _hit(calls, "go()", "map()", "src/lib.rs"), calls


def test_self_run_across_split_impls(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod a;\npub mod b;\npub struct Job;\n",
        "src/a.rs": "use crate::Job;\nimpl Job { pub fn run(&self) {} }\n",
        "src/b.rs": "use crate::Job;\nimpl Job { pub fn go(&self) { self.run() } }\n",
    })
    assert _hit(calls, ".go()", ".run()", "src/a.rs"), calls


def test_bare_name_with_two_definitions_and_no_use_gets_no_edge(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod a;\npub mod b;\npub mod c;\n",
        "src/a.rs": "pub fn helper() -> u8 { 1 }\n",
        "src/b.rs": "pub fn helper() -> u8 { 2 }\n",
        "src/c.rs": "pub fn go() -> u8 { helper() }\n",
    })
    assert not any(s == "go()" for s, _t, _f in calls), calls


def test_bare_call_never_binds_a_method(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod a;\npub mod b;\n",
        "src/a.rs": "pub struct S;\nimpl S { pub fn compute(&self) -> u8 { 1 } }\n",
        "src/b.rs": "pub fn go() -> u8 { compute() }\n",
    })
    assert not _hit(calls, "go()", ".compute()", "src/a.rs"), calls


def test_prelude_drop_never_binds_a_corpus_fn(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod a;\npub mod b;\n",
        "src/a.rs": "pub fn drop(x: u8) -> u8 { x }\n",
        "src/b.rs": "pub fn go(v: Vec<u8>) { drop(v) }\n",
    })
    assert not _hit(calls, "go()", "drop()", "src/a.rs"), calls


def test_bare_name_in_two_crates_binds_through_its_use(tmp_path: Path):
    calls = _calls(tmp_path, {
        "crates/ui/Cargo.toml": "[package]\nname = \"ui\"\n",
        "crates/ui/src/lib.rs": "pub mod geometry;\npub use geometry::*;\n",
        "crates/ui/src/geometry.rs": "pub fn px(v: f32) -> f32 { v }\n",
        "crates/app/src/main.rs": "mod theme;\nmod view;\n",
        "crates/app/src/theme.rs": "pub fn px(v: f32) -> f32 { v * 2.0 }\n",
        "crates/app/src/view.rs": "use crate::theme::px;\npub fn draw() -> f32 { px(1.0) }\n",
        "crates/app/src/other.rs": "use ui::px;\npub fn paint() -> f32 { px(1.0) }\n",
    })
    assert _hit(calls, "draw()", "px()", "crates/app/src/theme.rs"), calls
    assert not _hit(calls, "draw()", "px()", "crates/ui/src/geometry.rs"), calls
    assert _hit(calls, "paint()", "px()", "crates/ui/src/geometry.rs"), calls
