"""Which offscreen OpenGL backend MuJoCo should use on this machine.

MuJoCo reads MUJOCO_GL when it is first imported, so the choice must be made
before ``import mujoco``. An explicit MUJOCO_GL always wins. Otherwise:
macOS uses CGL (built in; OSMesa and EGL are not available there), and other
systems use OSMesa (software, needs the system OSMesa library). EGL (GPU) is
used only when asked for explicitly.
"""
from __future__ import annotations

import os
import sys
from typing import Mapping, MutableMapping, Optional


def offscreen_backend(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> str:
    """The backend for offscreen rendering: MUJOCO_GL if set, else cgl on macOS, else osmesa."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if env.get("MUJOCO_GL"):
        return env["MUJOCO_GL"]
    return "cgl" if platform == "darwin" else "osmesa"


def default_backend(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Optional[str]:
    """For plain simulation imports: like ``offscreen_backend``, but on Linux
    with a display (DISPLAY set) MuJoCo is left to choose (None)."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if env.get("MUJOCO_GL"):
        return env["MUJOCO_GL"]
    if platform != "darwin" and env.get("DISPLAY"):
        return None
    return offscreen_backend(env, platform)


_DEFAULTED: dict[str, str] = {}


def apply(backend: Optional[str], env: Optional[MutableMapping[str, str]] = None) -> Optional[str]:
    """Set MUJOCO_GL (and PYOPENGL_PLATFORM for osmesa/egl) unless already set."""
    env = os.environ if env is None else env
    if backend and not env.get("MUJOCO_GL"):
        env["MUJOCO_GL"] = backend
        if env is os.environ:
            _DEFAULTED["MUJOCO_GL"] = backend
    if env.get("MUJOCO_GL") in ("osmesa", "egl"):
        env.setdefault("PYOPENGL_PLATFORM", env["MUJOCO_GL"])
    return env.get("MUJOCO_GL")


def describe(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> str:
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if env.get("MUJOCO_GL"):
        if env is os.environ and _DEFAULTED.get("MUJOCO_GL") == env["MUJOCO_GL"]:
            return f"{env['MUJOCO_GL']} (default for {platform})"
        return f"{env['MUJOCO_GL']} (MUJOCO_GL set)"
    b = default_backend(env, platform)
    return f"{b} (default for {platform})" if b else f"MuJoCo default (display {env.get('DISPLAY')})"


def probe_context() -> tuple[bool, str]:
    """Try to create a tiny offscreen context with the current backend."""
    try:
        import mujoco
        ctx = mujoco.GLContext(16, 16)
        ctx.make_current()
        ctx.free()
        return True, "offscreen context created"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:160]
