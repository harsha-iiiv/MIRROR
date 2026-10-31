"""Generate a MuJoCo model from a SceneState.

Everything is derived from the SceneState and the shared reference geometry
in :mod:`mirror.geometry`, so the planner's collision envelope and the
physics agree.

Robot. The robot is a free-flying parallel-jaw gripper (no arm body), driven
by position actuators on six joints:

    gripper_x, gripper_y, gripper_z   slide joints along world x, y, z
    gripper_yaw                       hinge about world z
    gripper_roll                      hinge about the yawed x axis
    gripper_pitch                     hinge about the gripper's own y axis

so the gripper orientation is R = Rz(yaw) @ Rx(roll) @ Ry(pitch). The pitch
axis is the jaw's closing axis: pitching by 90 degrees lays a top-grasped
cup on its side with its axis along world x (the wall normal), and this
ordering keeps that pose far from the joint chain's singularity
(roll = +/-90 degrees). Two finger slide joints (gripper_finger_l / _r) each
carry one finger; their joint value is the distance from the jaw centre to
that finger's pad, so the jaw opening is their sum.

Contact labels. Every geom gets a label used for contact classification:
"table", "tray:<id>", "obstacle:<id>", "gripper", an object id, or "bead".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

from mirror.contracts import ObjectSpec, ObstacleSpec, SceneState, TraySpec
from mirror.geometry import CUP, GRIPPER, WORLD
from mirror.scenes.objects import TRAY_FLOOR_THICKNESS

TIMESTEP = 0.004

# Geom visual groups.
GROUP_STATIC, GROUP_ROBOT, GROUP_OBJECT, GROUP_BEAD = 0, 1, 2, 3

# Joint limits of the wrist (radians).
YAW_RANGE = (-1.5 * math.pi, 1.5 * math.pi)
ROLL_RANGE = (-math.radians(80.0), math.radians(80.0))
PITCH_RANGE = (-math.radians(135.0), math.radians(135.0))
FINGER_RANGE = (0.0, GRIPPER.max_opening / 2.0)

# Actuation. Gains are chosen for a stiff, critically damped position servo
# at TIMESTEP with the implicit-fast integrator.
SLIDE_KP, SLIDE_FORCE = 4000.0, 80.0
HINGE_KP, HINGE_TORQUE = 60.0, 12.0
FINGER_KP, FINGER_FORCE = 1500.0, 12.0
CARRIAGE_MASS = 0.4
WRIST_ARMATURE = 0.006
FINGER_ARMATURE = 0.05

FRICTION_PAD = (GRIPPER.pad_friction, 0.02, 0.0005)
FRICTION_OBJECT = (0.8, 0.005, 0.0001)
FRICTION_STATIC = (0.7, 0.005, 0.0001)
FRICTION_BEAD = (0.4, 0.002, 0.0005)

ROBOT_JOINTS = ("gripper_x", "gripper_y", "gripper_z", "gripper_yaw", "gripper_roll", "gripper_pitch")
FINGER_JOINTS = ("gripper_finger_l", "gripper_finger_r")


@dataclass
class SceneIndex:
    """Lookup tables from scene ids to MuJoCo ids for one compiled model."""

    object_ids: list[str]
    object_kinds: dict[str, str]
    object_body: dict[str, int]
    object_qposadr: dict[str, int]          # start of the object's free joint in qpos
    object_dofadr: dict[str, int]           # start of the object's free joint in qvel
    bead_bodies: dict[str, list[int]]       # cup id -> bead body ids
    bead_qposadr: dict[str, list[int]]
    geom_label: list[str]                   # per geom
    geom_owner: list[str]                   # per geom: object id, "gripper", "bead:<cup>", or label
    finger_l_geom: int
    finger_r_geom: int
    gripper_body: int
    robot_qposadr: list[int]                # x, y, z, yaw, roll, pitch
    robot_dofadr: list[int]
    finger_qposadr: list[int]
    robot_actuators: list[int]
    finger_actuators: list[int]
    static_bodies: list[int]
    dynamic_bodies: list[int]
    body_names: list[str]
    tray_ids: list[str]
    obstacle_ids: list[str]
    label_codes: dict[str, int] = field(default_factory=dict)
    geom_label_code: np.ndarray | None = None


def _f(v) -> str:
    return " ".join(f"{float(x):.6g}" for x in v)


def _quat(q) -> str:
    return _f(q)


def _box(name, pos, half, rgba, group, friction, contype=1, conaffinity=1, mass=None,
         condim=None, quat=None, extra=""):
    attrs = [f'name="{name}"', 'type="box"', f'pos="{_f(pos)}"', f'size="{_f(half)}"',
             f'rgba="{_f(rgba)}"', f'group="{group}"', f'friction="{_f(friction)}"',
             f'contype="{contype}"', f'conaffinity="{conaffinity}"']
    if mass is not None:
        attrs.append(f'mass="{mass:.6g}"')
    if condim is not None:
        attrs.append(f'condim="{condim}"')
    if quat is not None:
        attrs.append(f'quat="{_quat(quat)}"')
    return f"<geom {' '.join(attrs)} {extra}/>"


# ------------------------------------------------------------------ static


def _table_xml() -> str:
    hx, hy, hz = WORLD.table_half
    return _box("table", (0, 0, -hz), (hx, hy, hz), (0.86, 0.85, 0.82, 1), GROUP_STATIC,
                FRICTION_STATIC)


def _tray_xml(tray: TraySpec) -> str:
    hx, hy = tray.inner_half
    t, h = tray.wall_thickness, tray.wall_height
    ft = TRAY_FLOOR_THICKNESS
    rgba = (0.55, 0.60, 0.58, 1)
    geoms = [
        _box(f"{tray.id}/floor", (0, 0, -ft / 2), (hx + t, hy + t, ft / 2), rgba, GROUP_STATIC,
             FRICTION_STATIC),
        _box(f"{tray.id}/wall_xp", (hx + t / 2, 0, h / 2), (t / 2, hy + t, h / 2), rgba,
             GROUP_STATIC, FRICTION_STATIC),
        _box(f"{tray.id}/wall_xn", (-hx - t / 2, 0, h / 2), (t / 2, hy + t, h / 2), rgba,
             GROUP_STATIC, FRICTION_STATIC),
        _box(f"{tray.id}/wall_yp", (0, hy + t / 2, h / 2), (hx, t / 2, h / 2), rgba,
             GROUP_STATIC, FRICTION_STATIC),
        _box(f"{tray.id}/wall_yn", (0, -hy - t / 2, h / 2), (hx, t / 2, h / 2), rgba,
             GROUP_STATIC, FRICTION_STATIC),
    ]
    return (f'<body name="{tray.id}" pos="{_f(tray.pose.pos)}" quat="{_quat(tray.pose.quat)}">'
            + "".join(geoms) + "</body>")


def wall_segments(ob: ObstacleSpec) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Boxes (centre, half extents) in the wall's local frame that make up a
    wall with rectangular through-slots."""
    L, H, T = float(ob.length), float(ob.height), float(ob.thickness)
    y0, y1 = -L / 2, L / 2
    slots = sorted(ob.slots, key=lambda s: s.y_min)
    boxes = []
    cur = y0
    for s in slots:
        lo, hi = max(s.y_min, y0), min(s.y_max, y1)
        if hi <= lo:
            continue
        if lo > cur:  # full-height column before the slot
            boxes.append(((0, (cur + lo) / 2, H / 2), (T / 2, (lo - cur) / 2, H / 2)))
        yc, yh = (lo + hi) / 2, (hi - lo) / 2
        if s.z_min > 0:
            boxes.append(((0, yc, s.z_min / 2), (T / 2, yh, s.z_min / 2)))
        if s.z_max < H:
            boxes.append(((0, yc, (s.z_max + H) / 2), (T / 2, yh, (H - s.z_max) / 2)))
        cur = max(cur, hi)
    if cur < y1:
        boxes.append(((0, (cur + y1) / 2, H / 2), (T / 2, (y1 - cur) / 2, H / 2)))
    return boxes


