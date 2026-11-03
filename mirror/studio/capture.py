"""Real-phone capture sessions in the studio, wired to the offline phone pipeline (mirror.capture).

The studio owns storage, validation, state, review history and provenance; ``mirror.capture``
does calibration, synchronisation, tracking, reconstruction and human-hand events
(docs/CAMERA_CAPTURE.md). Flow:

    create -> clips (validated uploads) -> inputs (print check, objects/proxies, tray, tags)
           -> prompts (a box per object per camera, drawn on GET .../frame/<role>/<index>)
           -> run (job: mirror.capture.session.build_session, offline, classical tracking, no model calls)
           -> review (mapping confirmations, identity confirmations, event corrections, each with the
              time the person spent) -> automatic re-run
           -> propose (only when every blocking review item is resolved: the session's gate is open)

A review item is never marked resolved by the studio. Blocking items clear only when the person
supplies what the pipeline needs (a confirmation or a correction) and the pipeline re-runs; items
such as a failed print check or a rejected lens calibration need new input (reprint, re-film).
Nothing is reconstructed when a step is missing.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

ROLES = ("take_top", "take_side", "lens_top", "lens_side")
ROLE_ALIASES = {"top": "take_top", "side": "take_side", "single": "take_top", "calibration": "lens_top"}
PIPELINE_ROLE = {"take_top": ("take", "top"), "take_side": ("take", "side"),
                 "lens_top": ("lens", "top"), "lens_side": ("lens", "side")}
MAX_PROMPTS = 24
MAX_OBJECTS = 8
MAX_CORRECTIONS = 200
NEEDS_NEW_INPUT = {"print_scale_invalid": "reprint the target at 100% and measure it again",
                   "intrinsics_missing": "film a lens-calibration clip for this phone",
                   "calibration_invalid": "film a better lens-calibration clip",
                   "recalibration_required": "recalibrate: the camera changed",
                   "pose_invalid": "film the take again with the board flat on the table at the start",
                   "camera_moved": "film the take again without moving the phone",
                   "no_calibrated_camera": "calibrate at least one camera",
                   "sync_refused": "film both phones during one take with both sync events",
                   "multiple_trays": "map only one object to the tray",
                   "views_disagree": "check identities and synchronisation, or film again"}
HOW_TO_RESOLVE = {"mapping_unconfirmed": "confirm the object and its proxy (objects[].confirmed_by_person)",
                  "identity_uncertain": "confirm the reacquired track (identity_confirmations)",
                  "ambiguous_event_interval": "set or confirm the event time (corrections)",
                  # the destination tray: settled by the person's inputs, no new footage needed
                  "tray_not_mapped": "map the tray: map an object as proxy tray (objects, with its measured inside "
                                     "width and depth in real_dims) and draw its box in each camera (prompts), or enter "
                                     "the tray region on the board (tray {center_xy, inner_half})",
                  "tray_size_unsupported": "measure the tray's inside width and depth again and correct them (tray "
                                           "inner_half, or the tray mapping's real_dims); supported 6-60 cm per side",
                  "tray_footprint_assumed": "optional: measure the tray's inside width and depth with a ruler (tray "
                                            "inner_half, or real_dims on the tray mapping); the catalogue size is used "
                                            "until then"}
# Which request settles a review item the person can resolve (the wizard shows this action).
ACTIONS = {"mapping_unconfirmed": {"via": ["review"], "fields": ["objects[].confirmed_by_person", "objects[].proxy"]},
           "identity_uncertain": {"via": ["review"], "fields": ["identity_confirmations"]},
           "ambiguous_event_interval": {"via": ["review"], "fields": ["corrections"]},
           "tray_not_mapped": {"via": ["inputs", "prompts"],
                               "fields": ["objects[proxy=tray].real_dims.width|depth", "prompts[label=<tray label>]",
                                          "tray.center_xy", "tray.inner_half"]},
           "tray_size_unsupported": {"via": ["inputs"], "fields": ["tray.inner_half", "objects[proxy=tray].real_dims"]},
           "tray_footprint_assumed": {"via": ["inputs"], "fields": ["tray.inner_half", "objects[proxy=tray].real_dims"],
                                      "optional": True}}


class CaptureError(ValueError):
    pass


def capture_available() -> tuple[bool, str]:
    try:
        import cv2  # noqa: F401
        import mirror.capture.session  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - optional extra
        return False, f"the phone-capture pipeline is not installed on this server ({type(exc).__name__})"
    return True, ""


def norm_role(role: str) -> str:
    r = ROLE_ALIASES.get(role, role)
    if r not in ROLES:
        raise CaptureError(f"clip role is one of {list(ROLES)}")
    return r


class Capture:
    def __init__(self, mode: str = "two_view", notes: str = "", synthetic: bool = False):
        if mode not in ("two_view", "single_view"):
            raise CaptureError("mode is two_view or single_view")
        self.id = "cap" + uuid.uuid4().hex[:10]
        self.created_at = time.time()
        self.mode = mode
        self.notes = notes[:500]
        self.synthetic = bool(synthetic)        # rendered test clips; labelled everywhere
        self.clips: dict[str, dict] = {}
        self.declared_sync: Optional[dict] = None
        self.inputs: dict[str, Any] = {"print_check": None, "objects": [], "prompts": [], "tags": {}, "tray": None,
                                       "identity_confirmations": [], "corrections": []}
        self.session: Optional[dict] = None     # CaptureSession JSON of the last run
        self.summary: list[str] = []
        self.runs = 0
        self.stale = False                      # inputs changed since the last run
        self.review_log: list[dict] = []        # every confirmation/correction with seconds spent
        self.proposal: Optional[dict] = None
        self.errors: list[str] = []
        self.state = "awaiting_clips"
        self.calibration_preview: Optional[dict] = None   # calibrate step: lens, pose and sync before tracking
        self.reviewed = False

    # ------------------------------------------------------------ state
    def missing(self) -> list[str]:
        out = []
        if "take_top" not in self.clips:
            out.append("the top take clip (take_top)")
        if self.mode == "two_view" and "take_side" not in self.clips:
            out.append("the side take clip (take_side)")
        if not self.inputs["print_check"]:
            out.append("the print check (measured 50 mm bar and one square)")
        if not self.inputs["objects"]:
            out.append("the objects and their proxies")
        if not self.inputs["prompts"]:
            out.append("a box around each object in each camera (prompts)")
        return out

    def refresh_state(self) -> None:
        """awaiting_clips -> clips_ready -> calibrated -> tracked -> reviewed -> proposed (``stale`` when the
        inputs changed since the last run; ``missing`` lists what tracking still needs)."""
        if self.state in ("running", "proposing"):
            return
        if self.proposal is not None and self.proposal.get("spec_version_id") and not self.stale:
            self.state = "proposed"
        elif self.session is not None:
            self.state = "reviewed" if self.reviewed else "tracked"
        elif self.calibration_preview is not None:
            self.state = "calibrated"
        elif "take_top" in self.clips:
            self.state = "clips_ready"
        else:
            self.state = "awaiting_clips"

    def gate(self) -> tuple[bool, list[dict]]:
        if self.session is None:
            return False, []
        blocking = [r for r in self.session.get("review", []) if r["severity"] == "abstain" and not r.get("resolved")]
        return not blocking, blocking

    def effort(self) -> dict:
        secs = [e["seconds_spent"] for e in self.review_log if e.get("seconds_spent") is not None]
        by: dict[str, int] = {}
        for e in self.review_log:
            by[e["kind"]] = by.get(e["kind"], 0) + 1
        return {"actions": len(self.review_log), "by_kind": by, "seconds_spent_total": round(sum(secs), 1) if secs else None,
                "actions_without_time": sum(1 for e in self.review_log if e.get("seconds_spent") is None)}

    def report(self) -> Optional[dict]:
        """Calibration quality, sync result, mapping, events and review items from the last run."""
        s = self.session
        if s is None:
            return None
        ok, blocking = self.gate()
        cal = {}
        for cam, intr in (s.get("intrinsics") or {}).items():
            cal[cam] = {"accepted": intr.get("accepted"), "rms_px": intr.get("rms_px"), "n_views": intr.get("n_views"),
                        "source": intr.get("source"), "checks": intr.get("checks", [])}
        poses = {cam: {"accepted": p.get("accepted"), "rms_px": p.get("rms_px"), "holdout_mm": p.get("holdout_mm"),
                       "distance_m": p.get("distance_m"), "checks": p.get("checks", [])}
                 for cam, p in (s.get("poses") or {}).items()}
        recon = s.get("reconstruction") or {}
        objs = {}
        for lab, tr in (recon.get("objects") or {}).items():
            st = tr.get("pos_status") or []
            ts = tr.get("tilt_status") or []
            n = max(1, len(st))
            objs[lab] = {"proxy": tr.get("proxy"),
                         "coverage": {k: round(sum(1 for x in st if x == k) / n, 3)
                                      for k in ("measured", "inferred", "interpolated", "unobserved")},
                         "tilt_observed": round(sum(1 for x in ts if x == "measured") / max(1, len(ts)), 3)}
        mappings = []
        try:
            from mirror.capture.proxy import ObjectMapping
            for m in s.get("mappings") or []:
                mappings.append({**m, "fidelity_statement": ObjectMapping.model_validate(m).fidelity_statement()})
        except Exception:  # noqa: BLE001
            mappings = s.get("mappings") or []
        review = []
        for i, r in enumerate(s.get("review") or []):
            review.append({**r, "index": i, "blocking": r["severity"] == "abstain" and not r.get("resolved"),
                           "how_to_resolve": NEEDS_NEW_INPUT.get(r["code"]) or HOW_TO_RESOLVE.get(r["code"]),
                           "needs_new_input": r["code"] in NEEDS_NEW_INPUT,
                           "action": ACTIONS.get(r["code"])})
        return {"session_id": s.get("session_id"), "evidence_kind": s.get("evidence_kind"), "synthetic": s.get("synthetic"),
                "mode": s.get("mode"), "calibration": cal, "poses": poses, "recalibration": s.get("recalibration"),
                "camera_shift_px": s.get("camera_shift_px"), "sync": s.get("sync"), "mappings": mappings, "tray": s.get("tray"),
                "objects": objs, "events": s.get("events") or [], "review": review, "gate_open": ok,
                "blocking": len(blocking), "track_sources": s.get("track_sources"), "model_calls": s.get("model_calls") or [],
                "reacquisitions": reacquisitions(self),
                "notes": s.get("notes") or []}

    def public(self) -> dict:
        self.refresh_state()
        ok, blocking = self.gate()
        return {"id": self.id, "created_at": self.created_at, "mode": self.mode, "state": self.state,
                "hooks": dict.fromkeys(("calibrate", "track", "reconstruct"), capture_available()[0]),
                "calibration": self.calibration_status(), "sync": self.sync_status(), "session": self.session,
                "print_check": self.inputs.get("print_check"),
                "synthetic": self.synthetic, "synthetic_capture": self.synthetic, "notes": self.notes,
                "input_evidence": "synthetic_capture" if self.synthetic else "phone_capture",
                "label": ("SYNTHETIC test render: not real footage" if self.synthetic else
                          "Real phone footage (calibrated, reviewed)" if (self.session or {}).get("evidence_kind")
                          == "camera_video_reconstruction" else "Phone footage: not yet verified"),
                "clips": self.clips, "declared_sync": self.declared_sync,
                "inputs": {k: v for k, v in self.inputs.items()}, "missing": self.missing(),
                "runs": self.runs, "stale": self.stale, "summary_lines": self.summary, "report": self.report(),
                "gate_open": ok, "blocking": len(blocking), "effort": self.effort(), "review_log": self.review_log[-50:],
                "proposal": self.proposal, "errors": self.errors[-10:],
                "provenance": {"kind": "offline_pipeline", "model_calls": (self.session or {}).get("model_calls") or [],
                               "label": "No model calls (person-drawn boxes + classical tracking)"},
                "observability": ("single view: positions on the table plane only; heights while lifted are assumed and "
                                  "tilt is not observed unless a tag is visible" if self.mode == "single_view" else
                                  "two views, synchronised by the pipeline from the sync events in both clips")}

    def _cal_source(self) -> Optional[dict]:
        return self.session if self.session is not None else self.calibration_preview

    def calibration_status(self) -> Optional[dict]:
        """{ok, errors, cameras}: accepted only if every camera with a take has an accepted lens and pose."""
        s = self._cal_source()
        if s is None:
            return None
        cams = sorted({PIPELINE_ROLE[r][1] for r in self.clips if r.startswith("take_")})
        if self.mode == "single_view":
            cams = ["top"]
        errors, per = [], {}
        for cam in cams:
            intr = (s.get("intrinsics") or {}).get(cam)
            pose = (s.get("poses") or {}).get(cam)
            ok = bool(intr and intr.get("accepted") and pose and pose.get("accepted"))
            per[cam] = {"lens_accepted": bool(intr and intr.get("accepted")), "pose_accepted": bool(pose and pose.get("accepted")),
                        "rms_px": intr.get("rms_px") if intr else None, "n_views": intr.get("n_views") if intr else None,
                        "pose_rms_px": pose.get("rms_px") if pose else None, "ok": ok}
        for r in s.get("review") or []:
            if r["code"] in ("print_scale_invalid", "intrinsics_missing", "calibration_invalid", "recalibration_required",
                             "pose_invalid", "camera_moved", "no_calibrated_camera"):
                errors.append(r["message"])
        return {"ok": bool(per) and all(v["ok"] for v in per.values()) and not errors, "errors": errors, "cameras": per,
                "source": "full run" if self.session is not None else "calibration step"}

    def sync_status(self) -> Optional[dict]:
        s = self._cal_source()
        y = (s or {}).get("sync")
        if y:
            return {**y, "status": "detected" if y.get("ok") else "refused"}
        if self.declared_sync:
            return self.declared_sync
        return None

    def to_state(self) -> dict:
        return {"calibration_preview": self.calibration_preview, "reviewed": self.reviewed, "id": self.id, "created_at": self.created_at, "mode": self.mode, "notes": self.notes,
                "synthetic": self.synthetic, "clips": self.clips, "declared_sync": self.declared_sync,
                "inputs": self.inputs, "session": self.session, "summary": self.summary, "runs": self.runs,
                "stale": self.stale, "review_log": self.review_log, "proposal": self.proposal, "errors": self.errors,
                "state": self.state}

    @classmethod
    def from_state(cls, d: dict) -> "Capture":
        c = cls(d.get("mode", "two_view"), d.get("notes", ""), d.get("synthetic", False))
        for k in ("id", "created_at", "clips", "declared_sync", "inputs", "session", "summary", "runs", "stale",
                  "review_log", "proposal", "errors", "state", "calibration_preview", "reviewed"):
            if k in d:
                setattr(c, k, d[k])
        if c.state in ("running", "proposing"):
            c.state = "clips_ready"
            c.errors.append("the server restarted while this capture was being processed")
            c.refresh_state()
        return c


# ---------------------------------------------------------------- inputs

def check_print(pc: dict, synthetic: bool = False) -> dict:
    """The person's print measurements, refused with the pipeline's own rule (50 mm bar within
    tolerance, the square consistent with the bar) so a bad print never reaches calibration."""
    from mirror.capture import calibrate as C
    from mirror.capture.target import DEFAULT_TARGET
    pc = {k: pc.get(k) for k in ("measured_bar_mm", "measured_square_mm")}
    pc["measured_by"] = "synthetic_test" if synthetic else "person"
    try:
        obj = C.PrintCheck(**pc)
    except Exception as exc:  # noqa: BLE001
        raise CaptureError(f"print_check: give measured_bar_mm and measured_square_mm in millimetres ({exc.__class__.__name__})")
    failed = [c for c in obj.checks(DEFAULT_TARGET) if c.passed is False]
    if failed:
        raise CaptureError("print check failed: " + "; ".join(f"{c.name} = {c.value} ({c.note})" for c in failed)
                           + ". Reprint at 100% (Actual size) and measure again.")
    return obj.model_dump(mode="json")


def set_inputs(cap: Capture, body: dict) -> None:
    """print_check, objects (proxy mappings), tags, tray: validated with the pipeline's own models."""
    from mirror.capture import events as EV, proxy as PX
    changed = False
    if body.get("synthetic") is True and not cap.synthetic:
        # one-way: rendered test clips are labelled SYNTHETIC from here on; a capture is never un-marked
        cap.synthetic = True
        if cap.inputs.get("print_check"):
            cap.inputs["print_check"]["measured_by"] = "synthetic_test"
        changed = True
    elif body.get("synthetic") is False and cap.synthetic:
        raise CaptureError("a SYNTHETIC capture cannot be relabelled as real footage")
    if body.get("print_check") is not None:
        cap.inputs["print_check"] = check_print(dict(body["print_check"]), cap.synthetic)
        changed = True
    if body.get("objects") is not None:
        objs = list(body["objects"])
        if not objs or len(objs) > MAX_OBJECTS:
            raise CaptureError(f"send 1-{MAX_OBJECTS} objects")
        out = []
        for o in objs:
            o = dict(o)
            o["real_identity"] = str(o.get("real_identity") or o.get("track_label") or "")[:120]
            o["real_dims"] = {k: v for k, v in (o.get("real_dims") or {}).items() if v and v.get("value_m") is not None}
            try:
                out.append(PX.ObjectMapping(**o).model_dump(mode="json"))
            except Exception as exc:  # noqa: BLE001
                raise CaptureError(f"object {o.get('track_label')!r}: {exc}")
        labels = [m["track_label"] for m in out]
        if len(labels) != len(set(labels)):
            raise CaptureError("each object needs its own track label")
        cap.inputs["objects"] = out
        changed = True
    if body.get("tags") is not None:
        try:
            cap.inputs["tags"] = {str(int(k)): str(v) for k, v in dict(body["tags"]).items()}
        except (TypeError, ValueError):
            raise CaptureError("tags map a tag number to an object label")
        changed = True
    if "tray" in body:
        if body["tray"] is None:
            cap.inputs["tray"] = None
        else:
            try:
                cap.inputs["tray"] = EV.TrayRegion(**{**body["tray"], "source": "person_supplied"}).model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001
                raise CaptureError(f"tray: {exc}")
        changed = True
    if changed:
        cap.stale = cap.session is not None or cap.stale


