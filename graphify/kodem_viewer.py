# Modified by Kodem.
"""The Code Map viewer: graphify's graph.html, made to work offline inside Kodem.

The page is upstream's ``to_html`` output with four changes:

- vis-network is embedded instead of loaded from unpkg, so the page needs no
  network. The embedded file must match the integrity hash that upstream pins.
- The page starts in Kodem's theme until the viewer picks another one.
- Each node with a source file has an "Open in Kodem" link of the form
  ``kodem-codemap://open?path=<repo-relative path>&line=<n>``.
- The title is "Code Map".
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from graphify.exporters.html import write_text_atomic

_ASSETS = Path(__file__).with_name("kodem_assets")
_VIS_NETWORK = _ASSETS / "vis-network.min.js"

_CDN_SCRIPT = re.compile(
    r'<script src="https://unpkg\.com/vis-network@9\.1\.6/standalone/umd/vis-network\.min\.js"\s+'
    r'integrity="(?P<integrity>sha384-[A-Za-z0-9+/=]+)"\s+crossorigin="anonymous"></script>'
)

_SOURCE_FIELD = "html += `<div class=\"field\">Source: ${esc(n.source_file || '-')}</div>`;"
_SOURCE_FIELD_WITH_LINK = (
    "html += `<div class=\"field\">Source: ${esc(n.source_file || '-')}</div>`;\n"
    "    if (n.source_file) {\n"
    "      const line = String(n.source_location || '').replace(/^L/, '').split(/[^0-9]/)[0] || '1';\n"
    "      const href = 'kodem-codemap://open?path=' + encodeURIComponent(n.source_file)"
    " + '&line=' + encodeURIComponent(line);\n"
    "      html += `<div class=\"field\"><a class=\"kodem-open\" href=\"${esc(href)}\">Open in Kodem</a></div>`;\n"
    "    }"
)

_TITLE = re.compile(r"<title>[^<]*</title>")

_LINK_STYLE = "<style>.kodem-open { color: #4E79A7; text-decoration: none; } .kodem-open:hover { text-decoration: underline; }</style>"


class ViewerError(RuntimeError):
    """Raised when upstream's page no longer has a part that the viewer changes."""


def _integrity(data: bytes) -> str:
    """Returns the subresource-integrity value (sha384) of ``data``."""
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def _theme_script(theme: str) -> str:
    """Returns the script that applies Kodem's theme unless the viewer chose one in the page."""
    return (
        "<script id=\"kodem-theme-init\">(function () {\n"
        "  var pref = null;\n"
        "  try { pref = localStorage.getItem('graphify-theme'); } catch (e) {}\n"
        f"  if (!pref) {{ document.documentElement.setAttribute('data-theme', {json.dumps(theme)}); }}\n"
        "})();</script>"
    )


def adapt_page(html: str, *, theme: str) -> str:
    """Returns upstream's viewer page with the Kodem changes applied.

    Raises ViewerError when an anchor is missing, so that an upstream change to
    the page fails loudly instead of silently loading the CDN again.
    """
    match = _CDN_SCRIPT.search(html)
    if match is None:
        raise ViewerError("the vis-network script tag of graph.html changed upstream")
    script = _VIS_NETWORK.read_bytes()
    if _integrity(script) != match.group("integrity"):
        raise ViewerError("the embedded vis-network does not match upstream's pinned integrity hash")
    # The bundle has no "</script" sequence, so it can be embedded as it is.
    if b"</script" in script.lower():
        raise ViewerError("the embedded vis-network contains a closing script tag")
    inline = "<script>" + script.decode("utf-8") + "</script>"
    html = html[: match.start()] + inline + html[match.end():]

    if _SOURCE_FIELD not in html:
        raise ViewerError("the node panel of graph.html changed upstream")
    html = html.replace(_SOURCE_FIELD, _SOURCE_FIELD_WITH_LINK, 1)

    if "</head>" not in html:
        raise ViewerError("graph.html has no </head>")
    html = html.replace("</head>", _theme_script(theme) + "\n" + _LINK_STYLE + "\n</head>", 1)
    html, titles = _TITLE.subn("<title>Code Map</title>", html, count=1)
    if titles != 1:
        raise ViewerError("graph.html has no title")
    if "https://unpkg.com" in html:
        raise ViewerError("graph.html still loads a script from unpkg")
    return html


def _communities(graph_data: dict) -> tuple[dict[int, list[str]], dict[int, str]]:
    """Returns the communities and their names stored in a graph.json."""
    communities: dict[int, list[str]] = {}
    labels: dict[int, str] = {}
    for node in graph_data.get("nodes", []):
        community = node.get("community")
        if community is None:
            continue
        communities.setdefault(int(community), []).append(str(node.get("id")))
        name = node.get("community_name")
        if isinstance(name, str) and name:
            labels.setdefault(int(community), name)
    return communities, labels


def write_viewer(graph_path: Path, *, theme: str = "dark", node_limit: int = 5000) -> Path:
    """Writes ``graph.html`` next to ``graph_path`` and returns its path.

    Above ``node_limit`` nodes the page shows one node per community, as upstream does.
    """
    from graphify.export import to_html
    from graphify.paths import load_node_link_graph

    graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
    communities, labels = _communities(graph_data)
    graph = load_node_link_graph(graph_data)
    target = graph_path.with_name("graph.html")
    staging = graph_path.with_name(".kodem-graph.html")
    try:
        written = to_html(
            graph,
            communities,
            str(staging),
            community_labels=labels or None,
            node_limit=max(1, node_limit),
        )
        if not written or not staging.exists():
            raise ViewerError("graphify wrote no viewer page for this map")
        page = adapt_page(staging.read_text(encoding="utf-8"), theme=theme)
    finally:
        staging.unlink(missing_ok=True)
    write_text_atomic(str(target), page)
    return target
