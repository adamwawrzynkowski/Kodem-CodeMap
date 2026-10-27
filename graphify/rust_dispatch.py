# Modified by Kodem.
"""Member-level trait dispatch for Rust.

A call through a trait-typed receiver (`m: &dyn Matcher`, `m: impl Matcher`,
`fn f<M: Matcher>(m: M)`) lands on the trait's method declaration, because the
call site names only the trait. The implementing `impl Matcher for Regex`
methods are separate nodes, so a directed walk stopped at the trait. This links
each trait method to the method of every `impl Trait for X` block, through the
shared `graphify.interface_dispatch` mechanism (the C# and Swift passes use the
same one).

Both ends must be declared in a `.rs` file: a std trait (`Display`, `Iterator`)
is a sourceless stub and never passes the declared-in guard. An implementer
node that holds two methods of one name (a type implementing two traits that
both declare `fmt` in one file) is ambiguous and skipped.
"""
from __future__ import annotations

from graphify.interface_dispatch import resolve_interface_dispatch

_RUST_SUFFIXES = (".rs",)


def resolve_rust_trait_dispatch(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Link each Rust trait method to every implementing method that owns it unambiguously."""
    resolve_interface_dispatch(per_file, all_nodes, all_edges, suffixes=_RUST_SUFFIXES)
