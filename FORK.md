# Kodem Code Map fork

This repository is Kodem's fork of [graphify](https://github.com/Graphify-Labs/graphify) by Graphify Labs. Kodem uses it to build the Code Map: a local knowledge graph of a project's code that coding agents query through MCP. The fork is distributed as `kodem-codemap`, and Kodem downloads it on first use. "graphify" is a mark of Graphify Labs; this fork is not affiliated with or endorsed by Graphify Labs.

- Base: upstream `v8` at `dac0634` (graphify 0.9.96). The branch `v8` mirrors upstream; the fork's work is on `main`.
- License: Apache-2.0 (`LICENSE`), with older contributions under MIT (`LICENSE-MIT`). See `NOTICE`. Each file that the fork changes carries a `Modified by Kodem` line.
- The plan that this fork implements is `docs/design/code-map-plan.md` in the Kodem repository.

## Rules

- The fork never calls an LLM and never makes a network request.
- The fork writes only into the output folder that Kodem names. It never writes into the scanned repository or into the user's home folder.
- One commit holds one change, so that a change can be dropped when upstream fixes the same issue. Each change has an entry below.

## Kodem contract

Kodem depends on this interface. Change it only together with Kodem.

### Runtime archive

One archive per target: `kodem-codemap-<version>-<target>.tar.gz`, with `<target>` one of `aarch64-apple-darwin`, `x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc` and `x86_64-unknown-linux-gnu`. Each archive has a `.sha256` file next to it in the GitHub release `v<version>`.

The archive root holds:

- `codemap-package.json`: `{"name": "kodem-codemap", "version": "<version>", "target": "<target>", "python": "<relative path of the interpreter>", "schema": 1}`. The interpreter is `python/bin/python3` on macOS and Linux and `python/python.exe` on Windows.
- `python/`: a standalone CPython 3.12 with the fork and its locked dependencies installed.
- `licenses/`: the notices of everything in the archive.

### Commands

Kodem runs `<interpreter> -P -m graphify.kodem_main <command> ...`. `-P` keeps the working folder, which can be a repository, out of the module path. Every command:

- writes progress as plain lines on stderr;
- prints its result as the last line of stdout, in the form `KODEM-CODEMAP-RESULT <json>`;
- exits with 0 on success, 2 for bad arguments, 3 when another process holds the map's lock, and 1 for other errors; the last stderr line is then the error.

| Command | Does | Result JSON |
| --- | --- | --- |
| `build <repo> --out <dir>` | Maps `<repo>` from the beginning into `<dir>` | `{"nodes": n, "edges": n, "files": n, "commit": "<sha>" or null}` |
| `update <repo> --out <dir>` | Updates the map in `<dir>` for the changes in `<repo>`. Without a map it acts as `build` | same as `build` |
| `html --out <dir> [--theme dark\|light] [--node-limit n]` | Writes `<dir>/graph.html` from `<dir>/graph.json` | `{"html": "<path>"}` |
| `serve <dir>/graph.json` | Runs the MCP server on stdio | none |
| `version` | Prints the versions | `{"version": "<version>", "graphify": "0.9.96", "schema": 1}` |

`<repo>` and `<dir>` are absolute paths. `<dir>` holds `graph.json` directly. A map folder can be copied to another folder and updated against another checkout of the same repository, such as a git worktree.

### MCP server

Server name `kodem-codemap`. Tools: `query_graph`, `get_node`, `get_neighbors`, `shortest_path`. The server reloads `graph.json` when the file changes.

## Changes

| Change | Upstream issue | Files |
| --- | --- | --- |
| CODEOWNERS names the fork owner; upstream funding and team files removed | — | `.github/` |
| Offline viewer: vis-network 9.1.6 embedded (integrity-checked), Kodem theme, "Open in Kodem" links, node source line in the page data | — | `graphify/kodem_viewer.py`, `graphify/kodem_assets/`, `graphify/exporters/html.py` |
| Kodem entry point `graphify.kodem_main`: build, update, html, serve, version; no LLM, no network, writes only to `--out`; build and update share the update pipeline | #3300, #4361, #4624 | `graphify/kodem_main.py`, `pyproject.toml`, `tests/test_kodem_main.py` |
| `GRAPHIFY_NO_VIZ=1`: updates never write `graph.html` or its stale marker, and the no-change gate ignores the page | — | `graphify/watch.py` |
| Kodem MCP mode (`GRAPHIFY_KODEM_MCP=1`): server `kodem-codemap` with instructions; 4 tools with short schemas and no `project_path` (about 400 tokens instead of 1,600); answers grouped by file with empty fields left out; default depth 2 and budget 1,200; trailing edges cut to the budget. On the Kodem map the average answer fell from about 2,160 to about 1,340 tokens | #4469, #2784 | `graphify/serve.py`, `graphify/kodem_main.py`, `tests/test_kodem_mcp.py` |
| CI for the fork: contract tests on macOS, Windows and Linux, the upstream suite on Linux with the base's known failures deselected; upstream publish, release-graph, welcome and PR-management workflows removed; Dependabot for pinned actions | — | `.github/`, `tests/kodem_known_failures.txt` |
| Runtime archive build (`scripts/kodem/build_runtime.py`) and the draft-release workflow | — | `scripts/kodem/`, `.github/workflows/runtime.yml` |

## Known gaps

These upstream issues still apply to the runtime. Kodem works around them where noted.

| Issue | Effect | Kodem |
| --- | --- | --- |
| #4800 | Any change re-extracts all files (the AST cache keeps it at about 10 s on the Kodem repository; a no-change update takes under 1 s) | Updates run in the background after a turn |
| #3646 | On Windows, `update` can hang while it saves the manifest | Kodem stops map jobs that run too long |
| #3868 | A crafted corpus can create millions of import edges | — |
| #3810 | `query_graph` can seed on one common word of the question | — |
| #4738, #4281, #3726, #3407 | Node-ID collisions drop nodes; graphify prints a note | — |
