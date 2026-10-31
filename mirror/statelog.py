"""Dense per-physics-step state log.

The simulator fills a StateLog at every physics step while executing a plan.
The checker (mirror.evaluate) is a pure function of (StateLog, TaskSpec,
SceneState), so any recorded behaviour can be re-checked against any later
version of a specification, e.g. to show that an old trajectory violates a
newly confirmed rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class ContactRecord:
    step: int
    object_id: str          # movable object involved ("gripper" for the robot)
    other: str              # other body/geom group: an object id, "table",
                            # "tray:<id>", "obstacle:<id>", "gripper", "bead"
    depth: float = 0.0


@dataclass
class StateLog:
    dt: float                                   # physics timestep
    t: np.ndarray                                # (N,)
    object_ids: list[str]
    obj_pos: np.ndarray                          # (N, K, 3)
    obj_quat: np.ndarray                         # (N, K, 4) w,x,y,z
    gripper_pos: np.ndarray                      # (N, 3)
    gripper_quat: np.ndarray                     # (N, 4)
    gripper_width: np.ndarray                    # (N,)
    held: np.ndarray                             # (N,) index into object_ids, -1 if none
    phase: list[str]                             # (N,) executor phase label
    beads: dict[str, np.ndarray] = field(default_factory=dict)  # cup id -> (N, B, 3)
    contacts: list[ContactRecord] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.t.shape[0])

    def index(self, object_id: str) -> int:
        return self.object_ids.index(object_id)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        arrays = dict(dt=np.array(self.dt), t=self.t, obj_pos=self.obj_pos,
                      obj_quat=self.obj_quat, gripper_pos=self.gripper_pos,
                      gripper_quat=self.gripper_quat, gripper_width=self.gripper_width,
                      held=self.held, object_ids=np.array(self.object_ids),
                      phase=np.array(self.phase))
        for k, v in self.beads.items():
            arrays[f"beads__{k}"] = v
        if self.contacts:
            arrays["c_step"] = np.array([c.step for c in self.contacts])
            arrays["c_obj"] = np.array([c.object_id for c in self.contacts])
            arrays["c_other"] = np.array([c.other for c in self.contacts])
            arrays["c_depth"] = np.array([c.depth for c in self.contacts])
        import json
        arrays["meta"] = np.array(json.dumps(self.meta))
        np.savez_compressed(path, **arrays)

    @classmethod
    def load(cls, path: str | Path) -> "StateLog":
        import json
        z = np.load(path, allow_pickle=False)
        beads = {k[len("beads__"):]: z[k] for k in z.files if k.startswith("beads__")}
        contacts = []
        if "c_step" in z.files:
            contacts = [ContactRecord(int(s), str(o), str(x), float(d)) for s, o, x, d in
                        zip(z["c_step"], z["c_obj"], z["c_other"], z["c_depth"])]
        return cls(dt=float(z["dt"]), t=z["t"], object_ids=[str(s) for s in z["object_ids"]],
                   obj_pos=z["obj_pos"], obj_quat=z["obj_quat"], gripper_pos=z["gripper_pos"],
                   gripper_quat=z["gripper_quat"], gripper_width=z["gripper_width"],
                   held=z["held"], phase=[str(s) for s in z["phase"]], beads=beads,
                   contacts=contacts, meta=json.loads(str(z["meta"])))


def tilt_deg(quat: np.ndarray) -> np.ndarray:
    """Angle between body +z and world +z for (..., 4) w,x,y,z quaternions."""
    q = np.asarray(quat, dtype=float)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    zz = 1.0 - 2.0 * (x * x + y * y)   # world-z component of body z axis
    return np.degrees(np.arccos(np.clip(zz, -1.0, 1.0)))