def set_prompts(cap: Capture, prompts: list) -> None:
    from mirror.capture import track as TR
    if not prompts or len(prompts) > MAX_PROMPTS:
        raise CaptureError(f"send 1-{MAX_PROMPTS} prompts")
    cams = {PIPELINE_ROLE[r][1] for r in cap.clips if r.startswith("take_")}
    out = []
    for p in prompts:
        try:
            pr = TR.Prompt(**p)
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"prompt: {exc}")
        if pr.camera not in cams:
            raise CaptureError(f"prompt camera {pr.camera!r} has no take clip (have {sorted(cams)})")
        role = "take_" + pr.camera
        n = int(cap.clips[role].get("frames") or 0)
        if n and not 0 <= pr.frame < n:
            raise CaptureError(f"prompt frame {pr.frame} is outside the {pr.camera} clip (0-{n - 1})")
        if min(pr.box[2], pr.box[3]) < 4:
            raise CaptureError("a box must be at least 4 pixels wide and high")
        out.append(pr.model_dump(mode="json"))
    cap.inputs["prompts"] = out
    cap.stale = cap.session is not None or cap.stale


def add_review(cap: Capture, body: dict) -> list[dict]:
    """Record confirmations and corrections (with time spent). They take effect on the re-run."""
    from mirror.capture import events as EV
    if cap.session is None:
        raise CaptureError("run the capture before reviewing it")
    now = time.time()
    by = "synthetic_test" if cap.synthetic else "person"
    entries = []
    labels = {m["track_label"]: m for m in cap.inputs["objects"]}
    for m in body.get("objects") or []:
        lab = m.get("track_label")
        if lab not in labels:
            raise CaptureError(f"unknown object {lab!r}")
        upd = {k: m[k] for k in ("proxy", "real_identity", "real_dims", "unsupported_features", "assumptions",
                                  "real_contents", "contents_observable", "confirmed_by_person", "notes") if k in m}
        before = {k: labels[lab].get(k) for k in upd}
        merged = {**labels[lab], **upd}
        set_inputs_objects = [merged if o["track_label"] == lab else o for o in cap.inputs["objects"]]
        tmp = Capture(cap.mode, synthetic=cap.synthetic)
        set_inputs(tmp, {"objects": set_inputs_objects})
        cap.inputs["objects"] = tmp.inputs["objects"]
        labels = {o["track_label"]: o for o in cap.inputs["objects"]}
        entries.append({"kind": "mapping", "target": lab, "change": {k: [before.get(k), upd[k]] for k in upd},
                        "seconds_spent": _secs(m), "at": now, "by": by})
    cams = {PIPELINE_ROLE[r][1] for r in cap.clips if r.startswith("take_")}
    expanded = []
    for ic in body.get("identity_confirmations") or []:
        if isinstance(ic, dict) and ic.get("object") and not ic.get("camera"):
            # "same object throughout": confirm the whole track in every take clip
            if ic["object"] not in labels:
                raise CaptureError(f"unknown object {ic['object']!r}")
            for cam in sorted(cams):
                expanded.append({"camera": cam, "label": ic["object"], "from_frame": 0,
                                 "seconds_spent": ic.get("seconds_spent") if cam == sorted(cams)[0] else None})
        else:
            expanded.append(ic)
    for ic in expanded:
        cam, lab, frm = (ic.get("camera"), ic.get("label"), ic.get("from_frame")) if isinstance(ic, dict) else (list(ic) + [None] * 3)[:3]
        if cam not in cams or lab not in labels:
            raise CaptureError("identity confirmation needs a camera with a take clip and a known object label")
        try:
            frm = int(frm)
        except (TypeError, ValueError):
            raise CaptureError("identity confirmation needs from_frame")
        cap.inputs["identity_confirmations"].append([cam, lab, frm])
        entries.append({"kind": "identity", "target": f"{cam}:{lab}@{frm}", "seconds_spent": _secs(ic) if isinstance(ic, dict) else None,
                        "at": now, "by": by})
    known_events = {e.get("id") for e in cap.session.get("events") or []}
    for c in body.get("corrections") or []:
        c = dict(c)
        if c.get("target") not in known_events and not str(c.get("target", "")).startswith("object:"):
            raise CaptureError(f"unknown event {c.get('target')!r}")
        c["by"], c["at"] = by, now
        try:
            corr = EV.Correction(**c)
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"correction: {exc}")
        if len(cap.inputs["corrections"]) >= MAX_CORRECTIONS:
            raise CaptureError("too many corrections on one capture")
        cap.inputs["corrections"].append(corr.model_dump(mode="json"))
        entries.append({"kind": "event_correction", "target": corr.target, "change": [corr.field, corr.before, corr.after],
                        "seconds_spent": corr.seconds_spent, "at": now, "by": by})
    if not entries:
        raise CaptureError("nothing to review: send objects, identity_confirmations or corrections")
    cap.review_log += entries
    cap.stale = True
    return entries


