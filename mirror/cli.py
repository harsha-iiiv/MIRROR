"""`mirror` command line.

  mirror demo        summary of the reference demonstration (or regenerate it)
  mirror interpret   proposed task specification for a demonstration
  mirror run         plan, execute and check one scene
  mirror story       the full reference narrative in one command
  mirror eval        evaluation runs (delegates to scripts/run_eval.py)
  mirror studio      launch the studio web app
  mirror doctor      check the installation and model configuration

The interpreter follows MIRROR_LLM_MODE when it is set. Otherwise recorded
model responses are replayed when they match the demonstration, and the
offline rules are used when they do not. Every command that interprets says
which interpreter it used.
"""
from __future__ import annotations

import argparse
import gzip
import importlib
import importlib.util
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
DEMO_PATH = REPO / "data" / "demos" / "reference_demo.json"
RUN_EVAL = REPO / "scripts" / "run_eval.py"
REVIEW_CONFIRM_RULES = ("c_upright_final_cup",)
REFERENCE_CORRECTION = ("This container must remain upright throughout transport, "
                        "not just at the destination.")


# ----------------------------------------------------------------- output ---

class Out:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def __call__(self, text: str = "") -> None:
        print(text, file=self.stream)

    def heading(self, text: str) -> None:
        self()
        self(text)
        self("-" * len(text))


def table(rows: Sequence[Sequence[Any]], headers: Sequence[str], indent: str = "  ") -> str:
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = []
    for n, r in enumerate(cells):
        lines.append(indent + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
        if n == 0:
            lines.append(indent + "  ".join("-" * w for w in widths))
    return "\n".join(lines)


# ------------------------------------------------------------ data loading ---

def load_demo(path: Optional[str | Path] = None):
    from mirror.contracts import DemoTrace
    p = Path(path) if path else DEMO_PATH
    if not p.exists():
        raise SystemExit(f"demonstration not found: {p} (run `mirror demo --regenerate`)")
    raw = p.read_bytes()
    if p.suffix == ".gz":
        raw = gzip.decompress(raw)
    return DemoTrace.model_validate_json(raw)


def load_spec(path: str | Path, version: Optional[int] = None):
    from mirror.contracts import TaskSpec
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):  # specs.json from a session export
        specs = [TaskSpec.model_validate(d) for d in data]
        if not specs:
            raise SystemExit(f"{path} holds no specifications")
        if version is None:
            return specs[-1]
        for s in specs:
            if s.version == version:
                return s
        raise SystemExit(f"{path} has no specification version {version}")
    return TaskSpec.model_validate(data)


def make_session(trace, out: Out):
    from mirror.interpret import describe_client, resolve_client
    from mirror.pipeline import Session
    client, reason = resolve_client(trace)
    session = Session(client=client)
    session.demo = trace
    session.scene = trace.scene
    out(f"Interpreter: {describe_client(client)}  [{reason}]")
    return session


def with_fallback(session, step: Callable[[], Any], out: Out):
    """Run an interpretation step; if replay has no recording for it, switch
    to the offline rules for the rest of the session and say so."""
    from mirror.interpret import CassetteMiss, LLMClient
    try:
        return step()
    except CassetteMiss:
        session.client = LLMClient(mode="offline")
        out("  (no recording for this request; switching to the offline rules)")
        return step()


def review(session, out: Out):
    spec = session.spec
    confirm = [g.id for g in spec.goals] + [c.id for c in spec.constraints if c.id in REVIEW_CONFIRM_RULES]
    new = session.confirm(confirm=confirm)
    out(f"Confirmed: {', '.join(confirm)}  -> spec v{new.version}")
    return new


# --------------------------------------------------------------- printing ---

