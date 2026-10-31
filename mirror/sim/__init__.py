"""MuJoCo simulation: model generation, plan execution and teleoperation."""
import os as _os

# MuJoCo picks its OpenGL backend when it is first imported. Without a display,
# default to software offscreen rendering (OSMesa) so the video helpers work;
# an explicit MUJOCO_GL always wins.
if "MUJOCO_GL" not in _os.environ and not _os.environ.get("DISPLAY"):
    _os.environ["MUJOCO_GL"] = "osmesa"
if _os.environ.get("MUJOCO_GL") == "osmesa":
    _os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")
from mirror.sim.model import SceneIndex, build_model
from mirror.sim.env import ExecutionResult, SimEnv, visual_scene
from mirror.sim.teleop import Teleop
from mirror.sim.demo import REFERENCE_DEMO_PATH, load_demo, record_scripted_demo, save_demo

__all__ = ["SceneIndex", "build_model", "SimEnv", "ExecutionResult", "visual_scene", "Teleop",
           "record_scripted_demo", "save_demo", "load_demo", "REFERENCE_DEMO_PATH"]
