"""Consented trial recording (M2 section 4) and the opt-in study event log.

Two outputs, both written only for a consented study session:

* trial records, schema ``mirror-trial/1`` (one JSON object per attempt: a submitted
  correction or a saved manual edit), under MIRROR_TRIAL_STORE, one directory per
  context so development smoke tests, usability-pilot sessions and frozen benchmark
  runs never mix:  <store>/<context>/<trial_id>.json
* the event log, schema ``mirror-study-events/1``, appended to MIRROR_STUDY_LOG.

Nothing is written for a session that has not started study mode with
``consent.recorded`` true. Participant ids are anonymous (``P01``..``P99`` or ``DEV``);
no names, IP addresses or user agents are recorded. Correction text is kept only
with ``consent.text``. Audio is not stored by default; with ``consent.audio`` it is
kept only when MIRROR_STUDY_AUDIO_RETENTION_DAYS > 0, and ``purge_expired_audio``
deletes it after that many days.

Export:  python -m mirror.studio.trials export --store <dir> --out <dir> [--context usability_pilot]
writes trials.jsonl, trials.csv (text columns only with --keep-text) and summary.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

TRIAL_FORMAT = "mirror-trial/1"
EVENT_SCHEMA = "mirror-study-events/1"
CONTEXTS = ("dev_smoke", "usability_pilot", "frozen_benchmark")
STUDIO_CONTEXTS = ("dev_smoke", "usability_pilot")      # frozen benchmarks never run through the studio
ORIGINS = ("prepared", "developer_written", "independently_authored")
CONDITIONS = ("mirror", "manual")
PARTICIPANT_RE = re.compile(r"^(P\d\d|DEV)$")
TASK_RE = re.compile(r"^[A-Z]{1,3}\d{1,3}$")


def semantic_state(spec) -> Optional[dict]:
    """The protocol's semantic state of a specification (None when that module is absent)."""
    if spec is None:
        return None
    try:
        from ..evaluate.protocol_v3.edits import semantic_state as st
    except Exception:  # noqa: BLE001 - optional module (evaluation protocol v3)
        return None
    try:
        return st(spec)
    except Exception:  # noqa: BLE001
        return None


def code_commit() -> str:
    v = os.environ.get("MIRROR_CODE_VERSION")
    if v:
        return v
    try:
        import subprocess
        root = Path(__file__).resolve().parents[2]
        return subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=5, stdin=subprocess.DEVNULL).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


class StudyError(ValueError):
    pass


def check_config(cfg: dict) -> dict:
    """Validate and normalise a study-mode request from the facilitator."""
    p = str(cfg.get("participant") or "")
    if not PARTICIPANT_RE.match(p):
        raise StudyError("participant must be an anonymous id P01-P99, or DEV for developer smoke tests")
    ctx = cfg.get("context") or ("dev_smoke" if p == "DEV" else "usability_pilot")
    if ctx not in STUDIO_CONTEXTS:
        raise StudyError(f"context must be one of {list(STUDIO_CONTEXTS)} (frozen benchmarks do not run in the studio)")
    if ctx == "dev_smoke" and p != "DEV":
        raise StudyError("developer smoke tests use participant DEV")
    if ctx == "usability_pilot" and p == "DEV":
        raise StudyError("usability-pilot sessions need an anonymous participant id (P01-P99)")
    origin = cfg.get("task_origin") or ("developer_written" if p == "DEV" else "prepared")
    if origin not in ORIGINS:
        raise StudyError(f"task_origin must be one of {list(ORIGINS)}")
    cond = cfg.get("condition") or "mirror"
    if cond not in CONDITIONS:
        raise StudyError(f"condition must be one of {list(CONDITIONS)}")
    task = cfg.get("task")
    if task is not None and not TASK_RE.match(str(task)):
        raise StudyError("task is a task-card id such as C1 or T3")
    consent = cfg.get("consent") or {}
    consent = {k: bool(consent.get(k)) for k in ("recorded", "text", "audio", "quote")}
    if not consent["recorded"]:
        raise StudyError("study mode records nothing without consent.recorded = true")
    return {"participant": p, "context": ctx, "task_origin": origin, "condition": cond, "task": task,
            "consent": consent, "synthetic": False}


