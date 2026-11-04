"""web/js/viz/verdict.js: per-frame verdicts from the checker's intervals (run under node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _js(body: str):
    src = "import * as V from '" + (ROOT / "web/js/viz/verdict.js").as_uri() + "';\n" + body
    p = subprocess.run([NODE, "--input-type=module", "-e", src], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_intervals_and_verdicts():
    out = _js("""
const report = { violations: [{constraint_id: 'c_up', object_id: 'cup_0', t_start: 2, t_end: 3, peak: 40}],
  advisory_violations: [{constraint_id: 'c_adv', object_id: 'cup_0', t_start: 1, t_end: 2.5, peak: 20},
                        {constraint_id: 'c_adv', object_id: 'block_0', t_start: 0, t_end: 9, peak: 1}],
  forbidden_contacts: [{object: 'cup_0', other: 'wall_0', t_start: 5, t_end: 5.2, max_depth: 0.001}] };
const ints = V.intervalsFor(report, 'cup_0');
console.log(JSON.stringify({ n: ints.length, kinds: ints.map(i => i.kind),
  at: [0.5, 1.5, 2.2, 2.8, 3.5, 5.1].map(t => V.verdictAt(ints, t)),
  all: V.intervalsFor(report, null).length,
  carry: ['lift:cup_0', 'transport:cup_0', 'place:cup_0', 'move:cup_0', 'transport:block_0', 'approach:cup_0'].map(p => V.carrying(p, 'cup_0')),
  ev: V.events({ t: [0, 1, 2, 3, 4], phase: ['move:cup_0', 'grasp:cup_0', 'grasp:cup_0', 'release:cup_0', 'move:block_0'] }) }));
""")
    assert out["n"] == 3 and out["kinds"] == ["violated", "advisory", "contact"]
    assert out["at"] == ["ok", "advisory", "violated", "violated", "ok", "violated"]
    assert out["all"] == 4
    assert out["carry"] == [True, True, True, False, False, False]
    assert [(e["type"], e["object_id"], e["t"], e["frame"]) for e in out["ev"]] == [("grasp", "cup_0", 1, 1), ("release", "cup_0", 3, 3)]
