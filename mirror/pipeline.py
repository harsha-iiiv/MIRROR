"""End-to-end session: demonstration -> specification -> execution -> correction -> testing.

`Session` composes the packages through their public functions
(docs/CONTRACTS.md). It holds every specification version, rollout and step
log so that any recorded behaviour can be re-checked against any later
specification version.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .contracts import (CorrectionRecord, DemoTrace, PlanResult, Rollout,
                        RolloutReport, SceneState, SpecOp, TaskSpec)
from .statelog import StateLog


@dataclass
class RolloutEntry:
    rollout: Rollout
    log: Optional[StateLog]
    plan: PlanResult
    scene: SceneState
    spec_version: int


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    client: Any = None
    demo: Optional[DemoTrace] = None
    scene: Optional[SceneState] = None
    specs: list[TaskSpec] = field(default_factory=list)
    rollouts: dict[str, RolloutEntry] = field(default_factory=dict)
    clarification: Any = None
    events: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------ helpers
    def _client(self):
        if self.client is None:
            from .interpret import LLMClient
            self.client = LLMClient.from_env()
        return self.client

    def _event(self, event: str, **data) -> None:
        self.events.append({"t": time.time(), "event": event, **data})

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

    # -------------------------------------------------------- demonstration
    def load_demo(self, path: str | Path) -> DemoTrace:
        self.demo = DemoTrace.model_validate_json(Path(path).read_text())
        self.scene = self.demo.scene
        self._event("demo_loaded", demo_id=self.demo.demo_id, source=self.demo.source)
        return self.demo

    def record_demo(self, scene: SceneState) -> DemoTrace:
        from .sim import record_scripted_demo
        self.demo = record_scripted_demo(scene)
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

    def clarify(self, focus_kind: Optional[str] = None) -> Any:
        from .interpret import make_clarification
        from .plan import candidate_plans
        # Ask for several distinct behaviours: with only two, both may differ
        # in an object the user does not care about.
        candidates = candidate_plans(self.spec, self.scene, k=4)
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
        rid = f"r{len(self.rollouts) + 1:03d}"
        if plan_result.status != "ok":
            report = RolloutReport(rollout_id=rid, scene_id=scene.scene_id, spec_id=spec.spec_id,
                                   spec_version=spec.version, condition=condition,
                                   status="planning_failed", plan_status=plan_result.status,
                                   plan_reason=plan_result.reason,
                                   planning_time_s=plan_result.planning_time_s,
                                   wall_time_s=time.time() - t0)
            entry = RolloutEntry(Rollout(report=report, visual=visual), None, plan_result, scene, spec.version)
        else:
            result = env.execute(plan_result, frame_hz=frame_hz)
            report = check(result.log, spec, scene)
            report.rollout_id = rid
            report.condition = condition
            report.plan_status = plan_result.status
            report.route_labels = chosen_routes(plan_result)
            report.planning_time_s = plan_result.planning_time_s
            if result.status != "completed":
                report.status = "timeout" if result.status == "timeout" else "execution_failed"
                report.notes.append(result.reason)
            report.wall_time_s = time.time() - t0
            entry = RolloutEntry(Rollout(report=report, visual=visual, frame_hz=frame_hz,
                                         frames=result.frames), result.log, plan_result, scene, spec.version)
        self.rollouts[rid] = entry
        self._event("rollout", rollout_id=rid, status=entry.rollout.report.status,
                    spec_version=spec.version, scene_id=scene.scene_id)
        return entry

    def recheck(self, rollout_id: str, spec_version: Optional[int] = None) -> RolloutReport:
        """Re-check a recorded rollout against another specification version."""
        from .evaluate import check
        entry = self.rollouts[rollout_id]
        if entry.log is None:
            raise ValueError("rollout has no step log (planning failed)")
        spec = self.spec_version(spec_version)
        report = check(entry.log, spec, entry.scene)
        report.rollout_id = f"{rollout_id}@v{spec.version}"
        report.condition = f"recheck of {rollout_id} under v{spec.version}"
        return report

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
        (out / "events.json").write_text(json.dumps(self.events, indent=1, default=str))
        return out


def chosen_routes(plan: PlanResult) -> list[str]:
    """Route label of every transport step, as "<object_id>:<label>"."""
    out = []
    for s in plan.steps:
        if s.kind != "transport":
            continue
        for part in s.note.split(";"):
            part = part.strip()
            if part.startswith("route="):
                out.append(f"{s.object_id}:{part[len('route='):]}")
    return out
