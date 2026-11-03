"""Pure web modules (story, identity, provenance), run in Node against synthetic snapshots.

Every snapshot here is synthetic test data built for these checks, not a recording.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "web" / "js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_js(tmp_path: Path, body: str, data: dict | None = None):
    """Run an ES module that can import the web modules; it must print one JSON value."""
    (tmp_path / "data.json").write_text(json.dumps(data or {}))
    src = ("import fs from 'fs';\n"
           f"const DATA = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));\n"
           + body.replace("@JS@", JS.as_uri()))
    f = tmp_path / "case.mjs"
    f.write_text(src)
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _rollout(rid, version, scene, cup_route, tilt, kept, block_route="through_slot:wall_0:0:pitched-90", tags=("world",)):
    return {"id": rid, "spec_version": version, "scene_id": scene, "tags": list(tags), "has_frames": True,
            "scene": {"objects": [{"id": "cup_0", "kind": "cup"}, {"id": "block_0", "kind": "block"}]},
            "plan": {"transport": [{"object_id": "cup_0", "route": cup_route}, {"object_id": "block_0", "route": block_route}]},
            "report": {"status": "success", "violations": [], "route_labels": [],
                       "outcomes": [{"object_id": "cup_0", "beads_total": 8, "beads_in_container": kept,
                                     "max_transport_tilt_deg": tilt}, {"object_id": "block_0"}]}}


def synthetic_snapshot():
    """A synthetic session: v1 run spills in the shortcut layout, the v2 correction reroutes the cup."""
    upright_t = {"id": "c_upright_transport_cup", "type": "upright", "phase": "transport", "status": "confirmed",
                 "params": {"max_tilt_deg": 15}, "applies_to": {"kind": "cup"}}
    return {
        "session_id": "synthetic", "demo": {"demo_id": "demo-a", "duration": 10},
        "specs": [
            {"version": 1, "parent_version": None, "constraints": [], "history": []},
            {"version": 2, "parent_version": 1, "constraints": [upright_t],
             "history": [{"version": 2, "kind": "correction", "text": "keep the cup upright while carrying it", "model": None,
                          "diff": [{"op": "set_status", "target_id": "c_upright_transport_cup"}]}]},
        ],
        "rollouts": [
            _rollout("r001", 1, "ref_demo", "around:wall_0:+y", 1.0, 8, block_route="around:wall_0:+y", tags=("run",)),
            _rollout("r002", 1, "ref_shortcut", "through_slot:wall_0:0:pitched+90", 89.7, 4),
            _rollout("r003", 2, "ref_shortcut", "around:wall_0:+y", 0.2, 8, tags=("corrected",)),
        ],
        "rechecks": [
            {"rollout_id": "r001", "spec_version": 2, "report": {"status": "success", "violations": []}},
            {"rollout_id": "r002", "spec_version": 2, "report": {"status": "constraint_violated", "violations": [
                {"constraint_id": "c_upright_transport_cup", "peak": 89.7, "t_start": 6.0, "t_end": 10.1}]}},
        ],
    }


STORY = "import { correctionStory } from '@JS@/story.js';\nconsole.log(JSON.stringify(correctionStory(DATA)));\n"


def test_story_reports_failure_change_and_scope(tmp_path):
    st = run_js(tmp_path, STORY, synthetic_snapshot())
    assert st["version"] == 2 and st["beforeVersion"] == 1
    assert st["failed"]["id"] == "r002" and st["failed"]["beads"] == {"kept": 4, "total": 8} and st["failed"]["overTol"]
    assert st["recheck"]["status"] == "constraint_violated" and st["recheck"]["constraint_id"] == "c_upright_transport_cup"
    assert st["fixed"]["id"] == "r003" and st["fixed"]["beads"] == {"kept": 8, "total": 8}
    assert st["fixed"]["cup_route"] == "around:wall_0:+y"
    assert [c["id"] for c in st["scope"]] == ["c_upright_transport_cup"]
    # The block is outside the correction's scope and keeps its slot route.
    assert st["unchanged"] == [{"object_id": "block_0", "kind": "block", "route": "through_slot:wall_0:0:pitched-90"}]


def test_story_needs_an_independent_recheck(tmp_path):
    snap = synthetic_snapshot()
    snap["rechecks"] = [x for x in snap["rechecks"] if x["rollout_id"] != "r002"]
    assert run_js(tmp_path, STORY, snap) is None
    snap = synthetic_snapshot()
    snap["specs"][1]["history"] = []
    assert run_js(tmp_path, STORY, snap) is None


def test_story_ignores_evaluation_rollouts(tmp_path):
    snap = synthetic_snapshot()
    snap["rollouts"][1]["tags"] = ["eval"]
    assert run_js(tmp_path, STORY, snap) is None


IDENT = r"""
import { generationId, rolloutCacheKey, isStale, latestOnly, DROPPED, isImmutableId } from '@JS@/identity.js';
const a = DATA.a, b = DATA.b, g = DATA.g;
const sleep = (ms, v) => new Promise((r) => setTimeout(() => r(v), ms));
const slowFirst = latestOnly((ms, v) => sleep(ms, v));
const p1 = slowFirst(60, 'old'), p2 = slowFirst(5, 'new');
const [r1, r2] = await Promise.all([p1, p2]);
console.log(JSON.stringify({
  genA: generationId(a), genB: generationId(b), genG: generationId(g),
  keyA: rolloutCacheKey(a, 'r001'), keyB: rolloutCacheKey(b, 'r001'),
  demoA: rolloutCacheKey(a, 'demo'), demoB: rolloutCacheKey(b, 'demo'),
  keyG: rolloutCacheKey(g, g.rollouts[0].id), demoG: rolloutCacheKey(g, 'demo'),
  immutable: isImmutableId(g.rollouts[0].id), bareImmutable: isImmutableId('r001'),
  staleOther: isStale('g18f3aa01.x', { generation_id: 'g18f2bb02.x' }),
  staleSame: isStale('g18f3aa01.x', { generation_id: 'g18f3aa01.x' }),
  staleNoGen: isStale('g18f3aa01.x', {}),
  staleLegacy: isStale('legacy:s:d::', { generation_id: 'g1' }),
  r1Dropped: r1 === DROPPED, r2,
}));
"""


def test_cache_keys_never_collide_across_demonstrations(tmp_path):
    a = {"session_id": "s1", "demo": {"demo_id": "demo-a", "duration": 10},
         "rollouts": [{"id": "r001", "spec_version": 1}]}
    b = {"session_id": "s1", "demo": {"demo_id": "demo-b", "duration": 12},
         "rollouts": [{"id": "r001", "spec_version": 1}]}
    gid = "g18f3aa01b2c3"
    g = {"generation": {"id": gid, "demo": {"playback_id": f"demo.{gid}.1a2b3c4d"}},
         "rollouts": [{"id": f"{gid}.v1.s4242.r001.0f0e0d0c", "content_sha256": "0f0e0d0c" + "0" * 56, "spec_version": 1}]}
    out = run_js(tmp_path, IDENT, {"a": a, "b": b, "g": g})
    assert out["genA"] != out["genB"]
    assert out["keyA"] != out["keyB"], "run r001 of demo A must not be served for demo B"
    assert out["demoA"] != out["demoB"]
    assert out["genG"] == gid and out["keyG"].startswith(g["rollouts"][0]["id"])
    assert out["demoG"] == f"demo|demo.{gid}.1a2b3c4d"
    assert out["immutable"] and not out["bareImmutable"]
    assert out["staleOther"] and not out["staleSame"] and not out["staleNoGen"] and not out["staleLegacy"]
    assert out["r1Dropped"] and out["r2"] == "new"


PROV = r"""
import { demoSource, videoMediaKind, provenanceText } from '@JS@/provenance.js';
console.log(JSON.stringify(DATA.cases.map((s) => ({ kind: videoMediaKind(s), ...demoSource(s) }))
  .concat([{ p: provenanceText('offline_interpreter') }, { p: provenanceText('recorded') }])));
