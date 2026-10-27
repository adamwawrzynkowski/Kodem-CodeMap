# Modified by Kodem.
"""Rust calls through trait-typed receivers, and trait dispatch.

A receiver typed by a trait (`&dyn Matcher`, `impl Matcher`, a `M: Matcher`
type parameter, a `Box<dyn Matcher>` field) got no edge: the type is a trait or
a generic, not a struct. The call now binds to the trait's method, and
`resolve_rust_trait_dispatch` links that method to each `impl Matcher for X`
method (`dispatches_to`).
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract

_FILES = {
    "src/lib.rs": "pub mod matcher;\npub mod regex;\npub mod app;\n",
    "src/matcher.rs": "pub trait Matcher {\n    fn find(&self) -> usize;\n}\n",
    "src/regex.rs": (
        "use crate::matcher::Matcher;\n"
        "pub struct Regex;\n"
        "impl Matcher for Regex {\n"
        "    fn find(&self) -> usize { 1 }\n"
        "}\n"
    ),
    "src/app.rs": (
        "use crate::matcher::Matcher;\n"
        "pub fn by_dyn(m: &dyn Matcher) -> usize { m.find() }\n"
        "pub fn by_impl(m: impl Matcher) -> usize { m.find() }\n"
        "pub fn by_bound<M: Matcher + Send>(m: &M) -> usize { m.find() }\n"
        "pub fn by_where<M>(m: M) -> usize where M: Matcher { m.find() }\n"
        "pub struct Holder { m: Box<dyn Matcher> }\n"
        "impl Holder { pub fn run(&self) -> usize { self.m.find() } }\n"
        "pub fn untyped<M>(m: M) -> usize { m.find() }\n"
    ),
}


def _graph(tmp_path: Path):
    paths = []
    for name, body in _FILES.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    r = extract(paths, root=tmp_path, cache_root=tmp_path / "graphify-out")
    nodes = {n["id"]: n for n in r["nodes"]}
    return nodes, r["edges"]


def _owner_label(nodes, edges, method_nid: str) -> str:
    owner = next(e["source"] for e in edges if e["relation"] == "method" and e["target"] == method_nid)
    return nodes[owner]["label"]


def test_trait_typed_receivers_bind_to_the_trait_method(tmp_path: Path):
    nodes, edges = _graph(tmp_path)
    called = {
        nodes[e["source"]]["label"]: e["target"]
        for e in edges
        if e["relation"] == "calls" and nodes.get(e["target"], {}).get("label") == ".find()"
    }
    for caller in ("by_dyn()", "by_impl()", "by_bound()", "by_where()", ".run()"):
        assert caller in called, (caller, called)
        assert _owner_label(nodes, edges, called[caller]) == "Matcher", caller
    assert "untyped()" not in called


def test_trait_method_dispatches_to_the_impl(tmp_path: Path):
    nodes, edges = _graph(tmp_path)
    dispatch = [
        (e["source"], e["target"]) for e in edges if e["relation"] == "dispatches_to"
    ]
    assert len(dispatch) == 1, dispatch
    source, target = dispatch[0]
    assert _owner_label(nodes, edges, source) == "Matcher"
    assert _owner_label(nodes, edges, target) == "Regex"
    assert nodes[target]["source_file"].endswith("src/regex.rs")