def _secs(d: dict) -> Optional[float]:
    v = d.get("seconds_spent") if isinstance(d, dict) else None
    if v is None:
        return None
    v = float(v)
    if not 0 <= v <= 3600:
        raise CaptureError("seconds_spent is 0-3600")
    return round(v, 1)


def build(cap: Capture, paths: dict[str, str]):
    """Run the offline pipeline on the stored inputs (classical tracking, no model calls)."""
    from mirror.capture import calibrate as C, events as EV, proxy as PX, session as SE, track as TR
    miss = cap.missing()
    if miss:
        raise CaptureError("missing: " + "; ".join(miss))
    take = {PIPELINE_ROLE[r][1]: p for r, p in paths.items() if r.startswith("take_")}
    lens = {PIPELINE_ROLE[r][1]: p for r, p in paths.items() if r.startswith("lens_")}
    if cap.mode == "single_view":
        take = {"top": take["top"]}
    inp = cap.inputs
    s = SE.build_session(
        take=take, lens=lens or None, print_check=C.PrintCheck(**inp["print_check"]),
        prompts=[TR.Prompt(**p) for p in inp["prompts"] if p["camera"] in take],
        mappings=[PX.ObjectMapping(**o) for o in inp["objects"]],
        tags={int(k): v for k, v in (inp.get("tags") or {}).items()} or None,
        tray=EV.TrayRegion(**inp["tray"]) if inp.get("tray") else None, synthetic=cap.synthetic,
        corrections=[EV.Correction(**c) for c in inp.get("corrections") or []],
        identity_confirmations=[tuple(x) for x in inp.get("identity_confirmations") or []], tracker="classical")
    return s


