"""The viewer's hover readouts use web/js/viz/geom.js; it must agree with the checker's
geometry (mirror/evaluate/geom.py) on the same poses."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from mirror.contracts import ObjectSpec, ObstacleSpec
from mirror.evaluate import geom

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _run_js(cases):
    src = (
        "import * as G from '" + (ROOT / "web/js/viz/geom.js").as_uri() + "';\n"
        "const cases = JSON.parse(process.argv[process.argv.length - 1]);\n"
        "const out = cases.map((c) => {\n"
        "  const boxes = c.obstacles.flatMap(G.obstacleBoxes);\n"
        "  const r = G.objectObstacleClosest(c.obj, c.pos, c.quat, boxes);\n"
        "  return { d: r.d, tilt: G.tiltOf(c.quat), nboxes: boxes.length,\n"
        "           pd: G.pointBoxDistance(c.pos, boxes[0]) };\n"
        "});\n"
        "console.log(JSON.stringify(out));\n")
    p = subprocess.run([NODE, "--input-type=module", "-e", src, json.dumps(cases)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _cases(n=40, seed=7):
    rng = np.random.default_rng(seed)
    objs = [
        {"id": "cup_0", "kind": "cup", "size": {"outer_half": [0.03, 0.03, 0.05], "wall": 0.004}},
        {"id": "block_0", "kind": "block", "size": {"half": [0.02, 0.025, 0.02]}},
        {"id": "marker_0", "kind": "marker", "size": {"radius": 0.012, "half_length": 0.06}},
    ]
    wall = {"id": "wall_0", "kind": "wall", "pose": {"pos": [0.01, -0.1, 0.0], "quat": [0.923879532511287, 0, 0, 0.382683432365090]},
            "half": None, "length": 0.4, "height": 0.3, "thickness": 0.02,
            "slots": [{"y_min": -0.05, "y_max": 0.08, "z_min": 0.0, "z_max": 0.16}]}
    box = {"id": "box_0", "kind": "box", "pose": {"pos": [-0.2, 0.15, 0.06], "quat": [0.980343180180, 0.0, 0.100035018386, 0.170059531256]},
           "half": [0.04, 0.03, 0.06], "length": None, "height": None, "thickness": None, "slots": []}
    out = []
    for i in range(n):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        out.append({"obj": objs[i % 3], "pos": list(rng.uniform([-0.3, -0.3, 0.0], [0.3, 0.3, 0.3])),
                    "quat": list(q), "obstacles": [wall, box] if i % 2 else [box, wall]})
    return out


def test_geometry_matches_checker():
    cases = _cases()
    js = _run_js(cases)
    for c, r in zip(cases, js):
        obj = ObjectSpec.model_validate({**c["obj"], "mass": 0.05, "pose": {"pos": [0, 0, 0], "quat": [1, 0, 0, 0]}})
        boxes = [b for o in c["obstacles"] for b in geom.obstacle_boxes(ObstacleSpec.model_validate(o))]
        d = float(geom.object_obstacle_distance(obj, np.array([c["pos"]]), np.array([c["quat"]]), boxes)[0])
        assert r["nboxes"] == len(boxes)
        assert abs(r["d"] - d) < 1e-6, (c["obj"]["kind"], r["d"], d)
        assert abs(r["tilt"] - geom.tilt_of(c["quat"])) < 1e-6
        assert abs(r["pd"] - float(geom.point_box_distance(np.array(c["pos"]), boxes[0]))) < 1e-9
