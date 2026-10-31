"""Pixel-level check that an edited frame kept the kit objects' geometry.

The ground-truth masks belong to the original render. They stay valid for an
edited frame only if the edit left every object where it was, at the same size.
Colours may change freely (lighting, material), so the test compares where
the object outlines are, not their colour:

1. Framing. The edited image is resampled to the original size and brought
   back into register by one global similarity (scale about the centre and an
   integer shift), found by chamfer matching of edges. Image editors re-render
   the whole frame and drift by a few pixels; that camera-level drift is
   undone and reported, never applied per object. If the aspect ratio
   differs by more than 1 %, or no global similarity fits (mean edge distance
   above ``REG_MAX_FIT_PX``), the edit is ``reframed`` and no ground-truth
   mask applies.
2. Edges are taken from the gradient of the grey image divided by its local
   mean brightness, so a global change of illumination does not move or remove
   them. An edge pixel is one above ``EDGE_REL`` times the 99th percentile.
3. Per object, the outline of its ground-truth mask is matched against the
   edge map: ``support(d, s)`` is the share of outline pixels (shifted by d,
   scaled by s about the mask centre) that have an edge within 1 px.
   * ``kept``: the outline at d = 0, s = 1 keeps at least ``KEEP_REL`` of its
     support in the original, and no displaced outline does clearly better;
   * ``moved`` / ``resized``: a displaced or rescaled outline beats the
     original position by more than ``DISPLACE_MARGIN``;
   * ``missing``: no outline position keeps ``MISSING_REL`` of the original support;
   * ``weakened``: in between (outline partly lost, not displaced);
   * ``untested``: fewer than ``MIN_PIXELS`` visible pixels.

Thresholds were set on deterministic synthetic controls (``controls.py``,
exercised by the tests): illumination, colour, noise, blur and background
changes must pass; shifts of 4 px or more, 12 % rescaling and removal must be
flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

GLOBAL_SHIFT_PX = 3       # residual global shift after registration that still counts as reframed
REG_SCALES = (0.97, 0.98, 0.99, 1.0, 1.01, 1.02, 1.03)
REG_SHIFT_PX = 20
REG_MAX_FIT_PX = 2.5
REG_BAND_PX = 8           # registration uses original edges within this distance of a foreground mask
REG_MIN_POINTS = 200
REG_MIN_REGION_POINTS = 25      # registration fit worse than this: the frame is not a re-lit copy (reframed)
SEARCH_PX = 10
SCALES = (0.88, 0.94, 1.0, 1.06, 1.12)
MIN_PIXELS = 120
EDGE_REL = 0.12
EDGE_SIGMA = 1.6          # px; edges at this scale tolerate mild blur
MOVE_MIN_ALIGNED_PX = 1.2  # moved/resized also needs the original outline position to fit this badly
CHAMFER_CAP = 8.0          # px; distances are capped so far-away outliers count as "no edge"
DISPLACE_GAIN_PX = 0.9     # a displaced outline must fit this much better (mean px) to count as moved
MISSING_PX = 3.0           # even the best outline position is this far (mean px) from edges: missing
WEAK_PX = 2.0              # aligned fit this far from edges, but not displaced: weakened
SUPPORTED_PX = 1.5         # outline points this close to an original edge are used as evidence
MIN_EVIDENCE = 30
CLASSES = ("cup", "block", "marker")


def to_gray(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def edge_map(rgb: np.ndarray) -> np.ndarray:
    """Thin edges: gradient of the grey image divided by local mean brightness
    (so a global illumination change keeps them), non-maximum suppressed along
    the gradient direction, above ``EDGE_REL`` of the 99.5th percentile."""
    g = ndimage.gaussian_filter(to_gray(rgb), EDGE_SIGMA)
    gx, gy = ndimage.sobel(g, 1), ndimage.sobel(g, 0)
    mag = np.hypot(gx, gy) / (ndimage.uniform_filter(g, 15) + 8.0)
    ang = (np.rad2deg(np.arctan2(gy, gx)) + 180.0) % 180.0
    q = ((ang + 22.5) // 45).astype(int) % 4          # 0: horizontal gradient, 1: 45, 2: vertical, 3: 135
    pad = np.pad(mag, 1, mode="edge")
    H, W = mag.shape
    c = pad[1:H + 1, 1:W + 1]
    nb = {0: ((0, 1), (0, -1)), 1: ((1, 1), (-1, -1)), 2: ((1, 0), (-1, 0)), 3: ((1, -1), (-1, 1))}
    keep = np.zeros_like(mag, bool)
    for k, ((ay, ax), (by, bx)) in nb.items():
        a = pad[1 + ay:H + 1 + ay, 1 + ax:W + 1 + ax]
        b = pad[1 + by:H + 1 + by, 1 + bx:W + 1 + bx]
        keep |= (q == k) & (c >= a) & (c >= b)
    thr = EDGE_REL * float(np.percentile(mag, 99.5))
    return keep & (mag > thr)


def resample_to(rgb: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    from PIL import Image
    if (rgb.shape[1], rgb.shape[0]) == tuple(size):
        return rgb
    return np.asarray(Image.fromarray(np.asarray(rgb, np.uint8)).resize(size, Image.LANCZOS))


def _region_points(eo: np.ndarray, regions: list[np.ndarray], margin: int, cap: int = 4000):
    H, W = eo.shape
    pts = []
    rng = np.random.default_rng(0)
    for r in regions:
        ys, xs = np.nonzero(eo & r)
        ok = (ys >= margin) & (ys < H - margin) & (xs >= margin) & (xs < W - margin)
        ys, xs = ys[ok], xs[ok]
        if len(ys) < REG_MIN_REGION_POINTS:
            continue
        if len(ys) > cap:
            sel = rng.choice(len(ys), cap, replace=False)
            ys, xs = ys[sel], xs[sel]
        pts.append((ys, xs))
    return pts


def _search(eo, ee_rgb, pts, scales, shifts_x, shifts_y):
    best = None
    for s in scales:
        warped = warp_similarity(ee_rgb, s, 0, 0)
        D = np.minimum(ndimage.distance_transform_edt(~edge_map(warped)), CHAMFER_CAP)
        for dy in shifts_y:
            for dx in shifts_x:
                v = float(np.median([D[ys + dy, xs + dx].mean() for ys, xs in pts]))
                key = (round(v, 6), abs(s - 1.0), abs(dx) + abs(dy))
                if best is None or key < best[0]:
                    best = (key, s, dx, dy)
    return best


def _half(rgb: np.ndarray) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.fromarray(rgb).resize((rgb.shape[1] // 2, rgb.shape[0] // 2), Image.BILINEAR))


def register(eo: np.ndarray, ee_rgb: np.ndarray, *, scales=REG_SCALES, max_shift: int = REG_SHIFT_PX,
             regions: list[np.ndarray] | None = None, original_rgb: np.ndarray | None = None):
    """Global similarity (scale about the image centre, then integer shift) that
    best maps the edited image onto the original.

    The cost of a candidate is the median, over regions (each object, the
    gripper and the background), of the mean chamfer distance from the
    original's edges in that region to the warped edited image's edges. The
    median makes one moved object unable to pull the registration with it.
    Coarse to fine: every scale and shift at half resolution (when the
    original image is given), then a +-2 px refinement at full resolution.
    Returns (scale, dx, dy, fit_px, warped_rgb)."""
    regions = regions if regions else [np.ones_like(eo)]
    pts = _region_points(eo, regions, max_shift) or _region_points(eo, [np.ones_like(eo)], max_shift)
    cand_s, cx, cy = list(scales), range(-max_shift, max_shift + 1), range(-max_shift, max_shift + 1)
    if original_rgb is not None and len(scales) > 1:
        eo2 = edge_map(_half(original_rgb))
        reg2 = [r[::2, ::2][:eo2.shape[0], :eo2.shape[1]] for r in regions]
        m2 = max_shift // 2 + 1
        pts2 = _region_points(eo2, reg2, m2) or _region_points(eo2, [np.ones_like(eo2)], m2)
        (_, s0, dx0, dy0) = _search(eo2, _half(ee_rgb), pts2, scales, range(-m2, m2 + 1), range(-m2, m2 + 1))
        i = list(scales).index(s0)
        cand_s = list(scales)[max(0, i - 1): i + 2]
        cx = range(max(-max_shift, 2 * dx0 - 2), min(max_shift, 2 * dx0 + 2) + 1)
        cy = range(max(-max_shift, 2 * dy0 - 2), min(max_shift, 2 * dy0 + 2) + 1)
    (fit, _, _), s, dx, dy = _search(eo, ee_rgb, pts, cand_s, cx, cy)
    return s, dx, dy, fit, warp_similarity(ee_rgb, s, dx, dy)


def warp_similarity(rgb: np.ndarray, s: float, dx: int, dy: int) -> np.ndarray:
    """Output pixel p takes input pixel c + (p - c + (dx, dy)) / s ... i.e. the
    content of ``rgb`` scaled by 1/s about the centre and shifted by (-dx, -dy),
    so that an edited image matching the original at offset (dx, dy) and scale s
    is brought back into register."""
    if s == 1.0 and dx == 0 and dy == 0:
        return rgb
    H, W = rgb.shape[:2]
    cy, cx = (H - 1) / 2.0, (W - 1) / 2.0
    m = np.array([[s, 0.0], [0.0, s]])
    off = np.array([cy, cx]) - m @ np.array([cy, cx]) + np.array([dy, dx]) * s
    return np.stack([ndimage.affine_transform(rgb[..., c].astype(np.float32), m, offset=off, order=1,
                                              mode="nearest") for c in range(3)], -1).clip(0, 255).astype(np.uint8)


def global_shift(eo: np.ndarray, ee: np.ndarray, max_shift: int = 12,
                 region: np.ndarray | None = None) -> tuple[int, int, float]:
    """Integer (dx, dy) minimising the mean chamfer distance from the original
    edges (inside ``region`` if given) to the edited edges."""
    D = np.minimum(ndimage.distance_transform_edt(~ee), CHAMFER_CAP)
    src = eo if region is None else (eo & region)
    ys, xs = np.nonzero(src[max_shift:-max_shift, max_shift:-max_shift])
    ys, xs = ys + max_shift, xs + max_shift
    if len(ys) == 0:
        return 0, 0, 0.0
    if len(ys) > 20000:
        sel = np.random.default_rng(0).choice(len(ys), 20000, replace=False)
        ys, xs = ys[sel], xs[sel]
    best = (0, 0, float(D[ys, xs].mean()))
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            v = float(D[ys + dy, xs + dx].mean())
            if v < best[2] - 1e-9:
                best = (dx, dy, v)
    return best


@dataclass
class ObjectGeometry:
    cls: str
    verdict: str                 # kept | moved | resized | missing | weakened | untested
    pixels: int
    shift_px: tuple[float, float] = (0.0, 0.0)
    scale: float = 1.0
    fit_original: float = 0.0     # mean outline-to-edge distance, px
    fit_aligned: float = 0.0
    fit_best: float = 0.0
    note: str = ""


@dataclass
class GeometryReport:
    verdict: str                 # preserved | changed | reframed
    size_in: tuple[int, int]
    size_out: tuple[int, int]
    global_shift_px: tuple[int, int]
    objects: dict[str, ObjectGeometry] = field(default_factory=dict)
    registration: dict = field(default_factory=dict)

    def changed_objects(self) -> list[str]:
        return [k for k, o in self.objects.items() if o.verdict in ("moved", "resized", "missing")]

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "size_in": list(self.size_in), "size_out": list(self.size_out),
                "global_shift_px": list(self.global_shift_px), "registration": self.registration,
                "objects": {k: {**vars(o), "shift_px": list(o.shift_px)} for k, o in self.objects.items()}}


def _outline(mask: np.ndarray) -> np.ndarray:
    return mask & ~ndimage.binary_erosion(mask, iterations=1, border_value=1)


def _scaled_outline_points(mask: np.ndarray, s: float) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.nonzero(mask)
    cy, cx = ys.mean(), xs.mean()
    if s == 1.0:
        oy, ox = np.nonzero(_outline(mask))
        return oy, ox
    H, W = mask.shape
    m = np.array([[1 / s, 0.0], [0.0, 1 / s]])
    off = np.array([cy, cx]) - m @ np.array([cy, cx])
    ms = ndimage.affine_transform(mask.astype(np.float32), m, offset=off, order=0) > 0.5
    oy, ox = np.nonzero(_outline(ms))
    return oy, ox


def _chamfer(D: np.ndarray, oy, ox, dx: int, dy: int) -> float:
    H, W = D.shape
    y, x = oy + dy, ox + dx
    ok = (y >= 0) & (y < H) & (x >= 0) & (x < W)
    if not ok.any():
        return CHAMFER_CAP
    return float(D[y[ok], x[ok]].mean())


def _evidence(mask: np.ndarray, occluders: np.ndarray, Do: np.ndarray, s: float):
    """Outline points (for scale ``s``) that are usable evidence: not next to
    another foreground region or the image border, and (judged at scale 1 in
    the original) lying on a real edge. Returns (ys, xs) for scale ``s``."""
    H, W = mask.shape
    oy, ox = _scaled_outline_points(mask, 1.0)
    near_occ = ndimage.binary_dilation(occluders, iterations=3)
    ok = (~near_occ[oy, ox]) & (oy > 3) & (oy < H - 4) & (ox > 3) & (ox < W - 4) & (Do[oy, ox] <= SUPPORTED_PX)
    if s == 1.0:
        return oy[ok], ox[ok]
    ys, xs = np.nonzero(mask)
    cy, cx = ys.mean(), xs.mean()
    return (np.rint(cy + (oy[ok] - cy) * s).astype(int), np.rint(cx + (ox[ok] - cx) * s).astype(int))


def check_object(Do: np.ndarray, De: np.ndarray, mask: np.ndarray, cls: str,
                 occluders: np.ndarray | None = None) -> ObjectGeometry:
    """Mean distance (px) from the object's edge-supported outline points to
    the nearest edge: in the original (``fit_original``), in the edited frame at
    the original position (``fit_aligned``) and at the best shift and scale
    (``fit_best``)."""
    n = int(mask.sum())
    if n < MIN_PIXELS:
        return ObjectGeometry(cls, "untested", n, note=f"fewer than {MIN_PIXELS} visible pixels")
    occ = occluders if occluders is not None else np.zeros_like(mask)
    oy, ox = _evidence(mask, occ, Do, 1.0)
    if len(oy) < MIN_EVIDENCE:
        return ObjectGeometry(cls, "untested", n, note=f"only {len(oy)} outline points lie on edges")
    fo = _chamfer(Do, oy, ox, 0, 0)
    fa = _chamfer(De, oy, ox, 0, 0)
    best = (0, 0, 1.0, fa)
    for s in SCALES:
        py, px = (oy, ox) if s == 1.0 else _evidence(mask, occ, Do, s)
        for dy in range(-SEARCH_PX, SEARCH_PX + 1):
            for dx in range(-SEARCH_PX, SEARCH_PX + 1):
                v = _chamfer(De, py, px, dx, dy)
                if v < best[3] - 1e-9:
                    best = (dx, dy, s, v)
    dx, dy, s, fb = best
    displaced = ((fa - fb) > DISPLACE_GAIN_PX and fa > MOVE_MIN_ALIGNED_PX
                 and (abs(dx) + abs(dy) > 1 or s != 1.0))
    if fb > MISSING_PX:
        verdict, note = "missing", "no outline position is supported by edges"
    elif displaced:
        verdict = "resized" if s != 1.0 and abs(dx) + abs(dy) <= 3 else "moved"
        note = f"outline best supported at shift ({dx}, {dy}) px, scale {s:g}"
    elif fa > WEAK_PX:
        verdict, note = "weakened", "outline edges partly lost but not displaced"
    else:
        verdict, note = "kept", ""
    return ObjectGeometry(cls, verdict, n, (float(dx), float(dy)), float(s), round(fo, 3), round(fa, 3),
                          round(fb, 3), note)


def check_geometry(original: np.ndarray, edited: np.ndarray, masks: dict[str, np.ndarray],
                   classes=CLASSES, *, registered: bool = True) -> tuple[GeometryReport, np.ndarray]:
    """Compare an edited frame with its original.

    The edited image is resampled to the original size and, with
    ``registered``, brought back into register by a global similarity
    (camera-level drift introduced by the editor; reported in
    ``registration``). Per-object changes are judged after registration.
    Returns the report and the registered image used for all later measures."""
    H, W = original.shape[:2]
    size_out = (int(edited.shape[1]), int(edited.shape[0]))
    ed = resample_to(edited, (W, H))
    eo = edge_map(original)
    # Registration and the residual-shift test use edges near the foreground
    # (objects and gripper), which an appearance edit must keep; background
    # edges may legitimately disappear (a new tabletop).
    fg = np.zeros(original.shape[:2], bool)
    bands = []
    for m in masks.values():
        m = np.asarray(m, bool)
        fg |= m
        if m.sum() >= MIN_PIXELS:
            bands.append(ndimage.binary_dilation(m, iterations=REG_BAND_PX))
    region = ndimage.binary_dilation(fg, iterations=REG_BAND_PX)
    bands.append(~region)                                   # background, as one more vote
    if int((eo & region).sum()) < REG_MIN_POINTS:
        region = None
    reg = {"applied": False, "scale": 1.0, "dx": 0, "dy": 0}
    if registered:
        s, rdx, rdy, fit, ed = register(eo, ed, regions=bands, original_rgb=original)
        reg = {"applied": True, "scale": s, "dx": int(rdx), "dy": int(rdy), "fit_px": round(fit, 3)}
    ee = edge_map(ed)
    if registered:
        gdx, gdy = 0, 0                    # registration already chose the best global alignment
    else:
        _, gdx, gdy, _, _ = register(eo, ed, scales=(1.0,), max_shift=8, regions=bands)
    rep = GeometryReport("preserved", (W, H), size_out, (gdx, gdy), registration=reg)
    bad_aspect = abs((size_out[0] / size_out[1]) / (W / H) - 1.0) > 0.01
    bad_fit = registered and reg["fit_px"] > REG_MAX_FIT_PX
    if bad_aspect or bad_fit or max(abs(gdx), abs(gdy)) > GLOBAL_SHIFT_PX:
        rep.verdict = "reframed"
        why = ("aspect ratio changed" if bad_aspect else
               "no global similarity brings the frame into register" if bad_fit else "residual global shift")
        for c in classes:
            rep.objects[c] = ObjectGeometry(c, "untested", int(np.asarray(masks.get(c, np.zeros(1))).sum()),
                                            note=f"frame reframed ({why}); ground truth does not apply")
        return rep, ed
    Do = np.minimum(ndimage.distance_transform_edt(~eo), CHAMFER_CAP)
    De = np.minimum(ndimage.distance_transform_edt(~ee), CHAMFER_CAP)
    for c in classes:
        if c in masks:
            occ = np.zeros(original.shape[:2], bool)
            for k, m in masks.items():
                if k != c:
                    occ |= m
            rep.objects[c] = check_object(Do, De, masks[c], c, occ)
    if rep.changed_objects():
        rep.verdict = "changed"
    return rep, ed