def calibration_summary(cap: Capture) -> dict:
    r = cap.report() or {}
    return {"source": "phone capture", "capture_id": cap.id, "session_id": r.get("session_id"),
            "evidence_kind": r.get("evidence_kind"), "synthetic": cap.synthetic, "mode": r.get("mode"),
            "calibration": r.get("calibration"), "poses": r.get("poses"), "sync": r.get("sync"),
            "effort": cap.effort()}


def calibrate(cap: Capture, paths: dict[str, str]) -> dict:
    """Calibration step before any box is drawn: lens intrinsics, table pose and synchronisation,
    with the pipeline's own functions and thresholds (mirror.capture). Tracking is a separate step."""
    from mirror.capture import calibrate as C, media as M, session as SE, sync as Y
    from mirror.capture.target import DEFAULT_TARGET as spec
    if not cap.inputs.get("print_check"):
        raise CaptureError("enter the print check (measured 50 mm bar and one square) first")
    pc = C.PrintCheck(**cap.inputs["print_check"])
    take = {PIPELINE_ROLE[r][1]: p for r, p in paths.items() if r.startswith("take_")}
    lens = {PIPELINE_ROLE[r][1]: p for r, p in paths.items() if r.startswith("lens_")}
    if cap.mode == "single_view":
        take = {"top": take["top"]}
    out = {"intrinsics": {}, "poses": {}, "recalibration": {}, "sync": None, "review": []}
    metas = {}
    for cam, path in take.items():
        m = M.probe(path)
        metas[cam] = m
        intr = None
        if cam in lens:
            lm = M.probe(lens[cam])
            intr = C.calibrate_intrinsics(SE.lens_detections(lm, spec), spec, pc, lm.fingerprint)
        if intr is None:
            out["review"].append({"code": "intrinsics_missing", "severity": "abstain", "camera": cam,
                                  "message": f"No lens calibration for the {cam} phone. Film the board waved in front of it."})
            continue
        out["intrinsics"][cam] = intr.model_dump(mode="json")
        if not intr.accepted:
            failed = [f"{c.name} = {c.value} (limit {c.threshold})" for c in intr.checks if c.passed is False]
            out["review"].append({"code": "calibration_invalid", "severity": "abstain", "camera": cam,
                                  "message": f"{cam} lens calibration rejected: " + "; ".join(failed)})
        d0 = SE.first_board(m, spec)
        reasons, checks = C.recalibration_reasons(intr, m.fingerprint, d0, spec, pc)
        out["recalibration"][cam] = {"reasons": reasons, "checks": [c.model_dump(mode="json") for c in checks]}
        if reasons:
            out["review"].append({"code": "recalibration_required", "severity": "abstain", "camera": cam,
                                  "message": f"{cam}: " + "; ".join(reasons)})
        if d0 is None or d0.n < 4:
            out["review"].append({"code": "pose_invalid", "severity": "abstain", "camera": cam,
                                  "message": f"The board was not found lying on the table at the start of the {cam} take."})
            continue
        pose = C.table_pose(d0, intr, spec, pc, clip_sha256=m.sha256)
        out["poses"][cam] = pose.model_dump(mode="json")
        if not pose.accepted:
            failed = [f"{c.name} = {c.value} (limit {c.threshold})" for c in pose.checks if c.passed is False]
            out["review"].append({"code": "pose_invalid", "severity": "abstain", "camera": cam,
                                  "message": f"{cam} camera pose on the table rejected: " + "; ".join(failed)})
    if len(metas) >= 2 and "top" in metas:
        other = next(c for c in metas if c != "top")
        evs = {}
        for cam in ("top", other):
            cards, light = Y.scan(metas[cam], spec)
            evs[cam] = Y.card_events(metas[cam], spec, states=cards) + Y.light_events(metas[cam], series=light)
        sync = Y.align(evs["top"], evs[other], top_meta=metas["top"], side_meta=metas[other])
        out["sync"] = sync.model_dump(mode="json")
        if not sync.ok:
            out["review"].append({"code": "sync_refused", "severity": "review",
                                  "message": "Not triangulating: " + "; ".join(sync.reasons) + ". Using single-view positions."})
    return out


