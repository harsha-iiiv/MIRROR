"""Video -> DemoTrace in one call."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np

from mirror.perception.cameras import CAMERAS


def intrinsics_for(width: int, height: int, fovy_deg: float) -> list:
    f = (height / 2.0) / math.tan(math.radians(fovy_deg) / 2.0)
    return [[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]]


from mirror.perception.calibration import CalibrationRequired  # noqa: E402  (re-exported)


def trace_from_video(top_video, side_video=None, *, fps: Optional[float] = None, intrinsics: Optional[dict] = None,
                     prompts=None, client=None, tracks=None, demo_id: Optional[str] = None,
                     return_details: bool = False, footage: Optional[str] = None,
                     calibration=None, models: Optional[list] = None, actor: Optional[str] = None):
    """Reconstruct a DemoTrace (source "video_grounded") from a top-camera video
    and an optional side-camera video.

    ``footage`` states what the video is, and decides what may be assumed:

    * ``"rendered"``: rendered by MIRROR's simulator. The rendered cameras'
      intrinsics, the kit table size and the kit object sizes are known and used.
    * ``"camera"``: real camera footage. Requires ``calibration`` (a
      mirror.perception.calibration.RigCalibration with each camera's intrinsics
      and its pose from a calibration board on the table, plus confirmed object
      identities). Nothing from the rendered set-up is assumed; without it this
      raises CalibrationRequired.
    * ``None`` (unspecified, e.g. an upload of unknown origin): reconstructed as
      if rendered, but the trace's evidence is ``unverified_video_reconstruction``
      with an ``abstain`` review item, so it cannot support a specification until
      a person confirms the footage or calibrates the camera.

    ``actor``: who moves the objects ("robot_gripper", "human_hand", "unknown").
    Defaults: rendered footage "robot_gripper"; camera footage "human_hand"
    (object-centric events, never robot contact); unspecified "unknown".

    ``intrinsics``: camera -> 3x3 K; with ``footage="rendered"`` overrides the
    rendered cameras' defaults. With ``return_details`` returns
    (trace, estimates, scene, checks, tracks)."""
    import imageio.v2 as imageio
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    from mirror.perception.track import _objects_from, track_videos
    if footage not in (None, "rendered", "camera"):
        raise ValueError(f"footage must be 'rendered', 'camera' or None, not {footage!r}")
    if actor is None:
        actor = {"rendered": "robot_gripper", "camera": "human_hand"}.get(footage, "unknown")
    if footage == "camera":
        if calibration is None:
            raise CalibrationRequired([
                "real camera footage needs a calibration: camera intrinsics from a checkerboard (or the camera "
                "maker's values), each camera's pose from a board lying on the table, the board's position in "
                "the robot workspace, and a person's confirmation of which tracked object is the cup, block, "
                "marker and tray (docs/CAMERA_CAPTURE.md)"])
        calibration.require_ready()
    videos = {"top": Path(top_video)}
    if side_video is not None:
        videos["side"] = Path(side_video)
    if fps is None:
        fps = float(imageio.get_reader(str(top_video)).get_meta_data().get("fps", 15.0))
    if tracks is None:
        tracks = track_videos(videos, fps=fps, objects=_objects_from(prompts), client=client)
    if footage == "camera":
        tracks = calibration.apply_correspondence(tracks)
        est = estimate(tracks, {c: calibration.cameras[c].K for c in videos if c in calibration.cameras},
                       calib_override=calibration.calibs(), intrinsics_source=calibration.intrinsics_source(),
                       actor=actor)
        kind = "camera_video_reconstruction"
    else:
        K = {}
        for cam in videos:
            if intrinsics and cam in intrinsics:
                K[cam] = intrinsics[cam]
            else:
                w, h = tracks.size[cam]
                K[cam] = intrinsics_for(w, h, CAMERAS.get(cam, CAMERAS["top"])["fovy"])
        src = "rendered_camera_model" if footage == "rendered" else "assumed_rendered_model"
        est = estimate(tracks, K, intrinsics_source=src, event_timing="gripper_lowest", actor=actor)
        kind = "rendered_video_reconstruction" if footage == "rendered" else "unverified_video_reconstruction"
    did = demo_id or Path(top_video).parent.name + "_from_video"
    scene, checks = scene_from_video(tracks, est, scene_id=did)
    if footage == "camera":
        scene = calibration.to_workspace(scene)
    media = {}
    for cam, p in videos.items():
        try:
            import hashlib
            media[cam] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        except OSError:
            pass
    trace = trace_from_estimates(tracks, est, scene, demo_id=did, video_path=str(top_video), kind=kind,
                                 media=media, models=models)
    if footage == "camera":
        trace.evidence.manual_corrections.extend(calibration.manual_corrections())
    if return_details:
        return trace, est, scene, checks, tracks
    return trace
