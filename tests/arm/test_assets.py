"""Vendored arm models: licence, provenance and integrity."""
from pathlib import Path

import mujoco
import numpy as np
import pytest

from mirror.arm.model import ARMS, ARM_JOINTS, DEFAULT_MOUNT, MENAGERIE_COMMIT, build_arm_model

ASSETS = Path(__file__).resolve().parents[2] / "mirror" / "assets"


@pytest.mark.parametrize("key", sorted(ARMS))
def test_vendored_model_has_licence_and_provenance(key):
    d = ASSETS / f"universal_robots_{key}"
    lic = (d / "LICENSE").read_text()
    assert "Copyright 2018 ROS Industrial Consortium" in lic
    assert "Redistribution and use in source and binary forms" in lic
    xml = (d / f"{key}.xml").read_text()
    assert MENAGERIE_COMMIT in xml and "BSD-3-Clause" in xml
    assert "Modifications:" in xml
    # no mesh assets are referenced (visual meshes were removed)
    assert "<mesh " not in xml and 'mesh="' not in xml
    assert (ASSETS / "NOTICE").exists() and key in (ASSETS / "NOTICE").read_text().lower()


@pytest.mark.parametrize("key", sorted(ARMS))
def test_vendored_model_compiles_with_original_kinematics(key):
    spec = ARMS[key]
    m = mujoco.MjModel.from_xml_path(str(spec.xml))
    assert m.njnt == 6 and m.nu == 6
    names = [m.joint(j).name for j in range(m.njnt)]
    assert names == list(ARM_JOINTS)
    # UR elbow is limited to +/- pi, the other joints to +/- 2 pi
    rng = np.array([m.jnt_range[j] for j in range(6)])
    assert np.allclose(rng[2], [-3.1415, 3.1415])
    assert np.allclose(rng[[0, 1, 3, 4, 5]], [[-6.28319, 6.28319]] * 5)


def test_scene_model_replaces_the_free_flying_gripper(scenes):
    m, idx = build_arm_model(scenes["shortcut"], DEFAULT_MOUNT)
    names = [m.joint(j).name for j in range(m.njnt)]
    assert not any(n.startswith("gripper_x") or n == "gripper_yaw" for n in names), \
        "the arm model must not keep the legacy Cartesian gripper joints"
    assert [n for n in names if n.startswith("arm/")] == [f"arm/{j}" for j in ARM_JOINTS]
    assert len(idx.arm_actuators) == 6 and len(idx.finger_actuators) == 2
    # every robot geom is labelled, the gripper keeps its legacy geometry names
    assert {"gripper/palm", "gripper/finger_l", "gripper/finger_r"} <= {m.geom(g).name for g in range(m.ngeom)}
    assert all(idx.geom_owner[g] == "arm" for g in idx.arm_geoms) and idx.arm_geoms
