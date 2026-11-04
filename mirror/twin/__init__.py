"""Twin: the simulated run drawn on the person's own table, from their own phone.

Everything here is display. It reuses the capture's calibrated camera (intrinsics + table
pose) and the scene's placement to put the MuJoCo rollout in the phone's image. Accuracy is
bounded by the capture: one camera, and whatever calibration labels the session carries.
"""
from mirror.twin.camera import PhoneCamera, VirtualCamera  # noqa: F401
