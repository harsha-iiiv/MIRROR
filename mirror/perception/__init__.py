"""Perception: rendered demonstration videos with simulator ground truth, and
the reconstruction of a DemoTrace from video (segmentation masks -> table-plane
trajectories, grasp/release events and cup tilt).

Ground truth comes from the simulator only and is used for measurement, never
as an input to reconstruction.
"""
import mirror.sim  # noqa: F401  (selects the off-screen GL backend before mujoco loads)
from mirror.perception.cameras import CAMERAS, CameraInfo, camera_info, project
from mirror.perception.masks import rle_decode, rle_encode, mask_iou, mask_centroid
from mirror.perception.video import DemoVideo, load_demo_video, render_demo_video, render_frame
from mirror.perception.reconstruct import Tracks, estimate, scene_from_video, trace_from_estimates, tracks_from_ground_truth
from mirror.perception.metrics import perception_metrics
from mirror.perception.track import load_tracks, save_tracks, track, track_videos
from mirror.perception.pipeline import trace_from_video

__all__ = ["CAMERAS", "CameraInfo", "camera_info", "project", "rle_encode", "rle_decode", "mask_iou",
           "mask_centroid", "DemoVideo", "render_demo_video", "load_demo_video", "render_frame", "Tracks",
           "estimate", "scene_from_video", "trace_from_estimates", "tracks_from_ground_truth", "perception_metrics",
           "track", "track_videos", "load_tracks", "save_tracks", "trace_from_video"]
