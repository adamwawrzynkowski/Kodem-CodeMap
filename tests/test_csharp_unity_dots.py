# Modified by Kodem.
"""Unity and DOTS engine entry points and job scheduling.

Unity calls `Awake`/`Start`/`Update`... on a MonoBehaviour and DOTS calls
`OnCreate`/`OnUpdate` on a system and `Execute` on a job, so nothing in the
project names them: they had no incoming edge and read as dead code. They now
carry `entry_point: true`, and `new XJob { .. }.Schedule()` (or the same call
on a typed local) links the scheduling method to `XJob.Execute` with a
`schedules` edge.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract

_JOBS = (
    "using Unity.Entities;\n"
    "namespace Game {\n"
    "    public partial struct MoveJob : IJobEntity { public float Dt; void Execute(ref LocalTransform t) {} }\n"
    "    public struct CopyJob : IJob { public void Execute() {} }\n"
    "    public struct NotAJob { public void Execute() {} public void Run() {} }\n"
    "}\n"
)
_SYSTEM_A = (
    "using Unity.Entities;\n"
    "namespace Game {\n"
    "    public partial struct MoveSystem : ISystem {\n"
    "        public void OnCreate(ref SystemState state) {}\n"
    "    }\n"
    "}\n"
)
_SYSTEM_B = (
    "using Unity.Entities;\n"
    "namespace Game {\n"
    "    public partial struct MoveSystem {\n"
    "        public void OnUpdate(ref SystemState state) {\n"
    "            new MoveJob { Dt = 1f }.ScheduleParallel();\n"
    "            var job = new CopyJob();\n"
    "            state.Dependency = job.Schedule(state.Dependency);\n"
    "            var other = new NotAJob();\n"
    "            other.Run();\n"
    "        }\n"
    "    }\n"
    "}\n"
)
_BEHAVIOURS = (
    "using UnityEngine;\n"
    "namespace Game {\n"
    "    public class Character : MonoBehaviour { void Awake() {} }\n"
    "    public class Enemy : Character { void Update() {} void Helper() {} }\n"
    "    public class Plain { void Update() {} }\n"
    "}\n"
)


def _graph(tmp_path: Path):
    files = {"Jobs.cs": _JOBS, "MoveSystemA.cs": _SYSTEM_A, "MoveSystemB.cs": _SYSTEM_B,
             "Behaviours.cs": _BEHAVIOURS}
    paths = []
    for name, body in files.items():
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    r = extract(paths, root=tmp_path, cache_root=tmp_path / "graphify-out")
    return {n["id"]: n for n in r["nodes"]}, r["edges"]


def _owned(nodes, edges) -> dict[tuple[str, str], dict]:
    owner = {e["target"]: e["source"] for e in edges if e["relation"] == "method"}
    return {
        (nodes[owner[nid]]["label"], n["label"]): n
        for nid, n in nodes.items() if nid in owner
    }


def test_engine_entry_points_are_tagged(tmp_path: Path):
    nodes, edges = _graph(tmp_path)
    methods = _owned(nodes, edges)
    for key in (("Character", ".Awake()"), ("Enemy", ".Update()"),
                ("MoveSystem", ".OnCreate()"), ("MoveSystem", ".OnUpdate()"),
                ("MoveJob", ".Execute()"), ("CopyJob", ".Execute()")):
        assert methods[key].get("entry_point") is True, key
    for key in (("Enemy", ".Helper()"), ("Plain", ".Update()"), ("NotAJob", ".Execute()")):
        assert not methods[key].get("entry_point"), key


def test_partial_isystem_halves_merge(tmp_path: Path):
    nodes, edges = _graph(tmp_path)
    systems = [n for n in nodes.values() if n["label"] == "MoveSystem"]
    assert len(systems) == 1, systems
    methods = _owned(nodes, edges)
    assert ("MoveSystem", ".OnCreate()") in methods and ("MoveSystem", ".OnUpdate()") in methods


def test_schedule_calls_link_to_the_job_execute(tmp_path: Path):
    nodes, edges = _graph(tmp_path)
    owner = {e["target"]: e["source"] for e in edges if e["relation"] == "method"}
    schedules = {
        (nodes[e["source"]]["label"], nodes[owner[e["target"]]]["label"], nodes[e["target"]]["label"])
        for e in edges if e["relation"] == "schedules"
    }
    assert schedules == {
        (".OnUpdate()", "MoveJob", ".Execute()"),
        (".OnUpdate()", "CopyJob", ".Execute()"),
    }, schedules

