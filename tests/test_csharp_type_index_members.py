# Modified by Kodem.
"""C# property and enum-member nodes stay out of the type-definition index (#3212).

`_build_csharp_type_def_index` took every sourced `.cs` node with a plain label
as a type, so `public Widget Widget { get; set; }` or an enum member `Standard`
competed with the type of that name, and the earlier-sorting file won every
reference to it across the corpus.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract
from graphify.extractors.csharp import _build_csharp_type_def_index


def _graph(tmp_path: Path, files: dict[str, str]):
    paths = []
    for name, body in files.items():
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    r = extract(paths, root=tmp_path, cache_root=tmp_path / "graphify-out")
    return {n["id"]: n for n in r["nodes"]}, r["edges"]


def _ref_targets(nodes, edges, source_label: str, target_label: str) -> list[dict]:
    return [
        nodes[e["target"]] for e in edges
        if e["relation"] == "references"
        and nodes.get(e["source"], {}).get("label") == source_label
        and nodes.get(e["target"], {}).get("label") == target_label
    ]


def test_property_named_like_its_type_does_not_capture_references(tmp_path: Path):
    nodes, edges = _graph(tmp_path, {
        "holder.cs": "namespace App;\npublic class Holder { public Widget Widget { get; set; } }\n",
        "widget.cs": "namespace App;\npublic class Widget {}\n",
        "other.cs": "namespace App;\npublic class Other { public Widget Thing { get; set; } }\n",
    })
    for source in ("Holder", "Other"):
        targets = _ref_targets(nodes, edges, source, "Widget")
        assert targets, (source, targets)
        assert all(Path(t["source_file"]).name == "widget.cs" for t in targets), (source, targets)


def test_enum_member_named_like_a_type_does_not_capture_references(tmp_path: Path):
    nodes, edges = _graph(tmp_path, {
        "abc.cs": "namespace App;\npublic enum ProductType { Standard, Bundle }\n",
        "zeta.cs": "namespace App;\npublic class Standard {}\n",
        "user.cs": "namespace App;\npublic class User { public Standard Plan { get; set; } }\n",
    })
    targets = _ref_targets(nodes, edges, "User", "Standard")
    assert targets and all(Path(t["source_file"]).name == "zeta.cs" for t in targets), targets


def test_index_without_edges_still_skips_member_nodes():
    nodes = [
        {"id": "holder_widget", "label": "Widget", "source_file": "a.cs", "file_type": "code",
         "source_location": "L1", "metadata": {"csharp_member": "property"}},
        {"id": "widget", "label": "Widget", "source_file": "b.cs", "file_type": "code",
         "source_location": "L1"},
    ]
    assert _build_csharp_type_def_index(nodes) == {("", "Widget"): "widget"}