def print_spec(spec, out: Out) -> None:
    out(f"Spec {spec.spec_id} v{spec.version}   interpreter: {spec.interpreter}")
    if spec.summary:
        out(f"Summary: {spec.summary}")
    out()
    out("Goals")
    out(table([(g.id, f"{g.object.kind or g.object.role} inside {g.target}", g.status.value)
               for g in spec.goals], ["id", "goal", "status"]))
    if spec.constraints:
        out()
        out("Rules")
        out(table([(c.id, c.description or c.type, c.status.value) for c in spec.constraints],
                  ["id", "rule", "status"]))
    if spec.ordering:
        out()
        out("Ordering")
        out(table([(o.id, f"{o.before.kind} before {o.after.kind}", o.status.value) for o in spec.ordering],
                  ["id", "order", "status"]))
    open_q = [u for u in spec.uncertainties if u.status == "open"]
    if open_q:
        out()
        out("Open questions")
        for u in open_q:
            opts = f"  [{' / '.join(u.options)}]" if u.options else ""
            out(f"  {u.related_constraint or u.id}: {u.question}{opts}")
    out()
    out(f"Enforced now: {', '.join(c.id for c in spec.enforced_constraints()) or 'none'}")


def print_summary(summary, out: Out) -> None:
    out(f"Demonstration {summary.demo_id}  ({summary.source}, {summary.duration_s:g} s, "
        f"events from {summary.event_source})")
    out(table([(o.object_id, o.kind, ", ".join(f"{a:g}-{b:g}" for a, b in zip(o.grasp_times, o.release_times)) or "-",
                o.ended_in_tray or "-", o.carry_max_tilt_deg, o.end_tilt_deg,
                ", ".join(c.split(":", 1)[1] for c in o.wall_crossings) or "-",
                o.min_obstacle_distance_m)
               for o in summary.objects],
              ["object", "kind", "carried (s)", "ended in", "max tilt carried", "final tilt", "wall", "min dist (m)"]))
    out(f"Pick order: {', '.join(summary.order) or '-'}")
    out("Regularities:")
    for r in summary.regularities:
        out(f"  {r.id:>4}  {r.statement}")
    for n in summary.notes:
        out(f"  note: {n}")


def print_rollout(entry, out: Out) -> None:
    rep = entry.rollout.report
    out(f"Rollout {rep.rollout_id}: scene {rep.scene_id}, spec v{rep.spec_version} -> {rep.status.upper()}")
    if rep.status == "planning_failed":
        out(f"  planner: {rep.plan_status}: {rep.plan_reason}")
        cert = entry.plan.infeasibility
        if cert:
            proven = bool(cert.get("proven"))
            out(f"  infeasibility {'PROVEN' if proven else 'not proven (planner found no plan)'}"
                + (f" [{cert['kind']}]" if cert.get("kind") else ""))
            if cert.get("statement"):
                for line in textwrap.wrap(str(cert["statement"]), 96):
                    out(f"    {line}")
        else:
            out("  no infeasibility proof: the planner found no plan, which is not a proof")
        return
    for r in rep.route_labels:
        out(f"  route  {r}")
    if rep.outcomes:
        out(table([(o.object_id, "yes" if o.in_target else "no", f"{o.max_transport_tilt_deg:.1f}",
                    f"{o.final_tilt_deg:.1f}",
                    f"{o.beads_in_container}/{o.beads_total}" if o.beads_total else "-")
                   for o in rep.outcomes],
                  ["object", "in tray", "max tilt carried", "final tilt", "beads kept"]))
    for v in rep.violations:
        out(f"  violation  {v.constraint_id} on {v.object_id}: {v.t_start:.2f}-{v.t_end:.2f} s, "
            f"peak {v.peak:.1f}")
    for c in rep.forbidden_contacts[:5]:
        out(f"  contact  {json.dumps(c, default=str)[:160]}")
    for n in rep.notes:
        out(f"  note  {n}")
    out(f"  planning {rep.planning_time_s:.2f} s, simulated {rep.execution_time_s:.1f} s, "
        f"wall {rep.wall_time_s:.1f} s")


def print_recheck(rep, out: Out) -> None:
    out(f"Re-check {rep.rollout_id}: {rep.status.upper()}")
    for v in rep.violations:
        out(f"  violation  {v.constraint_id} on {v.object_id}: {v.t_start:.2f}-{v.t_end:.2f} s, "
            f"peak {v.peak:.1f}")
    if not rep.violations:
        out("  no violations")


# --------------------------------------------------------------- commands ---

