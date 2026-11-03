"""Calibrated real-camera (phone) capture for the supported kitting task.

Everything in this package is about footage of a person performing the
supported task with real props (a mug, a small box, a pen, a tray and an
upright book) filmed by one or two phones. It never assumes the simulator's
rendered-camera intrinsics, table size or kit geometry:

* ``target``      printable ChArUco calibration target with verified physical
                  dimensions (PDF/PNG) and its machine-readable spec.
* ``media``       ffprobe metadata, original frame timestamps, frame decoding.
* ``calibrate``   intrinsics and distortion, camera pose and metric scale on
                  the table plane, quality report, recalibration checks.
* ``sync``        observable synchronisation events and two-camera alignment;
                  refuses to pair takes that share no sync event.
* ``geometry``    rays, triangulation and table-plane intersection.
* ``track``       offline 2D observations: manual box prompts with classical
                  colour tracking, manual keyframes, or imported model tracks.
* ``proxy``       user-reviewed mapping from real objects to simulation proxies.
* ``reconstruct`` object trajectories with per-frame observability.
* ``events``      object-centric pick/release intervals for human-hand
                  demonstrations, with uncertainty and user correction.
* ``session``     the capture record that ties it together and gates spec
                  proposal on resolved review items.
* ``validation``  pre-registered metrics for real-footage validation.
* ``synthetic``   synthetic renders used by the tests (always labelled
                  synthetic; never evidence about real footage).
"""
