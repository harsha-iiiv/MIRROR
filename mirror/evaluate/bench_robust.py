"""Perception robustness benchmark (pre-registered).

The measured path is ``mirror.robustness.evaluate_robustness``
(the same function its development rehearsal ran): frozen ground-truth frames x
the frozen instructions; muse-image-1.0 edits with the frozen request options;
sam-3.1 under condition A (muse-spark-1.3 chooser per original frame, reused
for its edits) and condition B (fixed colour-free phrases); corrected (primary)
and uncorrected scores; geometry verdicts as defined there ("weakened" is its
own group, not kept). This module adds what a registration needs around it:
request caps enforced on every call, the ledger, spend, the pre-stated
threshold checks and a plain-language report. The function's own full report
is written to ``plan_report/`` next to it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

THRESHOLD_GROUPS = ("A/corrected", "B/corrected")


class BudgetedClient:
    """Wraps a Meta client: charges a budget line before each model request
    and logs it, so every cap holds whatever the caller does."""

    LINES = {"sam_segment": "sam-3.1", "images_edit": "muse-image-1.0", "responses": "muse-spark-1.3:chooser"}

    def __init__(self, client, budget, ledger):
        self._c, self._b, self._l = client, budget, ledger

    def __getattr__(self, name):
        attr = getattr(self._c, name)
        line = self.LINES.get(name)
        if line is None or not callable(attr):
            return attr

        def call(*a, **kw):
            self._b.charge(line)
            return attr(*a, **kw)
        return call


def run(design: dict, inputs: dict, *, ledger, budget, out_dir: Path, client=None, chooser=None,
        evaluate=None, repo_root: Optional[Path] = None) -> dict:
    from . import costs
    from .prereg import BudgetExceeded
    if client is None:
        from mirror.meta import MetaClient
        client = MetaClient.from_env()
    if evaluate is None:
        from mirror.robustness import evaluate_robustness as evaluate
    from mirror.robustness import by_id
    from mirror.robustness.frames import load_frames
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    frames_dir = repo_root / Path(inputs["files"][0]["path"]).parent
    wanted = list(inputs["provenance"]["frames"])
    frames = {g.frame_id: g for g in load_frames(frames_dir)}
    frames = [frames[f] for f in wanted]
    ins = by_id([d["id"] for d in design["instructions"]["instructions"]])
    pc = design["prompt_conditions"]
    conditions = {"A": "chooser", "B": dict(pc["B_fixed"]["prompts"])}
    wrapped = BudgetedClient(client, budget, ledger)
    ledger.append(kind="evaluate_start", frames=wanted, instructions=[i.id for i in ins])
    rep, stopped = None, None
    try:
        kw = dict(client=wrapped, conditions=conditions, uncorrected_conditions=tuple(design["correction"]["uncorrected_for"]),
                  out_dir=Path(out_dir) / "plan_report", edit_budget=design["budget_caps"]["muse-image-1.0"],
                  split=design["frames"]["split"])
        if chooser is not None:
            kw["chooser"] = chooser
        rep = evaluate(frames, ins, **kw)
    except BudgetExceeded as exc:
        stopped = str(exc)
    usage = costs.summarize_usage(list(getattr(client, "usage", []) or []))
    for e in (rep or {}).get("edits", []):
        ledger.append(kind="edit", frame=e.get("frame_id"), edit=e.get("instruction_id"), status=e.get("status"),
                      verdict=(e.get("geometry") or {}).get("verdict"))
    ledger.append(kind="evaluate_end", stopped=stopped, rows=len((rep or {}).get("rows", [])))
    if rep is None:
        return dict(stopped=stopped, usage=usage, cost_usd=costs.total_cost(usage))
    return dict(plan_report="plan_report/report.json", usage=usage, cost_usd=costs.total_cost(usage), stopped=stopped,
                summary=summarize(rep, design), prompts_used=rep.get("prompts_used"), edits=_edits(rep),
                sam=rep.get("sam"), edit_request=rep.get("edit_request"))


def _edits(rep: dict) -> list[dict]:
    out = []
    for e in rep.get("edits", []):
        g = e.get("geometry") or {}
        out.append(dict(frame=e.get("frame_id"), instruction=e.get("instruction_id"), family=e.get("family"),
                        kind=e.get("kind"), status=e.get("status"), error=e.get("error"),
                        verdict=(g.get("verdict") or e.get("geometry_by_construction")) if e.get("kind") == "model"
                        else "kept by construction" + (f" (check said {g.get('verdict')}, not used)"
                                                       if g.get("verdict") and g.get("verdict") != "preserved" else ""),
                        objects={k: v.get("verdict") for k, v in (g.get("objects") or {}).items()},
                        correction=g.get("registration"), latency_s=e.get("latency_s")))
    return out


def summarize(rep: dict, design: dict) -> dict:
    th = design["thresholds"]
    s = {k: v for k, v in rep["summary"].items() if not k.startswith("_")}
    checks = {}
    for grp in THRESHOLD_GROUPS:
        a = (s.get(grp) or {}).get("all_edits") or {}
        d, f, c = a.get("kept_iou_delta_median"), a.get("kept_found_rate"), a.get("kept_centroid_err_px_median")
        checks[grp] = {
            f"median IoU change >= {th['delta_iou_median_min']} (geometry kept)": d is not None and d >= th["delta_iou_median_min"],
            f"found rate >= {th['found_rate_min']} (geometry kept)": f is not None and f >= th["found_rate_min"],
            f"median centroid error <= {th['centroid_px_median_max']} px (geometry kept)": c is not None and c <= th["centroid_px_median_max"],
        }
    edits = rep.get("edits", [])
    return dict(groups=s, threshold_checks=checks, n_edits=len(edits),
                edit_failures=sum(1 for e in edits if e.get("status") not in (None, "ok")),
                chooser_missing={f: [k for k, v in (p.get("prompts") or {}).items() if not v]
                                 for f, p in (rep.get("prompts_used") or {}).get("A", {}).items()})


def _r(x, nd=3):
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, (int, float)) else str(x))


def markdown(report: dict, design: dict) -> str:
    from . import costs
    res = report.get("result", {})
    L = [f"# {report.get('title', 'Perception robustness benchmark (pre-registered)')}", ""]
    if report.get("rehearsal"):
        L += ["> REHEARSAL on development frames and a subset of instructions. Not an evaluation result.", ""]
    n_ins = len(design["instructions"]["instructions"])
    L += [f"Status: {report['status']}. Design `{report['design_sha256'][:16]}`, inputs `{report['inputs_sha256'][:16]}`, "
          f"code `{report['freeze_code_sha256'][:16]}`. {design['frames']['count']} frames x {n_ins} instructions, "
          "two prompt conditions.", ""]
    if "summary" not in res:
        return "\n".join(L + [f"Stopped: {res.get('stopped', '')}", ""])
    s = res["summary"]
    L += ["## What was measured", "",
          "Each ground-truth frame was edited once per instruction: by the image model (lighting, background, "
          "distractors, material) or by local code (camera noise and blur). SAM 3.1 segmented the cup, block and "
          "marker in the original and every edited frame, with phrases a model chose on the original frame "
          "(condition A) and with fixed colour-free phrases (condition B), and every mask was compared with the "
          "simulator's true mask. The image model shifts and rescales the whole frame slightly; that is measured "
          "per edit and undone for the corrected (primary) numbers. A geometry check decides per object whether "
          "an edit kept it where it was; objects that moved, resized, weakened or vanished are reported separately, "
          "never dropped.", "", design.get("dev_run_disclosure", ""), ""]
    if res.get("stopped"):
        L += [f"**Stopped early.** {res['stopped']}.", ""]
    for grp, g in s["groups"].items():
        L += [f"## {grp}", "", "| edits | objects | found | kept objects | kept: found | kept: median IoU change | "
              "kept: median centroid error | weakened | changed |", "|---|---|---|---|---|---|---|---|---|"]
        for fam, v in g.items():
            L.append(f"| {fam} | {v.get('n')} | {_r(v.get('found_rate'))} | {v.get('n_geometry_kept')} | "
                     f"{_r(v.get('kept_found_rate'))} | {_r(v.get('kept_iou_delta_median'))} | "
                     f"{_r(v.get('kept_centroid_err_px_median'), 2)} px | {v.get('n_geometry_weakened')} | "
                     f"{v.get('n_geometry_changed')} |")
        if grp in s["threshold_checks"]:
            L += ["", "Pre-stated checks (all edits, geometry kept):", ""]
            L += [f"- {k}: {'met' if ok else 'not met'}" for k, ok in s["threshold_checks"][grp].items()]
        L.append("")
    L += ["## Chosen phrases (condition A)", "", "| frame | cup | block | marker |", "|---|---|---|---|"]
    for fid, p in ((res.get("prompts_used") or {}).get("A") or {}).items():
        q = p.get("prompts") or {}
        L.append(f"| {fid} | {q.get('cup') or '(none)'} | {q.get('block') or '(none)'} | {q.get('marker') or '(none)'} |")
    L += ["", f"Fixed phrases (condition B): {json.dumps(design['prompt_conditions']['B_fixed']['prompts'])}.", "",
          "## Spend", "", "| model | network calls | replayed | median latency | cost |", "|---|---|---|---|---|"]
    for m, u in res.get("usage", {}).items():
        lat = "n/a" if u["latency_s_median"] is None else f"{u['latency_s_median']:.1f} s"
        L.append(f"| {m} | {u['network_calls']} | {u['replayed_calls']} | {lat} | ${u['cost_usd']:.3f} |")
    L += ["", f"Total ${res.get('cost_usd', 0):.3f} at list prices ({costs.PRICES_SOURCE}).", "",
          "## Every edit", "", "| frame | instruction | applied by | status | geometry | objects | correction (scale, dx, dy) |",
          "|---|---|---|---|---|---|---|"]
    for e in res.get("edits", []):
        c = e.get("correction") or {}
        corr = f"{c.get('scale', 1):.3f}, {c.get('dx')}, {c.get('dy')}" if c.get("applied") else "-"
        objs = ", ".join(f"{k} {v}" for k, v in (e.get("objects") or {}).items()) or "-"
        st = e.get("status") or ""
        if e.get("error"):
            st += f" ({str(e['error'])[:80]})"
        L.append(f"| {e['frame']} | {e['instruction']} | {'image model' if e.get('kind') == 'model' else 'local code'} | "
                 f"{st} | {e.get('verdict')} | {objs} | {corr} |")
    L += ["", f"Edit failures: {s['edit_failures']} of {s['n_edits']}. Every SAM request with its prompt, match count, IoU "
          f"and centroid error is in {res.get('plan_report')} (rows). Budget lines: {json.dumps(report['budget'])}. "
          f"Every attempt is in ledger.jsonl ({report['ledger_entries']} entries).", ""]
    return "\n".join(L)
