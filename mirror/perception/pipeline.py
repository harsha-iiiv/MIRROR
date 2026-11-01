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


def trace_from_video(top_video, side_video=None, *, fps: Optional[float] = None, intrinsics: Optional[dict] = None,
                     prompts=None, client=None, tracks=None, demo_id: Optional[str] = None,
                     return_details: bool = False):
    """Reconstruct a DemoTrace (source "video_grounded") from a top-camera video
    and an optional side-camera video.

    ``intrinsics``: camera -> 3x3 K (pixels). Defaults to the demonstration
    cameras' vertical field of view at the video's resolution; supply the real
    camera's intrinsics for other footage. With ``return_details`` returns
    (trace, estimates, scene, checks, tracks)."""
    import imageio.v2 as imageio
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    from mirror.perception.track import _objects_from, track_videos
    videos = {"top": Path(top_video)}
    if side_video is not None:
        videos["side"] = Path(side_video)
    if fps is None:
        fps = float(imageio.get_reader(str(top_video)).get_meta_data().get("fps", 15.0))
    if tracks is None:
        tracks = track_videos(videos, fps=fps, objects=_objects_from(prompts), client=client)
    K = {}
    for cam in videos:
        if intrinsics and cam in intrinsics:
            K[cam] = intrinsics[cam]
        else:
            w, h = tracks.size[cam]
            K[cam] = intrinsics_for(w, h, CAMERAS.get(cam, CAMERAS["top"])["fovy"])
    est = estimate(tracks, K)
    did = demo_id or Path(top_video).parent.name + "_from_video"
    scene, checks = scene_from_video(tracks, est, scene_id=did)
    trace = trace_from_estimates(tracks, est, scene, demo_id=did, video_path=str(top_video))
    if return_details:
        return trace, est, scene, checks, tracks
    return trace
