import os

os.environ.setdefault("MUJOCO_GL", "osmesa" if not os.environ.get("DISPLAY") else "glfw")
os.environ.setdefault("PYOPENGL_PLATFORM", os.environ["MUJOCO_GL"])
