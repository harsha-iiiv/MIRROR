"""End-to-end session: demonstration -> specification -> execution -> correction -> testing.

`Session` composes the packages through their public functions
(docs/CONTRACTS.md). It holds every specification version, rollout and step
log so that any recorded behaviour can be re-checked against any later
specification version.

Lineage (mirror/lineage.py): state belongs to a *generation*. A new or
replaced demonstration, or a fresh interpretation, archives the current
generation and starts another, so specification versions are never reused
and old rollouts keep resolving against the specifications they ran under.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .contracts import (CorrectionRecord, DemoTrace, PlanResult, Rollout,
                        RolloutReport, SceneState, SpecOp, TaskSpec)
from .lineage import (Generation, make_rollout_id, rollout_content_sha256, scene_sha256, sha256_of,
                      spec_version_id)
from .lineage import spec_version_id as spec_version_id_of
from .statelog import StateLog


@dataclass
class RolloutEntry:
    rollout: Rollout
    log: Optional[StateLog]
    plan: PlanResult
    scene: SceneState
    spec_version: int
    # Immutable lineage (mirror/lineage.py). Empty only for entries built by old callers.
    rollout_id: str = ""
    generation_id: str = ""
    spec_version_id: str = ""
    spec_sha256: str = ""
    scene_sha256: str = ""
    content_sha256: str = ""
    label: str = ""
    kind: str = "r"

    def lineage(self) -> dict:
        return {"rollout_id": self.rollout_id, "generation_id": self.generation_id,
                "spec_version_id": self.spec_version_id, "spec_sha256": self.spec_sha256,
                "scene_sha256": self.scene_sha256, "content_sha256": self.content_sha256,
                "label": self.label, "kind": self.kind}


@dataclass
class ArchivedGeneration:
    """A previous demonstration with the specifications and rollouts derived from it (read only)."""
    generation: Generation
    demo: Optional[DemoTrace]
    scene: Optional[SceneState]
    specs: list
    rollouts: dict

    def public(self) -> dict:
        return {**self.generation.public(),
                "spec_version_ids": [spec_version_id(self.generation.id, s.version) for s in self.specs],
                "rollout_ids": list(self.rollouts)}


class Session:
    """One authoring session. State belongs to the current *generation*; replacing the
    demonstration or re-interpreting it archives the generation instead of mutating it."""

    def __init__(self, session_id: Optional[str] = None, client: Any = None, demo: Optional[DemoTrace] = None,
                 scene: Optional[SceneState] = None, meta_client: Any = None):
        self.session_id: str = session_id or uuid.uuid4().hex[:10]
        self.client = client
        self.meta_client = meta_client  # mirror.meta.MetaClient for speech; created from the environment when needed
        self._demo: Optional[DemoTrace] = None
        self.scene: Optional[SceneState] = scene
        self.specs: list[TaskSpec] = []
        self.rollouts: dict[str, RolloutEntry] = {}
        self.clarification: Any = None
        self.clarification_candidates: list = []
        self.events: list[dict] = []
        self.generation = Generation(reason="started")
        self.archive: list[ArchivedGeneration] = []
        if demo is not None:
            self.set_demo(demo, keep_scene=scene is not None)

    # ------------------------------------------------------------ helpers
    def _client(self):
        if self.client is None:
            from .interpret import LLMClient
            self.client = LLMClient.from_env()
        return self.client

    def _event(self, event: str, **data) -> None:
        self.events.append({"t": time.time(), "event": event, "generation_id": self.generation.id, **data})

    @property
    def spec(self) -> TaskSpec:
        if not self.specs:
            raise RuntimeError("no specification yet: call interpret() first")
        return self.specs[-1]

    def spec_version(self, version: Optional[int]) -> TaskSpec:
        if version is None:
            return self.spec
        for s in self.specs:
            if s.version == version:
                return s
        raise KeyError(f"no specification version {version}")

    def spec_version_id(self, spec_or_version: Any = None) -> str:
        """Generation-scoped identity of a current specification version."""
        if spec_or_version is None:
            spec_or_version = self.spec
        v = spec_or_version if isinstance(spec_or_version, int) else spec_or_version.version
        return spec_version_id(self.generation.id, v)

    # ------------------------------------------------------------ lineage
    def _has_content(self) -> bool:
        return self._demo is not None or bool(self.specs) or bool(self.rollouts)

    def _start_generation(self, reason: str, *, carry_demo: bool) -> Generation:
        old = self.generation
        new = Generation(reason=reason, parent=old.id if self._has_content() else old.parent)
        if self._has_content():
            old.archived_at = time.time()
            old.archived_reason = f"{reason}: replaced by {new.id}"
            self.archive.append(ArchivedGeneration(old, self._demo, self.scene, list(self.specs), dict(self.rollouts)))
        if carry_demo and self._demo is not None:
            new.bind_demo(self._demo, source_media=list(old.source_media), calibration=old.calibration)
        self.generation = new
        self.specs = []
        self.rollouts = {}
        self.clarification = None
        self.clarification_candidates = []
        self._event("generation_started", reason=reason, parent=new.parent)
        return new

    @property
    def demo(self) -> Optional[DemoTrace]:
        return self._demo

    @demo.setter
    def demo(self, trace: Optional[DemoTrace]) -> None:
        self.set_demo(trace, reason="demo_set", keep_scene=True)

    def set_demo(self, trace: Optional[DemoTrace], *, reason: str = "demo_loaded", source_media: Optional[list] = None,
                 calibration: Optional[dict] = None, keep_scene: bool = False) -> Generation:
        """Replace the demonstration: archive the current generation and start a new one.

        Specifications and rollouts of the old demonstration are never carried over."""
        gen = self._start_generation(reason, carry_demo=False)
        self._demo = trace
        gen.bind_demo(trace, source_media=source_media or [], calibration=calibration)
        if trace is not None and not (keep_scene and self.scene is not None):
            self.scene = trace.scene
        return gen

    def all_generations(self) -> list:
        return [a.generation for a in self.archive] + [self.generation]

    def find_rollout(self, rollout_id: str) -> tuple[RolloutEntry, Optional[ArchivedGeneration]]:
        """A rollout of the current generation (owner None) or of an archived one."""
        if rollout_id in self.rollouts:
            return self.rollouts[rollout_id], None
        for a in reversed(self.archive):
            if rollout_id in a.rollouts:
                return a.rollouts[rollout_id], a
        raise KeyError(rollout_id)

    def resolve_spec_id(self, version_id: str) -> TaskSpec:
        gen, _, v = (version_id or "").rpartition(".v")
        if not gen or not v.isdigit():
            raise KeyError(f"bad specification version id {version_id!r}")
        pools = [(self.generation.id, self.specs)] + [(a.generation.id, a.specs) for a in self.archive]
        for gid, specs in pools:
            if gid == gen:
                for s in specs:
                    if s.version == int(v):
                        return s
        raise KeyError(f"no specification version {version_id}")

    def _register(self, rollout: Rollout, log: Optional[StateLog], plan: PlanResult, scene: SceneState,
                  spec: TaskSpec, kind: str) -> RolloutEntry:
        spec_sha, scn_sha = sha256_of(spec), scene_sha256(scene)
        content = rollout_content_sha256(rollout, spec_sha=spec_sha, scene_sha=scn_sha, plan=plan)
        seq = self.generation.next_seq()
        rid = make_rollout_id(self.generation.id, spec.version, scene, kind, seq, content)
        rollout.report.rollout_id = rid
        entry = RolloutEntry(rollout, log, plan, scene, spec.version, rollout_id=rid,
                             generation_id=self.generation.id,
                             spec_version_id=spec_version_id(self.generation.id, spec.version),
                             spec_sha256=spec_sha, scene_sha256=scn_sha, content_sha256=content,
                             label=f"{kind}{seq:03d}", kind=kind)
        self.rollouts[rid] = entry
        return entry

    def add_rollout(self, rollout: Rollout, log: Optional[StateLog], plan: PlanResult, scene: SceneState,
                    spec: TaskSpec, kind: str = "c") -> RolloutEntry:
        """Register a rollout executed outside :meth:`run` (clarification candidates)."""
        if spec not in self.specs:
            raise RuntimeError("the specification does not belong to the current generation")
        return self._register(rollout, log, plan, scene, spec, kind)

    # -------------------------------------------------------- demonstration
    def load_demo(self, path: str | Path) -> DemoTrace:
        self.set_demo(DemoTrace.model_validate_json(Path(path).read_text()), reason="demo_loaded")
        self._event("demo_loaded", demo_id=self.demo.demo_id, source=self.demo.source)
        return self.demo

    def record_demo(self, scene: SceneState) -> DemoTrace:
        from .sim import record_scripted_demo
        self.set_demo(record_scripted_demo(scene), reason="demo_recorded")
        self.scene = scene
        self._event("demo_recorded", demo_id=self.demo.demo_id, source=self.demo.source)
        return self.demo

    # ------------------------------------------------------- interpretation
    def interpret(self) -> TaskSpec:
        from .interpret import propose_spec, validate_spec
        if self.demo is None:
            raise RuntimeError("load or record a demonstration first")
        spec = propose_spec(self.demo, self._client())
        errors = validate_spec(spec)
        if errors:
            raise ValueError("interpreter returned an invalid specification: " + "; ".join(errors))
        if self.specs or self.rollouts:
            # A fresh proposal restarts the version history: archive the old one with its rollouts.
            self._start_generation("reinterpreted", carry_demo=True)
        self.specs = [spec]
        self._event("spec_proposed", version=spec.version, interpreter=spec.interpreter)
        return spec

    def apply_ops(self, ops: list[SpecOp], text: str, kind: str = "confirmation") -> TaskSpec:
        from .interpret import apply_ops
        new_spec, record = apply_ops(self.spec, ops, text=text, kind=kind)
        self.specs.append(new_spec)
        self._event("spec_updated", version=new_spec.version, kind=kind, text=text)
        return new_spec

    def confirm(self, confirm: list[str] = (), reject: list[str] = (), text: str = "") -> TaskSpec:
        """Apply the human review of a proposal (record kind "confirmation")."""
        from .interpret import confirm_items
        new_spec, record = confirm_items(self.spec, confirm=confirm, reject=reject, text=text)
        if new_spec.version != self.spec.version:
            self.specs.append(new_spec)
        self._event("review", version=new_spec.version, confirm=list(confirm), reject=list(reject))
        return new_spec

    def correct(self, text: str, focus_kind: Optional[str] = None) -> tuple[TaskSpec, CorrectionRecord]:
        from .interpret import compile_correction
        new_spec, record = compile_correction(self.spec, text, self._client(),
                                              context={"scene": self.scene, "focus_kind": focus_kind})
        if new_spec.version != self.spec.version:
            self.specs.append(new_spec)
        self._event("correction", version=new_spec.version, text=text)
        return new_spec, record

    def preview_correction(self, text: str, focus_kind: Optional[str] = None) -> tuple[TaskSpec, CorrectionRecord]:
        """Compile a correction without committing it (Challenge mode reviews it first)."""
        from .interpret import compile_correction
        new_spec, record = compile_correction(self.spec, text, self._client(),
                                              context={"scene": self.scene, "focus_kind": focus_kind})
        self._event("correction_previewed", base_version=self.spec.version, text=text)
        return new_spec, record

    def commit_spec(self, new_spec: TaskSpec, base_version: int, text: str = "") -> TaskSpec:
        """Append a reviewed specification version compiled from ``base_version``."""
        if not self.specs or self.spec.version != base_version:
            raise RuntimeError("the specification changed since this proposal was made; compile it again")
        if new_spec.version != base_version + 1 or (new_spec.parent_version not in (None, base_version)):
            raise ValueError("the proposal does not follow the current specification version")
        self.specs.append(new_spec)
        self._event("correction", version=new_spec.version, text=text, reviewed=True)
        return new_spec

    def correct_audio(self, wav: str | Path | bytes, focus_kind: Optional[str] = None,
                      keywords: Optional[list[str]] = None):
        """A spoken correction: transcribe it with Muse Voice Transcribe, then
        compile the transcript exactly like typed text. Returns
        (spec, record, transcript); the transcript and its provenance are logged."""
        from .interpret.voice import transcribe
        if self.meta_client is None:
            from .meta import MetaClient
            self.meta_client = MetaClient.from_env()
        transcript = transcribe(wav, self.meta_client, keywords=keywords)
        self._event("voice_transcribed", text=transcript.text, model=transcript.model,
                    audio_sha256=transcript.source_sha256, converted=transcript.converted,
                    latency_s=round(transcript.latency_s, 3), from_cassette=transcript.from_cassette,
                    keywords=transcript.keywords)
        new_spec, record = self.correct(transcript.text, focus_kind)
        return new_spec, record, transcript

    def clarify(self, focus_kind: Optional[str] = None) -> Any:
        from .interpret import make_clarification
        from .plan import candidate_plans
        # Ask for several distinct behaviours: with only two, both may differ
        # in an object the user does not care about.
        candidates = candidate_plans(self.spec, self.scene, k=4)
        self.clarification_candidates = candidates
        self.clarification = make_clarification(self.spec, self.scene, candidates,
                                                focus_kind=focus_kind)
        self._event("clarification", n_candidates=len(candidates))
        return self.clarification

    def answer(self, text: str) -> tuple[TaskSpec, CorrectionRecord]:
        from .interpret import apply_answer
        if self.clarification is None:
            raise RuntimeError("no open clarification")
        new_spec, record = apply_answer(self.spec, self.clarification, text, self._client())
        if new_spec.version != self.spec.version:
            self.specs.append(new_spec)
        self.clarification = None
        self._event("clarification_answered", version=new_spec.version, text=text)
        return new_spec, record

    # ----------------------------------------------------------- execution
    def set_scene(self, scene: SceneState | str) -> SceneState:
        if isinstance(scene, str):
            from .scenes import reference_scene
            scene = reference_scene(scene)
        self.scene = scene
        self._event("scene_changed", scene_id=scene.scene_id)
        return scene

    def run(self, *, spec_version: Optional[int] = None, condition: str = "",
            frame_hz: float = 30.0) -> RolloutEntry:
        from .evaluate import check
        from .plan import plan as make_plan
        from .sim import SimEnv
        spec = self.spec_version(spec_version)
        scene = self.scene
        t0 = time.time()
        plan_result = make_plan(spec, scene)
        env = SimEnv(scene)
        visual = env.visual_scene()
        if plan_result.status != "ok":
            report = RolloutReport(rollout_id="", scene_id=scene.scene_id, spec_id=spec.spec_id,
                                   spec_version=spec.version, condition=condition,
                                   status="planning_failed", plan_status=plan_result.status,
                                   plan_reason=plan_result.reason,
                                   planning_time_s=plan_result.planning_time_s,
                                   wall_time_s=time.time() - t0)
            rollout, log = Rollout(report=report, visual=visual), None
        else:
            result = env.execute(plan_result, frame_hz=frame_hz)
            report = check(result.log, spec, scene)
            report.condition = condition
            report.plan_status = plan_result.status
            report.route_labels = chosen_routes(plan_result)
            report.planning_time_s = plan_result.planning_time_s
            if result.status != "completed":
                report.status = "timeout" if result.status == "timeout" else "execution_failed"
                report.notes.append(result.reason)
            report.wall_time_s = time.time() - t0
            rollout, log = Rollout(report=report, visual=visual, frame_hz=frame_hz, frames=result.frames), result.log
        entry = self._register(rollout, log, plan_result, scene, spec, "r")
        self._event("rollout", rollout_id=entry.rollout_id, status=entry.rollout.report.status,
                    spec_version=spec.version, scene_id=scene.scene_id)
        return entry

    def recheck_target(self, rollout_id: str, spec_version: Optional[int] = None,
                       spec_version_id: Optional[str] = None) -> tuple[RolloutEntry, TaskSpec, str, str]:
        """(entry, spec, spec version id, rollout's own generation id) for a recheck.

        An integer version is resolved inside the rollout's own generation, so an
        archived rollout is never matched to an unrelated specification that happens
        to reuse its number; ``spec_version_id`` names any version explicitly."""
        entry, owner = self.find_rollout(rollout_id)
        own_gen = owner.generation.id if owner is not None else self.generation.id
        if spec_version_id is not None:
            return entry, self.resolve_spec_id(spec_version_id), spec_version_id, own_gen
        pool = owner.specs if owner is not None else self.specs
        if not pool:
            raise RuntimeError("no specification yet")
        spec = pool[-1] if spec_version is None else next((s for s in pool if s.version == spec_version), None)
        if spec is None:
            raise KeyError(f"no specification version {spec_version} in generation {own_gen}")
        return entry, spec, spec_version_id_of(own_gen, spec.version), own_gen

    @staticmethod
    def _label_recheck(report: RolloutReport, entry: RolloutEntry, rollout_id: str, svid: str, own_gen: str):
        label = entry.label or rollout_id
        report.rollout_id = f"{rollout_id}@{svid}"
        report.condition = f"recheck of {label} under {svid}"
        if not svid.startswith(own_gen + "."):
            report.notes.append(f"cross-generation recheck: {label} came from generation {own_gen}, "
                                f"the specification from {svid.rpartition('.v')[0]}")
        return report

    def recheck(self, rollout_id: str, spec_version: Optional[int] = None, *,
                spec_version_id: Optional[str] = None) -> RolloutReport:
        """Re-check a recorded rollout (of any generation) against a specification version."""
        from .evaluate import check
        entry, spec, svid, own_gen = self.recheck_target(rollout_id, spec_version, spec_version_id)
        if entry.log is None:
            raise ValueError("rollout has no step log (planning failed)")
        return self._label_recheck(check(entry.log, spec, entry.scene), entry, rollout_id, svid, own_gen)

    # -------------------------------------------------------------- export
    def export(self, out_dir: str | Path) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "specs.json").write_text(json.dumps([s.model_dump(mode="json") for s in self.specs], indent=1))
        if self.demo is not None:
            (out / "demo.json").write_text(self.demo.model_dump_json())
        for rid, e in self.rollouts.items():
            (out / f"rollout_{rid}.json").write_text(e.rollout.model_dump_json())
            (out / f"plan_{rid}.json").write_text(e.plan.model_dump_json(indent=1))
            if e.log is not None:
                e.log.save(out / f"steplog_{rid}.npz")
        (out / "lineage.json").write_text(json.dumps(
            {"generation": self.generation.public(),
             "spec_version_ids": [self.spec_version_id(s) for s in self.specs],
             "rollouts": {rid: e.lineage() for rid, e in self.rollouts.items()},
             "archive": [a.public() for a in self.archive]}, indent=1, default=str))
        (out / "events.json").write_text(json.dumps(self.events, indent=1, default=str))
        return out

    # --------------------------------------------------------- persistence
    def save_state(self, root: str | Path) -> Path:
        """Write enough to restore this session after a restart.

        Rollout files are content addressed by their immutable id and written once;
        the small index (generation, specs, scene) is rewritten atomically."""
        root = Path(root)
        for gen, demo, scene, specs, rollouts in (
                [(a.generation, a.demo, a.scene, a.specs, a.rollouts) for a in self.archive]
                + [(self.generation, self._demo, self.scene, self.specs, self.rollouts)]):
            d = root / "generations" / gen.id
            d.mkdir(parents=True, exist_ok=True)
            _atomic_write(d / "generation.json", json.dumps(gen.to_json(), default=str))
            if demo is not None and not (d / "demo.json").exists():
                _atomic_write(d / "demo.json", demo.model_dump_json())
            _atomic_write(d / "scene.json", scene.model_dump_json() if scene is not None else "null")
            _atomic_write(d / "specs.json", json.dumps([s.model_dump(mode="json") for s in specs]))
            rd = d / "rollouts"
            rd.mkdir(exist_ok=True)
            index = {}
            for rid, e in rollouts.items():
                index[rid] = e.lineage()
                f = rd / f"{rid}.json"
                if not f.exists():
                    _atomic_write(rd / f"{rid}.plan.json", e.plan.model_dump_json())
                    _atomic_write(rd / f"{rid}.scene.json", e.scene.model_dump_json())
                    if hasattr(e.log, "save"):
                        e.log.save(rd / f"{rid}.npz")
                    elif isinstance(e.log, dict) and "stats" in e.log:      # kinematic fixture log
                        _atomic_write(rd / f"{rid}.fixture.json", json.dumps(e.log["stats"]))
                    _atomic_write(f, e.rollout.model_dump_json())     # written last: marks the set complete
            _atomic_write(d / "rollouts.json", json.dumps(index))
        _atomic_write(root / "session.json", json.dumps({
            "format": "mirror-session-state/1", "session_id": self.session_id, "saved_at": time.time(),
            "current": self.generation.id, "archive": [a.generation.id for a in self.archive],
            "events": self.events[-200:]}, default=str))
        return root

    @classmethod
    def load_state(cls, root: str | Path, client: Any = None) -> "Session":
        root = Path(root)
        meta = json.loads((root / "session.json").read_text())
        s = cls(session_id=meta["session_id"], client=client)
        s.events = list(meta.get("events") or [])

        def gen_parts(gid):
            d = root / "generations" / gid
            gen = Generation.from_json(json.loads((d / "generation.json").read_text()))
            demo = DemoTrace.model_validate_json((d / "demo.json").read_text()) if (d / "demo.json").exists() else None
            raw_scene = (d / "scene.json").read_text()
            scene = SceneState.model_validate_json(raw_scene) if raw_scene.strip() != "null" else None
            specs = [TaskSpec.model_validate(x) for x in json.loads((d / "specs.json").read_text())]
            rollouts = {}
            index = json.loads((d / "rollouts.json").read_text()) if (d / "rollouts.json").exists() else {}
            for rid, lin in index.items():
                rd = d / "rollouts"
                if not (rd / f"{rid}.json").exists():
                    continue                     # interrupted write: the rollout is dropped, never half loaded
                log = StateLog.load(rd / f"{rid}.npz") if (rd / f"{rid}.npz").exists() else None
                ro = Rollout.model_validate_json((rd / f"{rid}.json").read_text())
                if log is None and (rd / f"{rid}.fixture.json").exists():
                    log = {"frames": ro.frames, "stats": json.loads((rd / f"{rid}.fixture.json").read_text())}
                plan = PlanResult.model_validate_json((rd / f"{rid}.plan.json").read_text())
                sc = SceneState.model_validate_json((rd / f"{rid}.scene.json").read_text())
                version = int(lin.get("spec_version_id", ".v0").rpartition(".v")[2] or 0)
                rollouts[rid] = entry = RolloutEntry(ro, log, plan, sc, version,
                                             **{k: lin.get(k, "") for k in ("rollout_id", "generation_id",
                                                                            "spec_version_id", "spec_sha256",
                                                                            "scene_sha256", "content_sha256",
                                                                            "label", "kind")})
                if isinstance(log, dict):
                    entry.stats = log["stats"]
            return gen, demo, scene, specs, rollouts

        for gid in meta.get("archive") or []:
            if (root / "generations" / gid).exists():
                s.archive.append(ArchivedGeneration(*gen_parts(gid)))
        gen, demo, scene, specs, rollouts = gen_parts(meta["current"])
        s.generation, s._demo, s.scene, s.specs, s.rollouts = gen, demo, scene, specs, rollouts
        s._event("session_restored", saved_at=meta.get("saved_at"))
        return s


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp{uuid.uuid4().hex[:6]}")
    tmp.write_text(text)
    tmp.replace(path)


def chosen_routes(plan: PlanResult) -> list[str]:
    """Route label of every object transport, as "<object_id>:<label>"."""
    flagged = [f"{r.object_id}:{r.label}" for r in plan.routes
               if r.chosen and not r.label.startswith("transit:")]
    if flagged:
        return flagged
    out = []
    for s in plan.steps:
        if s.kind != "transport":
            continue
        for part in s.note.split(";"):
            part = part.strip()
            if part.startswith("route="):
                out.append(f"{s.object_id}:{part[len('route='):]}")
    return out