"""


def test_demonstration_wording_matches_what_the_video_is(tmp_path):
    cases = [
        {"demo": {"source": "video_grounded"}, "video_demo": {"bundled": True}},
        {"demo": {"source": "video_grounded"}, "video_demo": {"bundled": False}},
        {"demo": {"source": "video_grounded"}, "video_demo": {"media": {"kind": "camera"}}},
        {"demo": {"source": "video_grounded", "provenance": {"label": "Phone video of a person", "description": "phone footage of a person, calibrated"}}},
        {"demo": {"source": "teleop_scripted"}},
    ]
    out = run_js(tmp_path, PROV, {"cases": cases})
    rendered, upload, camera, supplied, teleop = out[:5]
    assert rendered["kind"] == "rendered_simulation" and "not footage of a person" in rendered["long"]
    assert "rendered from the simulator" in rendered["long"]
    assert "person" not in upload["long"] and upload["kind"] == "upload"
    assert camera["kind"] == "camera" and "person" not in camera["long"]
    assert supplied["long"] == "phone footage of a person, calibrated"
    assert "not a video of a person" in teleop["long"]
    for c in out[:5]:
        # The old wording joined "human video" with "not a video of a person".
        assert "human video" not in c["long"]
    assert out[5]["p"].startswith("offline interpreter") and out[6]["p"].startswith("recorded")