def cmd_demo(args, out: Out) -> int:
    from mirror.interpret import summarize_demo
    if args.regenerate:
        from mirror.scenes import reference_scene
        from mirror.sim import record_scripted_demo
        target = Path(args.out) if args.out else DEMO_PATH
        t0 = time.time()
        trace = record_scripted_demo(reference_scene("demo"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(trace.model_dump_json())
        out(f"Recorded {trace.demo_id} in {time.time() - t0:.1f} s -> {target}")
    else:
        trace = load_demo(args.demo)
    summary = summarize_demo(trace)
    if args.json:
        out(summary.model_dump_json(indent=1))
    else:
        print_summary(summary, out)
    return 0


def cmd_interpret(args, out: Out) -> int:
    trace = load_demo(args.demo)
    info = out if not args.json else Out(sys.stderr)
    session = make_session(trace, info)
    frames = None
    if args.keyframes_dir:
        from mirror.interpret.keyframes import load_keyframes
        frames = load_keyframes(Path(args.keyframes_dir))
    elif args.keyframes:
        from mirror.interpret.keyframes import render_keyframes
        frames = render_keyframes(trace, n=args.keyframes)
    if frames:
        info(f"Attached {len(frames)} rendered simulator frames (not real video): "
             + ", ".join(f"t={k.t:g} s {k.label}" for k in frames))
        if session.client.offline:
            info("  (the offline rules do not use images)")

        def step():
            from mirror.interpret import propose_spec
            spec = propose_spec(trace, session.client, keyframes=frames)
            session.specs = [spec]
            return spec
        spec = with_fallback(session, step, info)
    else:
        spec = with_fallback(session, session.interpret, info)
    if args.json:
        out(spec.model_dump_json(indent=1))
    else:
        out()
        print_spec(spec, out)
    return 0


def _spec_for_run(args, out: Out):
    trace = load_demo(args.demo)
    session = make_session(trace, out)
    if args.spec:
        spec = load_spec(args.spec, args.spec_version)
        session.specs = [spec]
        out(f"Spec from {args.spec}: v{spec.version} ({spec.interpreter})")
    else:
        with_fallback(session, session.interpret, out)
        review(session, out)
    return session


def cmd_run(args, out: Out) -> int:
    session = _spec_for_run(args, out)
    session.set_scene(args.scene)
    entry = session.run(condition=args.scene, frame_hz=args.frame_hz)
    if args.json:
        out(entry.rollout.report.model_dump_json(indent=1))
    else:
        print_rollout(entry, out)
    if args.out:
        out(f"Session exported to {session.export(args.out)}")
    return 0 if entry.rollout.report.status in ("success", "constraint_violated", "planning_failed") else 1


def cmd_story(args, out: Out) -> int:
    from mirror.interpret import NeedsClarification, spec_diff
    t_start = time.time()
    trace = load_demo(args.demo)
    out("MIRROR reference story")
    out("======================")
    session = make_session(trace, out)

    out.heading("1. Watch me: the demonstration")
    from mirror.interpret import summarize_demo
    print_summary(summarize_demo(trace), out)

    out.heading("2. What MIRROR thinks matters (proposal)")
    spec = with_fallback(session, session.interpret, out)
    print_spec(spec, out)

    out.heading("3. Review: the user confirms the goals and that the cup ends upright")
    review(session, out)

    out.heading("4. Now your turn: run in the demonstration scene")
    session.set_scene("demo")
    print_rollout(session.run(condition="demo", frame_hz=args.frame_hz), out)

    out.heading("5. Change the world: tray moved, wall has a low slot")
    session.set_scene("shortcut")
    original = session.run(condition="original", frame_hz=args.frame_hz)
    print_rollout(original, out)
    original_id = original.rollout.report.rollout_id
    rep = original.rollout.report
    enforced = {c.id for c in session.spec.enforced_constraints()}
    for o in rep.outcomes:
        lost = o.beads_total - o.beads_in_container
        if o.beads_total and (lost > 0 or o.max_transport_tilt_deg > 15):
            out(f"  Under spec v{rep.spec_version} this is a {rep.status}: no enforced rule covers how the "
                f"{o.object_id} is carried (enforced: {', '.join(sorted(enforced)) or 'none'}). "
                f"It tilted to {o.max_transport_tilt_deg:.0f} degrees and {lost} of {o.beads_total} beads fell out.")

    out.heading("6. You misunderstood this: the correction")
    out(f'User: "{args.correction}"')
    before = session.spec
    try:
        new, record = with_fallback(session, lambda: session.correct(args.correction), out)
    except NeedsClarification as q:
        out(f"MIRROR asks: {q.question}")
        out("Story stopped: answer the question with a clearer correction (--correction).")
        return 1
    out(f"Compiled by: {record.model}  -> spec v{new.version}")
    for d in spec_diff(before, new):
        if d["change"] == "added":
            out(f"  + {d['id']}: {d['after'].get('description') or d['after'].get('question', '')}")
        elif d["change"] == "removed":
            out(f"  - {d['id']}")
        else:
            out(f"  ~ {d['id']}.{d['change']}: {json.dumps(d['before'])} -> {json.dumps(d['after'])}")

    out.heading("7. Run again in the changed world under the corrected spec")
    corrected = session.run(condition="corrected", frame_hz=args.frame_hz)
    print_rollout(corrected, out)

    out.heading(f"8. The old rollout ({original_id}) re-checked under spec v{new.version}")
    try:
        print_recheck(session.recheck(original_id), out)
    except ValueError as e:
        out(f"  cannot re-check: {e}")

    out.heading("9. Blocked: the slot is the only way through")
    session.set_scene("blocked")
    print_rollout(session.run(condition="blocked", frame_hz=args.frame_hz), out)

    out_dir = Path(args.out) if args.out else REPO / "results" / "tmp" / f"story_{session.session_id}"
    session.export(out_dir)
    out()
    out(f"Session exported to {out_dir}")
    out(f"Done in {time.time() - t_start:.0f} s.")
    return 0


def cmd_eval(args, out: Out) -> int:
    if not RUN_EVAL.exists():
        raise SystemExit(f"evaluation script not found: {RUN_EVAL}")
    spec = importlib.util.spec_from_file_location("mirror_run_eval", RUN_EVAL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    argv = list(args.eval_args)
    if argv and argv[0] == "--":
        argv = argv[1:]
    return int(mod.main(argv) or 0)


def cmd_studio(args, out: Out) -> int:
    import uvicorn
    out(f"MIRROR studio on http://{args.host}:{args.port}")
    uvicorn.run("mirror.studio.app:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_doctor(args, out: Out) -> int:
    from mirror.interpret.auto import cassette_dir, proposal_recorded, recorded_models
    from mirror.interpret.llm import DEFAULT_BASE_URL, DEFAULT_MODEL
    ok = True
    rows = []

    def check(name: str, good: bool, detail: str, required: bool = True) -> None:
        nonlocal ok
        ok = ok and (good or not required)
        rows.append(("ok" if good else ("FAIL" if required else "warn"), name, detail))

    check("python", sys.version_info >= (3, 10), sys.version.split()[0])
    try:
        import mujoco
        check("mujoco", True, mujoco.__version__)
    except Exception as e:  # noqa: BLE001
        check("mujoco", False, f"import failed: {e}")
    for mod in ("mirror.sim", "mirror.scenes", "mirror.plan", "mirror.evaluate", "mirror.interpret",
                "mirror.pipeline", "mirror.studio.app"):
        try:
            importlib.import_module(mod)
            check(mod, True, "imports")
        except Exception as e:  # noqa: BLE001
            check(mod, False, f"{type(e).__name__}: {e}"[:120], required=mod != "mirror.studio.app")
    env = os.environ
    mode = env.get("MIRROR_LLM_MODE")
    check("MIRROR_LLM_MODE", True, mode or "unset (replay when recordings match, else offline rules)")
    check("model base URL", True, env.get("MIRROR_LLM_BASE_URL") or f"{DEFAULT_BASE_URL} (default)")
    check("model id", True, env.get("MIRROR_LLM_MODEL") or f"{DEFAULT_MODEL} (default)")
    has_key = bool(env.get("MODEL_API_KEY"))
    check("MODEL_API_KEY", has_key or mode not in ("live", "record"),
          "set" if has_key else "not set (needed only for live or record mode)",
          required=mode in ("live", "record"))
    cdir = cassette_dir(env)
    models = recorded_models(cdir)
    n = len(list(Path(cdir).glob("*.json")))
    check("recordings", n > 0, f"{n} in {cdir}" + (f"; recorded with {', '.join(sorted(set(models.values())))}"
                                                     if models else ""), required=False)
    if DEMO_PATH.exists():
        check("reference demo", True, str(DEMO_PATH.relative_to(REPO)))
        try:
            trace = load_demo(DEMO_PATH)
            match = any(proposal_recorded(trace, m, cdir) for m in models)
            check("recordings match demo", match,
                  "yes: the proposal will be replayed" if match else
                  "no: interpretation will use the offline rules unless MIRROR_LLM_MODE is set",
                  required=False)
        except Exception as e:  # noqa: BLE001
            check("reference demo", False, f"unreadable: {e}"[:120])
    else:
        check("reference demo", False, "missing (run `mirror demo --regenerate`)", required=False)
    if args.ping:
        import httpx
        base = (env.get("MIRROR_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        headers = {"Authorization": f"Bearer {env['MODEL_API_KEY']}"} if has_key else {}
        try:
            r = httpx.get(f"{base}/models", headers=headers, timeout=10)
            check("endpoint /models", r.status_code == 200, f"HTTP {r.status_code}", required=False)
        except Exception as e:  # noqa: BLE001
            check("endpoint /models", False, f"{type(e).__name__}", required=False)
    out(table(rows, ["", "check", "detail"], indent=""))
    out()
    out("All required checks passed." if ok else "Some required checks failed.")
    return 0 if ok else 1


# ----------------------------------------------------------------- parser ---

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mirror", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="summary of the reference demonstration")
    d.add_argument("--demo", help="demonstration JSON (default: the reference demo)")
    d.add_argument("--regenerate", action="store_true", help="record the scripted reference demo again")
    d.add_argument("--out", help="where --regenerate writes (default: data/demos/reference_demo.json)")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_demo)

    i = sub.add_parser("interpret", help="proposed task specification")
    i.add_argument("--demo", help="demonstration JSON (.json or .json.gz)")
    i.add_argument("--json", action="store_true", help="print the specification as JSON")
    i.add_argument("--keyframes", type=int, default=0,
                   help="attach N rendered simulator frames of the demonstration to the prompt (off by default)")
    i.add_argument("--keyframes-dir", help="attach previously saved keyframes (index.json + PNGs)")
    i.set_defaults(func=cmd_interpret)

    r = sub.add_parser("run", help="plan, execute and check one scene")
    r.add_argument("--scene", choices=["demo", "shortcut", "blocked"], required=True)
    r.add_argument("--spec", help="TaskSpec JSON or a session's specs.json (default: reviewed proposal)")
    r.add_argument("--spec-version", type=int, help="version to take from a specs.json")
    r.add_argument("--demo", help="demonstration to interpret when --spec is not given")
    r.add_argument("--out", help="export the session to this directory")
    r.add_argument("--frame-hz", type=float, default=30.0)
    r.add_argument("--json", action="store_true", help="print the rollout report as JSON")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("story", help="the full reference narrative")
    s.add_argument("--demo", help="demonstration JSON (default: the reference demo)")
    s.add_argument("--out", help="export directory (default: results/tmp/story_<id>)")
    s.add_argument("--correction", default=REFERENCE_CORRECTION)
    s.add_argument("--frame-hz", type=float, default=30.0)
    s.set_defaults(func=cmd_story)

    e = sub.add_parser("eval", help="evaluation runs (arguments go to scripts/run_eval.py)")
    e.add_argument("eval_args", nargs=argparse.REMAINDER,
                   help="e.g. dev --seeds 0-5 --out results/dev")
    e.set_defaults(func=cmd_eval)

    st = sub.add_parser("studio", help="launch the studio web app")
    st.add_argument("--port", type=int, default=8000)
    st.add_argument("--host", default="127.0.0.1")
    st.set_defaults(func=cmd_studio)

    dr = sub.add_parser("doctor", help="check the installation and model configuration")
    dr.add_argument("--ping", action="store_true", help="also query the model endpoint's /models")
    dr.set_defaults(func=cmd_doctor)
    return p


def main(argv: Optional[Iterable[str]] = None, stream=None) -> int:
    args = build_parser().parse_args(None if argv is None else list(argv))
    out = Out(stream)
    try:
        return int(args.func(args, out) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
