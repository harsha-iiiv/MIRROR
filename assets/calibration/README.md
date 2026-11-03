# Calibration target

- `mirror_charuco_v1_A4.pdf`, `mirror_charuco_v1_Letter.pdf`: page 1 is the
  5 x 7 ChArUco board (30 mm squares, 21 mm markers, 5x5_100 ids 0-16) with a
  50 mm scale bar and instructions; page 2 is the sync card (marker 90) and
  optional 20 mm object tags (95-97). Print at 100% and measure the scale bar.
- `mirror_charuco_v1_A4_300dpi.png`: raster of page 1 (no text); prefer the PDF.
- `mirror_charuco_v1.json`: the machine-readable spec.

Regenerate and verify: `python -m mirror.capture.target assets/calibration`
(writes the files and checks the PDF geometry against OpenCV's board layout).
Filming instructions: `docs/CAMERA_CAPTURE.md`.
