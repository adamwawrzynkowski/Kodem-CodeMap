# Modified by Kodem.
"""Rust trait-impl method ids carry the trait; generic impls join the type.

`impl Display for Foo { fn fmt }` and `impl Debug for Foo { fn fmt }` minted one
`fmt` node, and `impl<T> Foo<T>` minted a `Foo<T>` node of its own beside
`struct Foo`, so the struct showed none of its methods.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extractors.rust import extract_rust

_SOURCE = (
    "use std::fmt;\n"
    "pub struct Foo<T> { v: T }\n"
    "impl<T> Foo<T> { pub fn get(&self) -> &T { &self.v } }\n"
    "impl<T> fmt::Display for Foo<T> {\n"
    "    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result { Ok(()) }\n"
    "}\n"
    "impl<T> fmt::Debug for Foo<T> {\n"
    "    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result { Ok(()) }\n"
    "}\n"
)


def _result(tmp_path: Path) -> dict:
    p = tmp_path / "foo.rs"
    p.write_text(_SOURCE, encoding="utf-8")
    return extract_rust(p)


def test_display_fmt_and_debug_fmt_are_two_nodes(tmp_path: Path):
    r = _result(tmp_path)
    fmts = [n for n in r["nodes"] if n["label"] == ".fmt()"]
    assert len(fmts) == 2, fmts
    assert any("display" in n["id"] for n in fmts)
    assert any("debug" in n["id"] for n in fmts)


def test_generic_impl_methods_attach_to_the_struct(tmp_path: Path):
    r = _result(tmp_path)
    struct = next(n for n in r["nodes"] if n["label"] == "Foo")
    assert not any(n["label"].startswith("Foo<") for n in r["nodes"])
    owners = {e["source"] for e in r["edges"] if e["relation"] == "method"}
    assert owners == {struct["id"]}
    methods = {e["target"] for e in r["edges"] if e["relation"] == "method"}
    assert len(methods) == 3