class StudyRecorder:
    """Per-session study mode: attempts, trial files and the event log."""

    def __init__(self, cfg: dict, store: Optional[Path], event_log: Optional[Path]):
        self.cfg = check_config(cfg)
        self.store = Path(store) if store else None
        self.event_log = Path(event_log) if event_log else None
        self.session = "s" + secrets.token_hex(6)       # random; not the studio session id
        self.t0 = time.monotonic()
        self.task_started_at = 0.0
        self.seq = 0
        self.attempts: dict[str, dict] = {}            # trial_id -> record
        self.by_challenge: dict[str, str] = {}         # challenge id -> open trial id
        self.counts: dict[tuple, int] = {}
        self._lock = threading.Lock()
        self.retention_days = float(os.environ.get("MIRROR_STUDY_AUDIO_RETENTION_DAYS") or 0)

    # ------------------------------------------------------------ basics
    def now(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    @property
    def keeps_audio(self) -> bool:
        return self.cfg["consent"]["audio"] and self.retention_days > 0 and self.store is not None

    def public(self) -> dict:
        return {**self.cfg, "session": self.session, "recording_trials": self.store is not None,
                "event_log": self.event_log is not None, "audio_stored": self.keeps_audio,
                "audio_retention_days": self.retention_days if self.keeps_audio else 0,
                "attempts": len(self.attempts)}

    def event(self, name: str, **extra) -> None:
        if self.event_log is None:
            return
        with self._lock:
            self.seq += 1
            rec = {"schema": EVENT_SCHEMA, "synthetic": False, "participant": self.cfg["participant"],
                   "session": self.session, "seq": self.seq, "t": self.now(), "event": name,
                   "task": self.cfg["task"], "condition": self.cfg["condition"], **extra}
            self.event_log.parent.mkdir(parents=True, exist_ok=True)
            with self.event_log.open("a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")

    def set_task(self, task: Optional[str], task_origin: Optional[str] = None, condition: Optional[str] = None,
                 spec_version_id: Optional[str] = None, spec=None, scene_key: Optional[str] = None) -> None:
        cfg = check_config({**self.cfg, "task": task, "task_origin": task_origin or self.cfg["task_origin"],
                            "condition": condition or self.cfg["condition"]})
        self.cfg.update({k: cfg[k] for k in ("task", "task_origin", "condition")})
        self.task_started_at = self.now()
        self.event("task_start", scene_key=scene_key, spec_version_id=spec_version_id, spec_state=semantic_state(spec))

    # ------------------------------------------------------------ attempts
    def _write(self, rec: dict) -> None:
        if self.store is None:
            return
        d = self.store / rec["context"]
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{rec['trial_id']}.tmp"
        tmp.write_text(json.dumps(rec, indent=1, default=str))
        tmp.replace(d / f"{rec['trial_id']}.json")

    def start_attempt(self, *, versions: dict, scene: dict, lineage: dict, input_: dict, text: Optional[str],
                      spec_before, challenge_id: Optional[str] = None) -> dict:
        if challenge_id and challenge_id in self.by_challenge:
            self.finish(self.by_challenge[challenge_id])
        key = (self.cfg["participant"], self.cfg["task"], self.cfg["condition"])
        self.counts[key] = self.counts.get(key, 0) + 1
        tid = "t" + secrets.token_hex(8)
        inp = {k: v for k, v in input_.items() if v is not None}
        inp["chars"] = len(text or "")
        if text is not None and self.cfg["consent"]["text"]:
            inp["text"] = text
        st = semantic_state(spec_before)
        rec = {"format": TRIAL_FORMAT, "trial_id": tid, "participant": self.cfg["participant"], "synthetic": False,
               "context": self.cfg["context"], "task_origin": self.cfg["task_origin"], "task": self.cfg["task"],
               "condition": self.cfg["condition"], "attempt": self.counts[key], "started_at": self.now(),
               "ended_at": None, "task_started_at": self.task_started_at, "consent": dict(self.cfg["consent"]),
               "versions": versions, "scene": scene, "lineage": lineage, "input": inp,
               "proposal": None, "clarification": None, "review": {"accepted": False, "edited_before_accept": False},
               "spec_state_before": st, "spec_state_after": st, "recheck": None, "execution": None,
               "failure_reason": None, "comprehension": None, "audio": None}
        with self._lock:
            self.attempts[tid] = rec
            if challenge_id:
                self.by_challenge[challenge_id] = tid
        self._write(rec)
        self.event("correction_submitted" if self.cfg["condition"] == "mirror" else "manual_edit_started",
                   input="speech" if inp.get("kind", "").startswith("spoken") else "text", chars=inp["chars"],
                   **({"text": text} if text is not None and self.cfg["consent"]["text"] else {}))
        return rec

    def update(self, tid: Optional[str], **fields) -> Optional[dict]:
        if not tid or tid not in self.attempts:
            return None
        rec = self.attempts[tid]
        for k, v in fields.items():
            if isinstance(v, dict) and isinstance(rec.get(k), dict):
                rec[k] = {**rec[k], **v}
            else:
                rec[k] = v
        self._write(rec)
        return rec

    def trial_for(self, challenge_id: str) -> Optional[str]:
        return self.by_challenge.get(challenge_id)

    def finish(self, tid: Optional[str], failure_reason: Optional[str] = None) -> None:
        rec = self.attempts.get(tid or "")
        if rec is None or rec["ended_at"] is not None:
            return
        rec["ended_at"] = self.now()
        if failure_reason and not rec["failure_reason"]:
            rec["failure_reason"] = failure_reason
        self._write(rec)
        self.by_challenge = {c: t for c, t in self.by_challenge.items() if t != tid}

    def keep_audio(self, tid: str, wav_path: Path) -> Optional[dict]:
        """Copy a correction's audio into the store under the retention policy (consent only)."""
        if not self.keeps_audio or not wav_path or not Path(wav_path).exists():
            return None
        d = self.store / self.cfg["context"] / "audio"
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"{tid}.wav"
        shutil.copyfile(wav_path, dst)
        meta = {"file": f"audio/{tid}.wav", "delete_after": time.time() + self.retention_days * 86400,
                "retention_days": self.retention_days}
        self.update(tid, audio=meta)
        return meta

    def close(self) -> None:
        for tid in list(self.attempts):
            self.finish(tid, failure_reason="session ended before the attempt was completed"
                        if self.attempts[tid]["proposal"] is None else None)
        self.event("session_end")


# ------------------------------------------------------------------ export

def load_trials(store: Path, context: Optional[str] = None) -> list[dict]:
    out = []
    for ctx in ([context] if context else CONTEXTS):
        d = Path(store) / ctx
        if not d.exists():
            continue
        for f in sorted(d.glob("t*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except ValueError:
                continue
    out.sort(key=lambda r: (r["context"], r["participant"], r.get("task") or "", r["attempt"], r["started_at"]))
    return out


def _flat(d: Any, prefix: str = "") -> dict:
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flat(v, f"{prefix}{k}."))
    elif isinstance(d, list):
        out[prefix[:-1]] = json.dumps(d, default=str)
    else:
        out[prefix[:-1]] = d
    return out


TEXT_COLUMNS = ("input.text", "proposal.question", "proposal.rejected", "clarification.question")


def export(store: Path, out: Path, context: Optional[str] = None, keep_text: bool = False) -> dict:
    """trials.jsonl + trials.csv + summary.md for one context (or all, each labelled)."""
    recs = load_trials(store, context)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "trials.jsonl").open("w") as fh:
        for r in recs:
            fh.write(json.dumps(r, default=str) + "\n")
    rows = [_flat(r) for r in recs]
    if not keep_text:
        rows = [{k: v for k, v in row.items() if k not in TEXT_COLUMNS} for row in rows]
    cols = sorted({k for row in rows for k in row})
    with (out / "trials.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    (out / "summary.md").write_text(summary(recs))
    return {"trials": len(recs), "files": ["trials.jsonl", "trials.csv", "summary.md"]}


def summary(recs: list[dict]) -> str:
    lines = ["# Trial summary", "",
             "Contexts are reported separately: developer smoke tests are not user evidence, and a small "
             "usability pilot is preliminary usability evidence only.", ""]
    for ctx in CONTEXTS:
        rs = [r for r in recs if r["context"] == ctx]
        if not rs:
            continue
        lines += [f"## {ctx} ({len(rs)} attempts)", "",
                  "| participant | task | condition | attempt | origin | outcome | provenance | accepted | "
                  "recheck | execution | latency s | failure |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rs:
            pr = r.get("proposal") or {}
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                r["participant"], r.get("task") or "-", r["condition"], r["attempt"], r["task_origin"],
                pr.get("outcome", "-"), pr.get("provenance", "-"), "yes" if r["review"].get("accepted") else "no",
                (r.get("recheck") or {}).get("status", "-"), (r.get("execution") or {}).get("status", "-"),
                pr.get("latency_s", "-"), r.get("failure_reason") or "-"))
        fails = [r for r in rs if r.get("failure_reason") or (r.get("proposal") or {}).get("outcome") == "error"]
        lines += ["", f"Failures and unfinished attempts: {len(fails)}", ""]
        for r in fails:
            lines.append(f"- {r['trial_id']} ({r['participant']}, {r.get('task')}): "
                         f"{r.get('failure_reason') or (r.get('proposal') or {}).get('error')}")
        lines.append("")
    if len(lines) <= 4:
        lines.append("No trials recorded.")
    return "\n".join(lines) + "\n"


def purge_expired_audio(store: Path, now: Optional[float] = None) -> list[str]:
    now = time.time() if now is None else now
    gone = []
    for rec in load_trials(store):
        a = rec.get("audio") or {}
        if a.get("file") and a.get("delete_after", 0) < now:
            p = Path(store) / rec["context"] / a["file"]
            p.unlink(missing_ok=True)
            rec["audio"] = {**a, "deleted_at": now}
            (Path(store) / rec["context"] / f"{rec['trial_id']}.json").write_text(json.dumps(rec, indent=1))
            gone.append(rec["trial_id"])
    return gone


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mirror.studio.trials")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--store", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--context", choices=CONTEXTS)
    e.add_argument("--keep-text", action="store_true")
    p = sub.add_parser("purge-audio")
    p.add_argument("--store", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "export":
        print(json.dumps(export(Path(a.store), Path(a.out), a.context, a.keep_text)))
    else:
        print(json.dumps({"deleted": purge_expired_audio(Path(a.store))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
