import os

import numpy as np
import pytest

os.environ.setdefault("MUJOCO_GL", "osmesa")
os.environ.setdefault("PYOPENGL_PLATFORM", os.environ["MUJOCO_GL"])
imageio = pytest.importorskip("imageio")


def _backend_ok():
    try:
        import mujoco
        m = mujoco.MjModel.from_xml_string("<mujoco><worldbody><geom size='1 1 .1' type='plane'/></worldbody></mujoco>")
        r = mujoco.Renderer(m, 32, 32)
        r.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _backend_ok(), reason="no offscreen OpenGL backend")
def test_still_render_of_a_recorded_frame(tmp_path):
    from mirror.scenes import reference_scene
    from mirror.sim import load_demo
    from mirror.sim.render import FrameRenderer
    from mirror.sim.render import demo_frames
    trace = load_demo()
    scene = reference_scene("demo")
    frame = demo_frames(trace)[len(trace.frames) // 4]
    fr = FrameRenderer(scene, 160, 90)
    img = fr.render(frame)
    fr.close()
    assert img.shape == (90, 160, 3)
    assert img.std() > 5.0
