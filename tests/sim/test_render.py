import pytest

pytest.importorskip("imageio")
import imageio.v2 as imageio  # noqa: E402


def _backend_ok():
    try:
        import mujoco
        m = mujoco.MjModel.from_xml_string("<mujoco><worldbody><geom size='1 1 .1' type='plane'/></worldbody></mujoco>")
        r = mujoco.Renderer(m, 32, 32)
        r.close()
        return True
    except Exception:
        return False


needs_gl = pytest.mark.skipif(not _backend_ok(), reason="no offscreen OpenGL backend")


@pytest.fixture(scope="module")
def short_rollout():
    from mirror.contracts import Rollout, RolloutReport
    from mirror.scenes import reference_scene
    from mirror.sim import SimEnv
    from tests.sim.plans import slot_plan
    scene = reference_scene("shortcut")
    env = SimEnv(scene)
    res = env.execute(slot_plan(scene, tilt_deg=90, place=False), max_sim_time=9.0)
    report = RolloutReport(rollout_id="t", scene_id=scene.scene_id, spec_id="s", spec_version=1,
                           condition="test", status="timeout")
    return Rollout(report=report, visual=env.visual_scene(), frames=res.frames)


def test_display_model_draws_every_body(short_rollout):
    import mujoco
    from mirror.sim.render import BEAD_RGBA, display_xml
    m = mujoco.MjModel.from_xml_string(display_xml(short_rollout.visual))
    moving = {b.name for b in short_rollout.visual.bodies if not b.static}
    names = {m.body(i).name for i in range(m.nbody)}
    assert moving <= names
    assert set(short_rollout.frames[0].bodies) <= names
    beads = [i for i in range(m.ngeom) if m.body(m.geom_bodyid[i]).name.startswith("cup_0/bead")]
    assert beads and all(tuple(m.geom_rgba[i].round(2)) == tuple(round(v, 2) for v in BEAD_RGBA) for i in beads)


def test_trail_colour_follows_tilt():
    from mirror.sim.render import tilt_rgba
    assert tilt_rgba(5) == tilt_rgba(15)
    side, slight = tilt_rgba(90), tilt_rgba(20)
    assert side != slight != tilt_rgba(5)
    assert side[0] > 2 * side[1]        # on its side reads red


@needs_gl
def test_render_stills_and_video_from_saved_rollout(short_rollout, tmp_path):
    from mirror.sim.render import render_rollout, render_stills
    path = tmp_path / "r.json"
    path.write_text(short_rollout.model_dump_json())
    stills = render_stills(path, [0.5, 8.0], tmp_path, camera="side", width=320, height=180,
                           trail_objects=["cup_0"])
    img = imageio.imread(stills[-1])
    assert img.shape == (180, 320, 3) and img.std() > 10
    out = render_rollout(short_rollout, tmp_path / "v.mp4", camera="top", width=320, height=176,
                         speed=4.0, hold_end=0.0)
    assert out.stat().st_size > 1000
    frames = imageio.mimread(out)
    assert len(frames) >= 10 and frames[0].shape[:2] == (176, 320)


@needs_gl
def test_demonstration_renders_without_beads(tmp_path):
    from mirror.sim import load_demo
    from mirror.sim.render import demo_frames, render_stills, _with_fingers
    from mirror.sim.env import visual_scene
    from mirror.sim.model import build_model
    from mirror.contracts import VisualScene
    trace = load_demo()
    model, index = build_model(trace.scene)
    vis = VisualScene(bodies=[b for b in visual_scene(model, index).bodies if "/bead" not in b.name])
    frames = [_with_fingers(f) for f in demo_frames(trace)[:300]]
    assert any(f.phase == "transport:cup_0" for f in frames)
    p = render_stills((vis, frames), [9.0], tmp_path, width=320, height=180)
    assert imageio.imread(p[0]).std() > 10