def _obstacle_xml(ob: ObstacleSpec) -> str:
    rgba = (0.70, 0.68, 0.64, 1) if ob.kind == "wall" else (0.62, 0.58, 0.52, 1)
    if ob.kind == "wall":
        geoms = [_box(f"{ob.id}/seg{i}", c, h, rgba, GROUP_STATIC, FRICTION_STATIC)
                 for i, (c, h) in enumerate(wall_segments(ob))]
    else:
        geoms = [_box(f"{ob.id}/box", (0, 0, 0), ob.half, rgba, GROUP_STATIC, FRICTION_STATIC)]
    return (f'<body name="{ob.id}" pos="{_f(ob.pose.pos)}" quat="{_quat(ob.pose.quat)}">'
            + "".join(geoms) + "</body>")


# ----------------------------------------------------------------- objects


def cup_parts(obj: ObjectSpec) -> list[tuple[str, tuple, tuple]]:
    """(name suffix, centre, half extents) of the five boxes of an open-top cup."""
    hx, hy, hz = obj.size["outer_half"]
    w = float(obj.size.get("wall", CUP.wall))
    return [
        ("floor", (0, 0, -hz + w / 2), (hx, hy, w / 2)),
        ("wall_xp", (hx - w / 2, 0, w / 2), (w / 2, hy, hz - w / 2)),
        ("wall_xn", (-hx + w / 2, 0, w / 2), (w / 2, hy, hz - w / 2)),
        ("wall_yp", (0, hy - w / 2, w / 2), (hx - w, w / 2, hz - w / 2)),
        ("wall_yn", (0, -hy + w / 2, w / 2), (hx - w, w / 2, hz - w / 2)),
    ]


