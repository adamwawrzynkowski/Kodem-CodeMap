# Modified by Kodem.
"""Calls inside a macro's arguments.

tree-sitter keeps a macro's arguments as a raw token tree, so
`json!({"rules": provider::instructions(t)})` used to hide its call: a Code Map of
Kodem missed two of the five callers of `provider::instructions`. The token tree's
`f(...)` and `path::f(...)` now bind like call expressions.
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


_PROVIDER = "pub fn instructions(n: u32) -> String { n.to_string() }\n"
_CRATE = {
    "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
    "src/lib.rs": "pub mod provider;\npub mod codex;\npub mod grok;\n",
    "src/provider.rs": _PROVIDER,
}


def test_a_bare_call_in_a_macro_binds_in_the_same_file(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {"src/lib.rs": (
        "fn helper(x: u32) -> u32 { x }\n"
        "fn run() { println!(\"{}\", helper(1)); }\n"
    )})
    assert _hit(calls, "run()", "helper()", "src/lib.rs")


def test_an_imported_call_in_a_macro_binds_across_files(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {**_CRATE,
        "src/codex.rs": (
            "use crate::provider::instructions;\n"
            "pub fn start() -> String { format!(\"{}\", instructions(1)) }\n"
        ),
        "src/grok.rs": (
            "use crate::provider;\n"
            "pub fn open() -> String { format!(\"{}\", provider::instructions(2)) }\n"
        ),
    })
    assert _hit(calls, "start()", "instructions()", "src/provider.rs")
    assert _hit(calls, "open()", "instructions()", "src/provider.rs")


def test_nested_token_trees_and_type_calls_bind(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {"src/lib.rs": (
        "pub struct Plan;\n"
        "impl Plan { pub fn new() -> Self { Plan } }\n"
        "fn leaf(x: u32) -> u32 { x }\n"
        "fn run() { vec![[leaf(1)], [leaf(2)]]; assert!(matches!(Plan::new(), Plan)); }\n"
    )})
    assert _hit(calls, "run()", "leaf()", "src/lib.rs")
    assert _hit(calls, "run()", ".new()", "src/lib.rs")


def test_methods_nested_macros_and_fn_names_in_a_macro_are_not_calls(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {"src/lib.rs": (
        "fn len(x: u32) -> u32 { x }\n"
        "fn helper(x: u32) -> u32 { x }\n"
        "macro_rules! wrap { ($e:expr) => { $e }; }\n"
        "fn run(v: Vec<u32>) { wrap!(v.len()); println!(\"{}\", helper!(1)); }\n"
    )})
    assert not _hit(calls, "run()", "len()", "src/lib.rs")
    assert not _hit(calls, "run()", "helper()", "src/lib.rs")
