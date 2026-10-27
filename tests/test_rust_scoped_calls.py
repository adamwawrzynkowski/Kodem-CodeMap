# Modified by Kodem.
"""Rust cross-file `Type::f()` and `module::f()` calls.

rust.py used to drop every qualified call it could not bind in-file (#908), so
`Matcher::new()` and `util::helper()` across files had no edge. The qualifier
path now goes to `_resolve_rust_scoped_calls`, which follows `use` bindings to
one module or type in the crate.
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


_MATCHER = (
    "pub struct Matcher;\n"
    "impl Matcher {\n"
    "    pub fn new() -> Self { Matcher }\n"
    "}\n"
)
_UTIL = "pub fn helper() -> u8 { 1 }\n"


def test_type_and_module_qualified_calls_across_two_files(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod matcher;\npub mod util;\npub mod app;\n",
        "src/matcher.rs": _MATCHER,
        "src/util.rs": _UTIL,
        "src/app.rs": (
            "use crate::matcher::Matcher;\n"
            "use crate::util;\n"
            "pub fn run() -> u8 { let _m = Matcher::new(); util::helper() }\n"
            "pub fn full() -> u8 { crate::util::helper() }\n"
        ),
    })
    assert _hit(calls, "run()", ".new()", "src/matcher.rs"), calls
    assert _hit(calls, "run()", "helper()", "src/util.rs"), calls
    assert _hit(calls, "full()", "helper()", "src/util.rs"), calls


def test_module_call_never_binds_the_same_named_local_function(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod util;\npub mod app;\n",
        "src/util.rs": _UTIL,
        "src/app.rs": (
            "use crate::util;\n"
            "fn helper() -> u8 { 2 }\n"
            "pub fn run() -> u8 { util::helper() }\n"
        ),
    })
    assert _hit(calls, "run()", "helper()", "src/util.rs"), calls
    assert not _hit(calls, "run()", "helper()", "src/app.rs"), calls


def test_std_and_external_paths_get_no_edge(tmp_path: Path):
    calls = _calls(tmp_path, {
        "src/lib.rs": "pub mod fs;\npub mod app;\n",
        "src/fs.rs": "pub fn read() -> u8 { 1 }\n",
        "src/app.rs": (
            "use std::fs;\n"
            "use regex::Regex;\n"
            "pub fn run() { let _ = fs::read(\"x\"); let _ = Regex::new(\"a\"); }\n"
        ),
        "src/other.rs": "pub struct Regex;\nimpl Regex { pub fn new() -> Self { Regex } }\n",
    })
    assert not _hit(calls, "run()", "read()", "src/fs.rs"), calls
    assert not _hit(calls, "run()", ".new()", "src/other.rs"), calls


def test_type_call_stays_in_the_callers_crate(tmp_path: Path):
    calls = _calls(tmp_path, {
        "crates/a/src/lib.rs": "pub mod config;\npub mod main;\n",
        "crates/a/src/config.rs": "pub struct Config;\nimpl Config { pub fn load() -> u8 { 0 } }\n",
        "crates/b/src/config.rs": "pub struct Config;\nimpl Config { pub fn load() -> u8 { 0 } }\n",
        "crates/a/src/main.rs": "use crate::config::Config;\npub fn go() -> u8 { Config::load() }\n",
    })
    assert _hit(calls, "go()", ".load()", "crates/a/src/config.rs"), calls
    assert not _hit(calls, "go()", ".load()", "crates/b/src/config.rs"), calls


def test_sibling_crate_by_name(tmp_path: Path):
    calls = _calls(tmp_path, {
        "crates/util/Cargo.toml": "[package]\nname = \"kit-util\"\n",
        "crates/util/src/lib.rs": "pub fn helper() -> u8 { 1 }\n",
        "crates/app/src/main.rs": "pub fn go() -> u8 { kit_util::helper() }\n",
    })
    assert _hit(calls, "go()", "helper()", "crates/util/src/lib.rs"), calls