EVENT_TYPES = {"grasp": "pick", "pick": "pick", "release": "place", "place": "place", "carry_start": "pick", "carry_end": "place"}


def translate_review(cap: Capture, body: dict) -> dict:
    """Accept the wizard's review shape {correspondence, events} besides the canonical one.

    correspondence -> the person confirms each object and its proxy;
    events -> matched to the pipeline's events by object, kind and order: "confirmed" confirms the
    bracketed moment, "edited" sets its time, "rejected" rejects it, "uncertain" changes nothing.
    Identity confirmations have no equivalent there and must be sent explicitly."""
    out = {k: list(body.get(k) or []) for k in ("objects", "identity_confirmations", "corrections")}
    out["identity_confirmations"] += [{"object": x.get("object") or x.get("track_id"), "seconds_spent": x.get("seconds_spent")}
                                      for x in body.get("identity") or [] if x.get("confirmed", True)]
    labels = {m["track_label"] for m in cap.inputs["objects"]}
    for c in body.get("correspondence") or []:
        lab = str(c.get("track_id"))
        if lab not in labels:
            raise CaptureError(f"unknown object {lab!r}")
        upd = {"track_label": lab, "confirmed_by_person": True, "seconds_spent": c.get("seconds_spent")}
        if c.get("proxy") in ("cup", "block", "marker"):
            upd["proxy"] = c["proxy"]
        if c.get("label"):
            upd["real_identity"] = str(c["label"])[:120]
        if c.get("unsupported_features") is not None:
            upd["unsupported_features"] = list(c["unsupported_features"])[:5]
        out["objects"].append(upd)
    events = (cap.session or {}).get("events") or []
    seen: dict[tuple, int] = {}
    by_id = {x.get("id"): x for x in events}
    for e in body.get("events") or []:
        if e.get("id"):
            ev = by_id.get(e["id"])
            if ev is None:
                raise CaptureError(f"unknown event {e['id']!r}")
        else:
            key = (str(e.get("track_id")), EVENT_TYPES.get(e.get("type"), e.get("type")))
            n = seen.get(key, 0)
            seen[key] = n + 1
            matches = [x for x in events if x.get("object") == key[0] and x.get("type") == key[1]]
            if n >= len(matches):
                raise CaptureError(f"no {key[1]} event number {n + 1} for {key[0]!r} in this capture")
            ev = matches[n]
        st = e.get("status")
        if st == "confirmed" and ev.get("ambiguous") and ev.get("status") != "person_corrected":
            out["corrections"].append({"target": ev["id"], "field": "confirm", "seconds_spent": e.get("seconds_spent")})
        elif st == "edited":
            t = float(e.get("t_start"))
            out["corrections"].append({"target": ev["id"], "field": "t", "before": str(ev.get("t")), "after": str(t),
                                       "seconds_spent": e.get("seconds_spent")})
        elif st == "rejected":
            out["corrections"].append({"target": ev["id"], "field": "reject", "seconds_spent": e.get("seconds_spent")})
    return out


