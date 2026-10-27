# Modified by Kodem.
"""Unity's generated folders are pruned beside a Unity project's layout.

`Library/` (with every package's sources under PackageCache), `Temp/`, `Logs/`
and `obj/` were scanned when a Unity project had no `.gitignore` yet. They are
now noise when their parent holds both `Assets/` and `ProjectSettings/`.
"""
from __future__ import annotations

from pathlib import Path

from graphify.detect import _is_noise_dir


def test_unity_generated_folders_are_noise_only_in_a_unity_project(tmp_path: Path):
    unity = tmp_path / "game"
    for d in ("Assets", "ProjectSettings", "Library", "Temp", "Logs", "obj"):
        (unity / d).mkdir(parents=True)
    for d in ("Library", "Temp", "Logs", "obj"):
        assert _is_noise_dir(d, unity), d
    assert not _is_noise_dir("Assets", unity)
    plain = tmp_path / "lib"
    (plain / "Library").mkdir(parents=True)
    (plain / "Temp").mkdir()
    assert not _is_noise_dir("Library", plain)
    assert not _is_noise_dir("Temp", plain)


def test_detect_skips_unity_library_without_a_gitignore(tmp_path: Path):
    from graphify.detect import detect

    (tmp_path / "Assets" / "Scripts").mkdir(parents=True)
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "Library" / "PackageCache" / "pkg").mkdir(parents=True)
    (tmp_path / "Assets" / "Scripts" / "Player.cs").write_text("class Player {}\n", encoding="utf-8")
    (tmp_path / "Library" / "PackageCache" / "pkg" / "Vendor.cs").write_text("class Vendor {}\n", encoding="utf-8")
    found = detect(tmp_path, cache_root=tmp_path / "graphify-out")
    code = [Path(p).name for p in found["files"]["code"]]
    assert "Player.cs" in code
    assert "Vendor.cs" not in code
