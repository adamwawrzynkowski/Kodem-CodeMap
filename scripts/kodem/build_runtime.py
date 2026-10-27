# Modified by Kodem.
"""Builds the Code Map runtime archive for the machine it runs on (FORK.md, "Runtime archive").

Usage: python scripts/kodem/build_runtime.py --out DIST [--target TARGET]

The archive holds a standalone CPython from python-build-standalone, the fork
and its locked dependencies (uv.lock, core plus the mcp extra), and the license
files of everything inside. Each target is built on a machine of that target:
the dependencies include native wheels, so the script does not cross-build.
Needs Python 3.12 or later, uv on PATH, and network access to GitHub and PyPI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# python-build-standalone release 20261009, CPython 3.12.15, "install_only" archives.
PYTHON_RELEASE = "20261009"
PYTHON_VERSION = "3.12.15"
PYTHON_ARCHIVES = {
    "aarch64-apple-darwin": "83dcb04777c798bd95f3687acf9ae84b09860272ee6bdfb05be8bcf9447f75b8",
    "aarch64-pc-windows-msvc": "4da354e7366d4437fceea714eab1bbdca1eb17bba4491a3d20b9c6af94b44f68",
    "x86_64-pc-windows-msvc": "194c8f0d9e0de6f1c4576e5dfa67819a48c764be87608b74b8ad43387a57b93b",
    "x86_64-unknown-linux-gnu": "5864001bf969db329fadc2b654c4878c6027d170c6bd77e28f88666260bcaf63",
}
PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "{release}/cpython-{version}+{release}-{target}-install_only.tar.gz"
)

# Standard-library parts that the runtime never uses.
UNUSED_STDLIB = ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data")

# Files of the fork itself that go into licenses/.
FORK_NOTICES = ("LICENSE", "LICENSE-MIT", "NOTICE", "FORK.md")

# Prints name, version, license and license files of every installed distribution.
_LICENSE_PROBE = r"""
import json, sys
from importlib import metadata
rows = []
for dist in metadata.distributions():
    meta = dist.metadata
    files = [str(dist.locate_file(f)) for f in (dist.files or [])
             if any(part in f.name.upper() for part in ("LICEN", "COPYING", "NOTICE", "AUTHORS"))]
    classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    rows.append({
        "name": meta["Name"],
        "version": dist.version,
        "license": meta.get("License-Expression") or meta.get("License") or "",
        "classifiers": classifiers,
        "files": files,
    })
json.dump(sorted(rows, key=lambda r: r["name"].lower()), sys.stdout)
"""


def host_target() -> str:
    """Returns the target triple of this machine."""
    machine = platform.machine().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "amd64": "x86_64", "x86_64": "x86_64"}.get(machine)
    if arch is None:
        raise SystemExit(f"unsupported machine: {machine}")
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    if sys.platform == "win32":
        return f"{arch}-pc-windows-msvc"
    if sys.platform.startswith("linux"):
        return f"{arch}-unknown-linux-gnu"
    raise SystemExit(f"unsupported platform: {sys.platform}")


def runtime_version() -> str:
    """Returns VERSION from graphify/kodem_main.py without importing the package."""
    text = (ROOT / "graphify" / "kodem_main.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise SystemExit("VERSION not found in graphify/kodem_main.py")
    return match.group(1)


def interpreter(stage: Path, target: str) -> Path:
    """Returns the interpreter path inside the staged runtime."""
    if "windows" in target:
        return stage / "python" / "python.exe"
    return stage / "python" / "bin" / "python3"


def stdlib(stage: Path, target: str) -> Path:
    """Returns the standard-library folder inside the staged runtime."""
    if "windows" in target:
        return stage / "python" / "Lib"
    return stage / "python" / "lib" / f"python{PYTHON_VERSION.rsplit('.', 1)[0]}"


def sha256(path: Path) -> str:
    """Returns the SHA-256 of a file in hex."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_python(target: str, cache: Path) -> Path:
    """Downloads the pinned CPython archive for ``target`` and checks its SHA-256."""
    url = PYTHON_URL.format(release=PYTHON_RELEASE, version=PYTHON_VERSION, target=target)
    archive = cache / url.rsplit("/", 1)[1]
    if not archive.exists() or sha256(archive) != PYTHON_ARCHIVES[target]:
        print(f"downloading {url}", flush=True)
        partial = archive.with_suffix(".partial")
        with urllib.request.urlopen(url, timeout=300) as response, partial.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        partial.replace(archive)
    if sha256(archive) != PYTHON_ARCHIVES[target]:
        raise SystemExit(f"checksum mismatch for {archive.name}")
    return archive


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """Runs a command, echoing its first line, and fails the build when it fails."""
    print("+ " + " ".join(part.splitlines()[0] if part.strip() else part for part in command), flush=True)
    return subprocess.run(command, check=True, text=True, **kwargs)


def install_packages(python: Path, work: Path) -> None:
    """Installs the locked dependencies and then the fork into the staged interpreter."""
    requirements = work / "requirements.txt"
    run([
        "uv", "export", "--frozen", "--no-dev", "--extra", "mcp", "--no-emit-project",
        "--format", "requirements-txt", "--output-file", str(requirements),
    ], cwd=ROOT, stdout=subprocess.DEVNULL)
    run(["uv", "pip", "install", "--python", str(python), "--require-hashes", "--no-deps", "-r", str(requirements)])
    run(["uv", "pip", "install", "--python", str(python), "--no-deps", "--reinstall-package", "graphifyy", str(ROOT)])