def options(limits_public: Optional[dict] = None) -> dict:
    """What the wizard's forms need: proxy catalogue, clip roles, target geometry and the print rule."""
    from mirror.capture import calibrate as C, proxy as PX
    from mirror.capture.target import DEFAULT_TARGET as T
    ok, why = capture_available()
    return {"available": ok, "reason": why or None, "modes": ["two_view", "single_view"],
            "roles": {"two_view": ["take_top", "take_side", "lens_top", "lens_side"],
                      "single_view": ["take_top", "lens_top"]},
            "role_aliases": ROLE_ALIASES,
            "proxies": {k: {**v, "proxy_dims_m": v.get("proxy_dims_m")} for k, v in PX.SUPPORTED.items()},
            "proxy_choices": ["cup", "block", "marker", "tray", "wall", "ignore"],
            "dimension_sources": ["measured_ruler", "manual_estimate"],
            "target": {"name": T.name, "square_mm": T.square_mm, "scale_bar_mm": T.scale_bar_mm,
                       "squares": [T.squares_x, T.squares_y], "sync_marker_id": T.sync_marker_id,
                       "pdf": ["calibration/mirror_charuco_v1_A4.pdf", "calibration/mirror_charuco_v1_Letter.pdf"]},
            "print_rule": {"bar_tolerance_mm": C.THRESHOLDS["print_bar_tolerance_mm"],
                           "square_tolerance_rel": C.THRESHOLDS["print_square_tolerance_rel"]},
            "review": {"needs_new_input": NEEDS_NEW_INPUT, "how_to_resolve": HOW_TO_RESOLVE, "actions": ACTIONS,
                       "identity_confirmation": "{object} confirms the whole track in every take; {camera, label, "
                                                "from_frame} confirms from one frame of one clip"},
            "limits": limits_public, "model_calls": "none (person-drawn boxes + classical tracking)"}


def reacquisitions(cap: Capture) -> list[dict]:
    """Intervals where a track was lost and found again (identity not yet confirmed), on the reference timeline."""
    out = []
    recon = (cap.session or {}).get("reconstruction") or {}
    for lab, tr in (recon.get("objects") or {}).items():
        t, flags = tr.get("t") or [], tr.get("identity_uncertain") or []
        k = 0
        while k < len(flags):
            if flags[k]:
                j = k
                while j + 1 < len(flags) and flags[j + 1]:
                    j += 1
                out.append({"object": lab, "t_start": t[k] if k < len(t) else None, "t_end": t[j] if j < len(t) else None,
                            "reference_camera": recon.get("reference_camera")})
                k = j + 1
            else:
                k += 1
    return out
