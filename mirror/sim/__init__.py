"""MuJoCo simulation: model generation, plan execution and teleoperation."""
import os as _os

# MuJoCo picks its OpenGL backend when it is first imported. An explicit
# MUJOCO_GL always wins; otherwise CGL on macOS, OSMesa on other systems without
# a display (see mirror/glbackend.py).
from mirror import glbackend as _gl

_gl.apply(_gl.default_backend())
from mirror.sim.model import SceneIndex, build_model
from mirror.sim.env import ExecutionResult, SimEnv, visual_scene
from mirror.sim.teleop import Teleop
from mirror.sim.demo import REFERENCE_DEMO_PATH, load_demo, record_scripted_demo, save_demo

__all__ = ["SceneIndex", "build_model", "SimEnv", "ExecutionResult", "visual_scene", "Teleop",
           "record_scripted_demo", "save_demo", "load_demo", "REFERENCE_DEMO_PATH"]
