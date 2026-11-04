"""Upload admission: probing, decoding, bounds, cleanup and expiry.

Every video here is SYNTHETIC: ffmpeg's built-in test pattern, made on the fly.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mirror.studio import uploads as up
from mirror.studio.admission import Admission
from mirror.studio.app import create_app
from mirror.studio.session_adapter import StudioService
from mirror.studio.sessions import SessionManager
from mirror.studio.uploads import MediaQuota, UploadLimits, UploadRejected, validate_video

pytestmark = pytest.mark.skipif(not up.tools_available(), reason="ffmpeg/ffprobe needed")


def synth(path: Path, seconds=1.0, size="160x120", rate=10, faststart=True, codec="libx264") -> Path:
    """A SYNTHETIC test-pattern video (not a recording of anything)."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}",
           "-t", str(seconds), "-c:v", codec, "-pix_fmt", "yuv420p"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    subprocess.run(cmd + [str(path)], check=True, timeout=60, stdin=subprocess.DEVNULL)
    return path


@pytest.fixture(scope="module")
def vids(tmp_path_factory):
    d = tmp_path_factory.mktemp("synthetic_videos")
    good = synth(d / "good.mp4")
    data = good.read_bytes()
    (d / "truncated.mp4").write_bytes(data[: int(len(data) * 0.6)])      # moov first, frames cut off
    (d / "fake.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4000)
    synth(d / "long.mp4", seconds=4.0)
    synth(d / "big.mp4", size="640x480", seconds=0.6)
    synth(d / "fast.mp4", rate=50, seconds=1.0)
    return d


def svc(**kw):
    return StudioService(backend="fixture", session_id="u", private_runtime=True, **kw)


def files_in(s):
    return sorted(p.name for p in s.upload_dir().iterdir())


def test_valid_synthetic_video_is_probed_and_fully_decoded(vids):
    info = validate_video(vids / "good.mp4", UploadLimits())
    assert info["width"] == 160 and info["height"] == 120 and info["fps"] == 10.0
    assert info["frames"] == 10 and info["decoded_frames"] == 10 and info["codec"] == "h264"


@pytest.mark.parametrize("name,why", [("fake.mp4", "could not be read"), ("truncated.mp4", "")])
def test_undecodable_files_are_rejected_and_deleted(vids, name, why):
    s = svc()
    with pytest.raises(UploadRejected) as e:
        s.upload_video((vids / name).read_bytes())
    assert why in str(e.value)
    assert files_in(s) == [] and s.media.entries == {} and s.media.held_bytes() == 0


def test_header_alone_never_passes_without_ffprobe(vids, monkeypatch):
    s = svc()
    monkeypatch.setattr(up.shutil, "which", lambda name: None)
    with pytest.raises(UploadRejected) as e:
        s.upload_video((vids / "good.mp4").read_bytes())
    assert e.value.code == "uploads_unavailable" and e.value.status == 503
    assert files_in(s) == []


@pytest.mark.parametrize("field,value,name,msg", [
    ("max_duration_s", 2.0, "long.mp4", "long"),
    ("max_side", 320, "big.mp4", "640x480"),
    ("max_pixels", 100 * 100, "good.mp4", "pixels"),
    ("max_fps", 30.0, "fast.mp4", "frame rate"),
    ("max_frames", 5, "good.mp4", "frames"),
])
def test_bounds_on_duration_dimensions_rate_and_frames(vids, field, value, name, msg):
    s = svc(upload_limits=UploadLimits(**{field: value}))
    with pytest.raises(UploadRejected) as e:
        s.upload_video((vids / name).read_bytes())
    assert msg in str(e.value)
    assert files_in(s) == []


def test_unknown_duration_or_size_is_refused(vids, monkeypatch, tmp_path):
    real = up._run

    def no_duration(cmd, timeout, limits):
        r = real(cmd, timeout, limits)
        if "ffprobe" in cmd[0]:
            r = subprocess.CompletedProcess(cmd, 0, r.stdout.replace('"duration"', '"_d"'), r.stderr)
        return r
    monkeypatch.setattr(up, "_run", no_duration)
    with pytest.raises(UploadRejected, match="duration is unknown"):
        up.probe_video(vids / "good.mp4", UploadLimits())

    def no_size(cmd, timeout, limits):
        r = real(cmd, timeout, limits)
        if "ffprobe" in cmd[0]:
            r = subprocess.CompletedProcess(cmd, 0, r.stdout.replace('"width"', '"_w"'), r.stderr)
        return r
    monkeypatch.setattr(up, "_run", no_size)
    with pytest.raises(UploadRejected, match="dimensions are unknown"):
        up.probe_video(vids / "good.mp4", UploadLimits())


def test_decode_has_a_time_limit_and_kills_the_decoder(vids):
    lim = UploadLimits(decode_timeout_s=0.001)
    with pytest.raises(UploadRejected, match="longer than"):
        validate_video(vids / "long.mp4", lim)
    out = subprocess.run(["pgrep", "-f", str(vids / "long.mp4")], capture_output=True, text=True)
    assert out.stdout.strip() == ""


def test_aggregate_bytes_and_file_count_per_session(vids):
    data = (vids / "good.mp4").read_bytes()
    s = svc(upload_limits=UploadLimits(max_session_bytes=int(len(data) * 2.5), max_session_files=5))
    a = s.upload_video(data, "top")
    b = s.upload_video(data, "side")
    with pytest.raises(UploadRejected) as e:
        s.upload_video(data, "top")
    assert e.value.code == "too_large" and "session's media" in str(e.value)
    assert set(s.media.entries) == {a["id"], b["id"]}
    s2 = svc(upload_limits=UploadLimits(max_session_files=1))
    s2.upload_video(data, "top")
    with pytest.raises(UploadRejected, match="already holds"):
        s2.upload_video(data, "side")


def test_global_quota_across_sessions_and_release_on_close(vids):
    data = (vids / "good.mp4").read_bytes()
    q = MediaQuota(int(len(data) * 1.5))
    s1, s2 = svc(media_quota=q), svc(media_quota=q)
    s1.upload_video(data)
    with pytest.raises(UploadRejected) as e:
        s2.upload_video(data)
    assert e.value.code == "storage_full" and e.value.status == 507
    assert files_in(s2) == []
    s1.close("done")
    assert q.used == 0
    s2.upload_video(data)


def test_rejected_upload_gives_its_reservation_back(vids):
    q = MediaQuota(10 * 2**20)
    s = svc(media_quota=q)
    with pytest.raises(UploadRejected):
        s.upload_video((vids / "fake.mp4").read_bytes())
    assert q.used == 0


def test_replaced_and_abandoned_uploads_expire(vids):
    data = (vids / "good.mp4").read_bytes()
    s = svc(upload_limits=UploadLimits(ttl_s=60))
    clock = [1000.0]
    s.media.clock = lambda: clock[0]
    first = s.upload_video(data, "top")
    for e in s.media.entries.values():
        e.last_used = clock[0]
    second = s.upload_video(data, "top")            # replaces the unused first upload of the same view
    assert first["id"] not in s.media.entries and not Path(s.upload_dir() / f"{first['id']}.mp4").exists()
    s.media.entries[second["id"]].last_used = clock[0]
    s.media.acquire(second["id"])                    # a job is reading it
    clock[0] += 120
    assert s.media.sweep() == [] and second["id"] in s.media.entries
    s.media.release(second["id"], "g-used")          # used by a generation: kept while it exists
    clock[0] += 120
    assert s.media.sweep() == []
    s.media.forget_generations(set())                # that generation is gone
    assert s.media.sweep() == [second["id"]] and files_in(s) == []


def test_http_upload_requires_length_and_checks_it_before_reading(vids):
    mgr = SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, private_runtime=True,
                                                      upload_limits=UploadLimits(max_file_bytes=50_000)))
    with TestClient(create_app(mgr)) as c:
        r = c.post("/api/session").json()
        h = {"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]}
        big = c.post("/api/video/upload?view=top", headers={**h, "Content-Length": "999999999"}, content=b"x")
        assert big.status_code == 413
        def gen():
            yield b"abc"
        nolen = c.post("/api/video/upload?view=top", headers=h, content=gen())
        assert nolen.status_code == 411
        ok = c.post("/api/video/upload?view=top", headers=h, content=(vids / "good.mp4").read_bytes())
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["decoded_frames"] == 10 and len(body["sha256"]) == 64 and "path" not in body
        bad = c.post("/api/video/upload?view=top", headers=h, content=(vids / "fake.mp4").read_bytes())
        assert bad.status_code == 422 and bad.json()["error"] == "invalid_video"
        svc_ = mgr.get(r["session"], r["token"])
        assert [p.name for p in svc_.upload_dir().iterdir()] == [f"{body['id']}.mp4"]