def trim(stage: Path, target: str) -> None:
    """Removes the standard-library parts and installer tools that the runtime never uses."""
    library = stdlib(stage, target)
    for name in UNUSED_STDLIB:
        shutil.rmtree(library / name, ignore_errors=True)
    site = library / "site-packages"
    for entry in list(site.iterdir()) if site.is_dir() else []:
        if entry.name == "pip" or entry.name.startswith("pip-"):
            shutil.rmtree(entry, ignore_errors=True)
    for cache in stage.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def collect_licenses(stage: Path, python: Path, target: str) -> list[dict]:
    """Copies every license file into licenses/ and returns the inventory.

    Fails the build when a distribution states no license at all.
    """
    licenses = stage / "licenses"
    licenses.mkdir()
    for name in FORK_NOTICES:
        shutil.copy2(ROOT / name, licenses / f"kodem-codemap-{name}")
    for path in (ROOT / "graphify" / "kodem_assets").glob("vis-network-LICENSE-*"):
        shutil.copy2(path, licenses / path.name)
    python_license = stdlib(stage, target) / "LICENSE.txt"
    if not python_license.exists():
        python_license = stage / "python" / "LICENSE.txt"
    shutil.copy2(python_license, licenses / "python-LICENSE.txt")
    probe = run([str(python), "-I", "-c", _LICENSE_PROBE], capture_output=True)
    inventory = json.loads(probe.stdout)
    missing = []
    for row in inventory:
        folder = licenses / f"{row['name']}-{row['version']}"
        copied = []
        for file in row.pop("files"):
            source = Path(file)
            if source.is_file():
                folder.mkdir(exist_ok=True)
                shutil.copy2(source, folder / source.name)
                copied.append(source.name)
        row["license_files"] = copied
        if not copied and not row["license"] and not row["classifiers"]:
            missing.append(row["name"])
    if missing:
        raise SystemExit("distributions without license information: " + ", ".join(missing))
    inventory.append({
        "name": "CPython (python-build-standalone)",
        "version": f"{PYTHON_VERSION}+{PYTHON_RELEASE}",
        "license": "PSF-2.0 and the licenses of the bundled libraries",
        "classifiers": [],
        "license_files": ["python-LICENSE.txt"],
    })
    inventory.append({
        "name": "vis-network",
        "version": "9.1.6",
        "license": "Apache-2.0 OR MIT",
        "classifiers": [],
        "license_files": ["vis-network-LICENSE-APACHE-2.0", "vis-network-LICENSE-MIT"],
    })
    (licenses / "THIRD_PARTY.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    return inventory


def smoke_test(python: Path, version: str, work: Path) -> None:
    """Runs ``version`` and maps a two-file repository with the staged runtime."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "VIRTUAL_ENV"))}
    shown = run([str(python), "-I", "-m", "graphify.kodem_main", "version"], capture_output=True, env=environment)
    result = json.loads(shown.stdout.strip().splitlines()[-1].split(" ", 1)[1])
    if result["version"] != version:
        raise SystemExit(f"runtime reports version {result['version']}, expected {version}")
    repo = work / "smoke-repo"
    repo.mkdir()
    (repo / "main.rs").write_text("fn main() { helper(); }\nfn helper() {}\n", encoding="utf-8")
    (repo / "tool.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    built = run(
        [str(python), "-I", "-m", "graphify.kodem_main", "build", str(repo), "--out", str(work / "smoke-map")],
        capture_output=True,
        env=environment,
    )
    summary = json.loads(built.stdout.strip().splitlines()[-1].split(" ", 1)[1])
    if summary["nodes"] < 3:
        raise SystemExit(f"smoke map is too small: {summary}")


def write_archive(stage: Path, out: Path, version: str, target: str) -> Path:
    """Writes the .tar.gz and its .sha256 file and returns the archive path."""
    name = f"kodem-codemap-{version}-{target}"
    archive = out / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=9) as bundle:
        for entry in sorted(stage.iterdir()):
            bundle.add(entry, arcname=entry.name)
    (out / f"{name}.tar.gz.sha256").write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
    return archive


def main() -> None:
    """Builds, checks and archives the runtime for one target."""
    if sys.version_info < (3, 12):
        raise SystemExit("run this script with Python 3.12 or later")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--target", default=None)
    parser.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "kodem-codemap-python")
    arguments = parser.parse_args()
    # The staged interpreter must run on this machine (the smoke test runs it),
    # so a target of another machine fails there.
    target = arguments.target or host_target()
    if target not in PYTHON_ARCHIVES:
        raise SystemExit(f"unsupported target: {target}")
    version = runtime_version()
    arguments.out.mkdir(parents=True, exist_ok=True)
    arguments.cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kodem-codemap-") as scratch:
        work = Path(scratch)
        stage = work / "stage"
        stage.mkdir()
        with tarfile.open(download_python(target, arguments.cache)) as python_archive:
            python_archive.extractall(stage, filter="data")
        python = interpreter(stage, target)
        install_packages(python, work)
        trim(stage, target)
        inventory = collect_licenses(stage, python, target)
        package = {
            "name": "kodem-codemap",
            "version": version,
            "target": target,
            "python": interpreter(stage, target).relative_to(stage).as_posix(),
            "schema": 1,
        }
        (stage / "codemap-package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
        smoke_test(python, version, work)
        for leftover in stage.rglob("__pycache__"):
            shutil.rmtree(leftover, ignore_errors=True)
        archive = write_archive(stage, arguments.out, version, target)
    size = archive.stat().st_size / (1 << 20)
    print(f"wrote {archive} ({size:.1f} MiB, {len(inventory)} components)")


if __name__ == "__main__":
    main()
