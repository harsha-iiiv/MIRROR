"""Build compact display meshes of the UR5e/UR10e for the 3D workspace from MuJoCo Menagerie.

Reads the Menagerie model directory (visual OBJ meshes + materials), decimates each body's meshes
(quadric simplification) and writes one GLB per arm with one node per body (base, shoulder_link,
... wrist_3_link), mesh vertices in the body frame exactly as MuJoCo places the visual geoms.
Display only: collision geometry and kinematics in the simulation are unchanged.
Licence: BSD-3-Clause (Menagerie / ROS-Industrial); the LICENSE text is copied next to the GLB.

usage: PYTHONPATH=/path/to/trimesh python scripts/viz_build_arm_meshes.py --menagerie DIR --out web/js/viz/assets
"""
from __future__ import annotations

import argparse
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np


def build(model_dir: Path, arm: str, out: Path, ratio: float) -> dict:
    import fast_simplification
    import trimesh
    root = ET.parse(model_dir / f"{arm}.xml").getroot()
    mats = {m.get("name"): [float(x) for x in m.get("rgba").split()] for m in root.iter("material") if m.get("rgba")}
    scene = trimesh.Scene()
    stats = {}
    for body in root.iter("body"):
        name = body.get("name")
        by_mat: dict[str, list] = {}
        for g in body.findall("geom"):
            if not g.get("mesh"):
                continue
            if g.get("pos") or g.get("quat"):
                raise SystemExit(f"{name}: visual geom with pos/quat not handled")
            m = trimesh.load(model_dir / "assets" / f"{g.get('mesh')}.obj", force="mesh", process=True)
            by_mat.setdefault(g.get("material"), []).append(m)
        tri_in = tri_out = 0
        for mat, ms in by_mat.items():
            mesh = trimesh.util.concatenate(ms)
            mesh.merge_vertices(merge_tex=True, merge_norm=True)   # OBJ faces arrive unshared
            tri_in += len(mesh.faces)
            target = max(0.02, min(1.0, ratio))
            v, f = fast_simplification.simplify(np.asarray(mesh.vertices, np.float32), np.asarray(mesh.faces, np.int32), target_reduction=1 - target)
            sm = trimesh.Trimesh(v, f, process=True)
            trimesh.smoothing  # noqa: B018 - keep import
            rgba = mats.get(mat, [0.7, 0.7, 0.7, 1])
            sm.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                name=mat, baseColorFactor=[int(255 * c) for c in rgba], metallicFactor=0.1 if mat != "linkgray" else 0.35,
                roughnessFactor=0.45 if mat != "black" else 0.6))
            tri_out += len(sm.faces)
            scene.add_geometry(sm, node_name=f"{name}:{mat}", geom_name=f"{name}:{mat}", parent_node_name=None)
        stats[name] = (tri_in, tri_out)
    out.mkdir(parents=True, exist_ok=True)
    data = scene.export(file_type="glb")
    (out / f"{arm}.glb").write_bytes(data)
    return {"bytes": len(data), "bodies": stats}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--menagerie", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ratio", type=float, default=0.08)
    a = ap.parse_args(argv)
    out = Path(a.out)
    for arm in ("ur10e", "ur5e"):
        d = Path(a.menagerie) / f"universal_robots_{arm}"
        r = build(d, arm, out, a.ratio)
        shutil.copyfile(d / "LICENSE", out / f"{arm}.LICENSE")
        print(arm, r["bytes"], {k: v for k, v in r["bodies"].items()})
    (out / "NOTICE").write_text(
        "ur10e.glb, ur5e.glb: display meshes decimated from the MuJoCo Menagerie models\n"
        "universal_robots_ur10e and universal_robots_ur5e (https://github.com/google-deepmind/mujoco_menagerie),\n"
        "derived from the ROS-Industrial ur_e_description URDF. Copyright 2018 ROS Industrial Consortium.\n"
        "License: BSD-3-Clause (full text in ur10e.LICENSE / ur5e.LICENSE). Built by scripts/viz_build_arm_meshes.py.\n"
        "\"Universal Robots\", \"UR5e\" and \"UR10e\" are trademarks of Universal Robots A/S; used to identify the model only.\n")


if __name__ == "__main__":
    main()