def bead_offsets(obj: ObjectSpec) -> list[tuple[float, float, float]]:
    """Initial bead centres in the cup frame: a single layer on the cup floor."""
    hx, hy, hz = obj.size["outer_half"]
    w = float(obj.size.get("wall", CUP.wall))
    r = obj.bead_radius
    pitch = 2 * r + 0.002
    ix, iy = hx - w - r - 0.0005, hy - w - r - 0.0005
    nx = max(1, int(2 * ix / pitch) + 1)
    ny = max(1, int(2 * iy / pitch) + 1)
    xs = np.linspace(-ix, ix, nx) if nx > 1 else [0.0]
    ys = np.linspace(-iy, iy, ny) if ny > 1 else [0.0]
    cells = [(float(x), float(y)) for y in ys for x in xs]
    out = []
    layer = 0
    while len(out) < obj.beads:
        z = -hz + w + r + 0.0005 + layer * (2 * r + 0.001)
        for x, y in cells:
            if len(out) >= obj.beads:
                break
            out.append((x, y, z))
        layer += 1
    return out


def _rotate(q, v):
    w, x, y, z = q
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                  [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                  [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
    return R @ np.asarray(v, dtype=float)


def _object_xml(obj: ObjectSpec) -> tuple[str, str]:
    """(object body xml, bead bodies xml)."""
    rgba = obj.rgba or (0.8, 0.8, 0.8, 1)
    head = (f'<body name="{obj.id}" pos="{_f(obj.pose.pos)}" quat="{_quat(obj.pose.quat)}">'
            f'<freejoint name="{obj.id}/free"/>')
    geoms = []
    if obj.kind == "cup":
        parts = cup_parts(obj)
        vols = [8 * h[0] * h[1] * h[2] for _, _, h in parts]
        tot = sum(vols)
        for (nm, c, h), v in zip(parts, vols):
            geoms.append(_box(f"{obj.id}/{nm}", c, h, rgba, GROUP_OBJECT, FRICTION_OBJECT,
                              mass=obj.mass * v / tot))
    elif obj.kind == "block":
        geoms.append(_box(f"{obj.id}/box", (0, 0, 0), obj.size["half"], rgba, GROUP_OBJECT,
                          FRICTION_OBJECT, mass=obj.mass))
    elif obj.kind == "marker":
        r, hl = obj.size["radius"], obj.size["half_length"]
        geoms.append(f'<geom name="{obj.id}/body" type="cylinder" size="{r:.6g} {hl:.6g}" '
                     f'rgba="{_f(rgba)}" group="{GROUP_OBJECT}" friction="{_f(FRICTION_OBJECT)}" '
                     f'mass="{obj.mass:.6g}"/>')
    else:  # pragma: no cover - guarded by the contract's Literal
        raise ValueError(obj.kind)
    body = head + "".join(geoms) + "</body>"
    beads = []
    if obj.kind == "cup" and obj.beads > 0:
        p0 = np.asarray(obj.pose.pos)
        for i, off in enumerate(bead_offsets(obj)):
            p = p0 + _rotate(obj.pose.quat, off)
            beads.append(
                f'<body name="{obj.id}/bead{i}" pos="{_f(p)}"><freejoint name="{obj.id}/bead{i}/free"/>'
                f'<geom name="{obj.id}/bead{i}" type="sphere" size="{obj.bead_radius:.6g}" '
                f'rgba="0.95 0.75 0.15 1" group="{GROUP_BEAD}" friction="{_f(FRICTION_BEAD)}" '
                f'mass="{CUP.bead_mass:.6g}"/></body>')
    return body, "".join(beads)


# ------------------------------------------------------------------- robot


def _robot_xml(scene: SceneState) -> str:
    ws = scene.workspace
    ph = GRIPPER.palm_half
    fh = GRIPPER.finger_half
    small = 'mass="0.02" diaginertia="2e-5 2e-5 2e-5"'
    pad = (0.25, 0.25, 0.28, 1)
    palm = (0.18, 0.18, 0.20, 1)
    return f"""
<body name="gripper_carriage" pos="0 0 0" gravcomp="1">
  <inertial pos="0 0 0" mass="{CARRIAGE_MASS}" diaginertia="1e-3 1e-3 1e-3"/>
  <joint name="gripper_x" type="slide" axis="1 0 0" range="{_f(ws.x_range)}" limited="true" damping="2"/>
  <joint name="gripper_y" type="slide" axis="0 1 0" range="{_f(ws.y_range)}" limited="true" damping="2"/>
  <joint name="gripper_z" type="slide" axis="0 0 1" range="{_f(ws.z_range)}" limited="true" damping="2"/>
  <body name="gripper_yaw_link" gravcomp="1">
    <inertial pos="0 0 0" {small}/>
    <joint name="gripper_yaw" type="hinge" axis="0 0 1" range="{_f(YAW_RANGE)}" limited="true" armature="{WRIST_ARMATURE}" damping="0.05"/>
    <body name="gripper_roll_link" gravcomp="1">
      <inertial pos="0 0 0" {small}/>
      <joint name="gripper_roll" type="hinge" axis="1 0 0" range="{_f(ROLL_RANGE)}" limited="true" armature="{WRIST_ARMATURE}" damping="0.05"/>
      <body name="gripper" gravcomp="1">
        <joint name="gripper_pitch" type="hinge" axis="0 1 0" range="{_f(PITCH_RANGE)}" limited="true" armature="{WRIST_ARMATURE}" damping="0.05"/>
        {_box("gripper/palm", (0, 0, GRIPPER.palm_center_z), ph, palm, GROUP_ROBOT, FRICTION_OBJECT, mass=0.25)}
        <site name="tcp" pos="0 0 0" size="0.004" rgba="1 0 0 0.6" group="{GROUP_ROBOT}"/>
        <body name="gripper_finger_l" gravcomp="1">
          <joint name="gripper_finger_l" type="slide" axis="0 1 0" range="{_f(FINGER_RANGE)}" limited="true" damping="1" armature="{FINGER_ARMATURE}"/>
          {_box("gripper/finger_l", (0, fh[1], GRIPPER.finger_center_z), fh, pad, GROUP_ROBOT, FRICTION_PAD, mass=0.03, condim=4)}
        </body>
        <body name="gripper_finger_r" gravcomp="1">
          <joint name="gripper_finger_r" type="slide" axis="0 -1 0" range="{_f(FINGER_RANGE)}" limited="true" damping="1" armature="{FINGER_ARMATURE}"/>
          {_box("gripper/finger_r", (0, -fh[1], GRIPPER.finger_center_z), fh, pad, GROUP_ROBOT, FRICTION_PAD, mass=0.03, condim=4)}
        </body>
      </body>
    </body>
  </body>
</body>"""


def _actuators_xml(scene: SceneState) -> str:
    ws = scene.workspace
    rows = []
    for j, rng in (("gripper_x", ws.x_range), ("gripper_y", ws.y_range), ("gripper_z", ws.z_range)):
        rows.append(f'<position name="{j}" joint="{j}" kp="{SLIDE_KP}" dampratio="1" '
                    f'ctrlrange="{_f(rng)}" ctrllimited="true" forcerange="{-SLIDE_FORCE} {SLIDE_FORCE}" forcelimited="true"/>')
    for j, rng in (("gripper_yaw", YAW_RANGE), ("gripper_roll", ROLL_RANGE), ("gripper_pitch", PITCH_RANGE)):
        rows.append(f'<position name="{j}" joint="{j}" kp="{HINGE_KP}" dampratio="1" '
                    f'ctrlrange="{_f(rng)}" ctrllimited="true" forcerange="{-HINGE_TORQUE} {HINGE_TORQUE}" forcelimited="true"/>')
    for j in FINGER_JOINTS:
        rows.append(f'<position name="{j}" joint="{j}" kp="{FINGER_KP}" dampratio="1" '
                    f'ctrlrange="{_f(FINGER_RANGE)}" ctrllimited="true" forcerange="{-FINGER_FORCE} {FINGER_FORCE}" forcelimited="true"/>')
    return "<actuator>" + "".join(rows) + "</actuator>"


def scene_xml(scene: SceneState) -> str:
    statics = [_table_xml()]
    bodies = []
    for tr in scene.trays:
        bodies.append(_tray_xml(tr))
    for ob in scene.obstacles:
        bodies.append(_obstacle_xml(ob))
    objs, beads = [], []
    for o in scene.objects:
        b, bb = _object_xml(o)
        objs.append(b)
        beads.append(bb)
    return f"""<mujoco model="mirror_{scene.scene_id}">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="{TIMESTEP}" integrator="implicitfast" tolerance="1e-6" cone="elliptic" impratio="10" gravity="0 0 -9.81">
    <flag multiccd="enable"/>
  </option>
  <size memory="64M"/>
  <visual>
    <global offwidth="1280" offheight="720"/>
    <headlight ambient="0.45 0.45 0.45" diffuse="0.55 0.55 0.55" specular="0.1 0.1 0.1"/>
    <quality shadowsize="2048"/>
  </visual>
  <asset>
    <texture name="sky" type="skybox" builtin="gradient" rgb1="0.96 0.96 0.95" rgb2="0.82 0.83 0.84" width="64" height="64"/>
  </asset>
  <worldbody>
    <light pos="0.3 -0.8 1.8" dir="-0.15 0.4 -1" diffuse="0.6 0.6 0.6" castshadow="true"/>
    {''.join(statics)}
    {''.join(bodies)}
    {_robot_xml(scene)}
    {''.join(objs)}
    {''.join(beads)}
  </worldbody>
  {_actuators_xml(scene)}
</mujoco>"""


# ------------------------------------------------------------------- index


def _label_for_geom(name: str, scene: SceneState) -> tuple[str, str]:
    """(contact label, owner) for a geom name."""
    if name == "table":
        return "table", "table"
    head = name.split("/")[0]
    if head == "gripper":
        return "gripper", "gripper"
    tray_ids = {t.id for t in scene.trays}
    obs_ids = {o.id for o in scene.obstacles}
    obj_ids = {o.id for o in scene.objects}
    if head in tray_ids:
        return f"tray:{head}", f"tray:{head}"
    if head in obs_ids:
        return f"obstacle:{head}", f"obstacle:{head}"
    if head in obj_ids:
        if "/bead" in name:
            return "bead", f"bead:{head}"
        return head, head
    return name, name


def build_model(scene: SceneState) -> tuple[mujoco.MjModel, SceneIndex]:
    """Compile the MuJoCo model for ``scene`` and return it with its index."""
    ids = [o.id for o in scene.objects]
    if len(set(ids)) != len(ids):
        raise ValueError("object ids must be unique")
    model = mujoco.MjModel.from_xml_string(scene_xml(scene))

    def bid(n):
        return mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, n)

    def jid(n):
        return mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n)

    def aid(n):
        return mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, n)

    def gid(n):
        return mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, n)

    labels, owners = [], []
    for g in range(model.ngeom):
        lab, own = _label_for_geom(model.geom(g).name, scene)
        labels.append(lab)
        owners.append(own)
    codes: dict[str, int] = {}
    for lab in labels:
        codes.setdefault(lab, len(codes))
    bead_bodies, bead_q = {}, {}
    for o in scene.objects:
        if o.kind == "cup":
            bead_bodies[o.id] = [bid(f"{o.id}/bead{i}") for i in range(o.beads)]
            bead_q[o.id] = [int(model.jnt_qposadr[jid(f"{o.id}/bead{i}/free")]) for i in range(o.beads)]
    body_names = [model.body(b).name for b in range(model.nbody)]
    static_bodies = [b for b in range(1, model.nbody) if model.body_weldid[b] == 0]
    dynamic_bodies = [b for b in range(1, model.nbody) if model.body_weldid[b] != 0]
    idx = SceneIndex(
        object_ids=ids,
        object_kinds={o.id: o.kind for o in scene.objects},
        object_body={o.id: bid(o.id) for o in scene.objects},
        object_qposadr={o.id: int(model.jnt_qposadr[jid(f"{o.id}/free")]) for o in scene.objects},
        object_dofadr={o.id: int(model.jnt_dofadr[jid(f"{o.id}/free")]) for o in scene.objects},
        bead_bodies=bead_bodies,
        bead_qposadr=bead_q,
        geom_label=labels,
        geom_owner=owners,
        finger_l_geom=gid("gripper/finger_l"),
        finger_r_geom=gid("gripper/finger_r"),
        gripper_body=bid("gripper"),
        robot_qposadr=[int(model.jnt_qposadr[jid(j)]) for j in ROBOT_JOINTS],
        robot_dofadr=[int(model.jnt_dofadr[jid(j)]) for j in ROBOT_JOINTS],
        finger_qposadr=[int(model.jnt_qposadr[jid(j)]) for j in FINGER_JOINTS],
        robot_actuators=[aid(j) for j in ROBOT_JOINTS],
        finger_actuators=[aid(j) for j in FINGER_JOINTS],
        static_bodies=static_bodies,
        dynamic_bodies=dynamic_bodies,
        body_names=body_names,
        tray_ids=[t.id for t in scene.trays],
        obstacle_ids=[o.id for o in scene.obstacles],
        label_codes=codes,
        geom_label_code=np.array([codes[lab] for lab in labels], dtype=np.int32),
    )
    return model, idx
