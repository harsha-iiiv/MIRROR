"""Pre-registered benchmarks beyond the paired evaluations: video, voice and
perception robustness.

Three frozen files per registration, all committed before any measured run:

* ``design.json``: seeds, models, metrics, thresholds and hypotheses stated
  in advance, budget caps. Written once, after an audit shows that none of its
  seeds appear in any ledger, report, model-usage log or model recording.
* ``inputs.json``: every input file (audio, rendered video, ground-truth
  frames) with its sha256, produced without any model call. Rendering samples
  layouts, which the sampler records in the registration's ledger; the ledger
  state at the end of this stage is recorded, and nothing may be added to it
  before the freeze.
* ``freeze.json``: the code hash (every ``mirror/**/*.py`` except listed
  presentation-only paths), script/data hashes, library versions and the
  sha256 of design.json and inputs.json. Written when the harness code is
  final.

``run`` refuses if anything recorded in either file changed, or if the
registration's append-only ledger has any entry from before the freeze. Every
attempt (layout generated, model request, edit) is appended to the ledger and
reported; nothing is filtered by outcome.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import importlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional  # noqa: F401

from .paired import FreezeError, code_hash, file_hashes, runtime_versions

REPO_ROOT = Path(__file__).resolve().parents[2]

PRESENTATION_ONLY = ("studio/**", "cli.py")

REGISTRY: dict[str, dict[str, Any]] = {
    "voice": dict(out="results/eval_voice", code_exclude=PRESENTATION_ONLY + ("sim/render.py",),
                  code_entries=["mirror.evaluate.prereg", "mirror.evaluate.bench_voice", "mirror.evaluate.costs",
                                "mirror.interpret.voice", "mirror.interpret.benchmark.score", "mirror.meta",
                                "mirror.scenes"],
                  requires=[("mirror.interpret.voice", "transcribe")],
                  runner="mirror.evaluate.bench_voice:run"),
    "video": dict(out="results/eval_video", code_exclude=PRESENTATION_ONLY + ("sim/render.py",),
                  code_entries=["mirror.evaluate.prereg", "mirror.evaluate.bench_video", "mirror.evaluate.costs",
                                "mirror.perception", "mirror.perception.track", "mirror.perception.reconstruct",
                                "mirror.interpret.video_observe", "mirror.interpret", "mirror.meta", "mirror.contracts"],
                  requires=[("mirror.perception", "load_demo_video"), ("mirror.perception.track", "track_videos"),
                            ("mirror.perception.reconstruct", "estimate"),
                            ("mirror.perception.reconstruct", "scene_from_video"),
                            ("mirror.perception.reconstruct", "trace_from_estimates"),
                            ("mirror.interpret.video_observe", "observe"), ("mirror.interpret", "propose_spec")],
                  runner="mirror.evaluate.bench_video:run"),
    "robustness": dict(out="results/eval_robustness", code_exclude=PRESENTATION_ONLY + ("sim/render.py",),
                       code_entries=["mirror.evaluate.prereg", "mirror.evaluate.bench_robust", "mirror.evaluate.costs",
                                     "mirror.robustness", "mirror.robustness.geometry", "mirror.robustness.local",
                                     "mirror.robustness.frames", "mirror.robustness.instructions",
                                     "mirror.interpret.sam_prompts", "mirror.meta"],
                       requires=[("mirror.robustness.geometry", "check_geometry"),
                                 ("mirror.robustness.geometry", "warp_similarity"),
                                 ("mirror.robustness.local", "apply_local"),
                                 ("mirror.robustness.frames", "GTFrame"),
                                 ("mirror.robustness.frames", "frames_for_seeds"),
                                 ("mirror.interpret.sam_prompts", "choose_prompts"), ("mirror.meta", "MetaClient")],
                       runner="mirror.evaluate.bench_robust:run"),
}

BUDGET_CAPS = {"muse-wonder-1.0": 60, "muse-spark-1.3:video": 60, "sam-3.1": 400,
               "muse-image-1.0": 150, "muse-voice-transcribe-1.0": 300}

AUDIT_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".csv", ".html", ".log"}


def paths(name: str) -> dict[str, Path]:
    out = REPO_ROOT / REGISTRY[name]["out"]
    return dict(out=out, design=out / "design.json", inputs=out / "inputs.json", freeze=out / "freeze.json",
                ledger=out / "ledger.jsonl",
                report_json=out / "report.json", report_md=out / "report.md")


def sha256_file(p: Path | str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _shown(p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return "external:" + Path(p).name


# ------------------------------------------------------------------ ledger ---

class Ledger:
    """Append-only JSON-lines record of every attempt."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def append(self, **entry) -> dict:
        entry.setdefault("unix", time.time())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(entry, sort_keys=True, default=str) + "\n").encode())
        finally:
            os.close(fd)
        return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for i, line in enumerate(self.path.read_text().splitlines()):
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({"line": i + 1, "unparsable": line[:200]})
        return out


# ------------------------------------------------------------------ budget ---

class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Counts model requests per budget line and refuses past the cap."""

    def __init__(self, caps: Optional[dict] = None):
        self.caps = dict(caps or BUDGET_CAPS)
        self.used: dict[str, int] = {}

    def charge(self, line: str, n: int = 1) -> None:
        cap = self.caps.get(line)
        if cap is not None and self.used.get(line, 0) + n > cap:
            raise BudgetExceeded(f"budget line {line}: {self.used.get(line, 0)} used, cap {cap}")
        self.used[line] = self.used.get(line, 0) + n

    def snapshot(self) -> dict:
        return {k: {"used": self.used.get(k, 0), "cap": self.caps[k]} for k in self.caps}


# ------------------------------------------------------------------- audit ---

def audit(tokens: dict[str, list], roots: list[Path | str], *, skip: tuple = ()) -> dict:
    """Search text files (and file names) under ``roots`` for any seed (as a
    whole five-digit token) or input sha256 (full hex or its first 16 hex
    characters). ``skip`` excludes directories (the registration's own)."""
    import re
    seeds = {int(s) for s in tokens.get("seeds", [])}
    shas = {s.lower() for s in tokens.get("sha256", [])}
    short = {s[:16]: s for s in shas}
    five = re.compile(r"(?<![\d.])(\d{5})(?![\d.])")
    hexes = re.compile(r"[0-9a-f]{16,64}")
    skip = tuple(Path(s).resolve() for s in skip)
    hits, files, scanned = [], 0, []
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        scanned.append(_shown(root))
        it = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for p in it:
            rp = p.resolve()
            if any(rp == s or s in rp.parents for s in skip):
                continue
            for tok in five.findall(p.name):
                if int(tok) in seeds:
                    hits.append(dict(file=_shown(p), seed=int(tok), where="file name"))
            if p.suffix.lower() not in AUDIT_SUFFIXES:
                continue
            files += 1
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            found = sorted({int(t) for t in five.findall(text)} & seeds)
            if found:
                hits.append(dict(file=_shown(p), seeds=found[:10], where="content"))
            if shas:
                low = text.lower()
                hs = sorted({short[h[:16]] for h in hexes.findall(low) if h[:16] in short})
                if hs:
                    hits.append(dict(file=_shown(p), input_sha256=[h[:16] for h in hs[:5]], where="content"))
    return dict(roots=scanned, text_files=files, hits=hits)


def default_audit_roots() -> list[Path]:
    return [REPO_ROOT / "results", REPO_ROOT / "data"]


def _tokens(design: dict, inputs: Optional[dict] = None) -> dict:
    seeds = list(design.get("seeds", [])) + list((design.get("frames") or {}).get("seeds", []))
    return dict(seeds=seeds, sha256=[f["sha256"] for f in (inputs or {}).get("files", [])])


def ledger_snapshot(path: Path) -> dict:
    data = path.read_bytes() if path.exists() else b""
    return dict(lines=data.count(b"\n"), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


# ------------------------------------------------------------------ design ---

def write_design(name: str, design: dict, *, audit_paths: tuple = (), replace: bool = False) -> dict:
    """Write design.json once. ``replace`` is allowed only while no freeze.json
    exists and the ledger is empty (nothing has been measured)."""
    p = paths(name)
    if p["design"].exists():
        if not replace:
            raise FileExistsError(f"{_shown(p['design'])} exists; a design is written once")
        if p["freeze"].exists() or p["inputs"].exists() or Ledger(p["ledger"]).entries():
            raise FreezeError("design can no longer be replaced: inputs made, frozen or measured")
    a = audit(_tokens(design), default_audit_roots() + [Path(x) for x in audit_paths], skip=(p["out"],))
    if a["hits"]:
        raise FreezeError(f"seeds or inputs of '{name}' already appear in {len(a['hits'])} place(s): {a['hits'][:3]}")
    if Ledger(p["ledger"]).entries():
        raise FreezeError(f"ledger of '{name}' is not empty before the design")
    now = time.time()
    doc = dict(design, registration=name,
               created_at=_dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat(), created_unix=now,
               audit=dict(a, result="no seed or input hash of this registration found"))
    p["out"].mkdir(parents=True, exist_ok=True)
    p["design"].write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    return doc


def amendment_files(name: str) -> list[Path]:
    return sorted(paths(name)["out"].glob("design_amendment_*.json"))


def amendment_hashes(name: str) -> dict:
    return {p.name: sha256_file(p) for p in amendment_files(name)}


def load_design(name: str) -> dict:
    """design.json plus any add-only amendments (which may add fields but
    never change a published one)."""
    design = json.loads(paths(name)["design"].read_text())
    for p in amendment_files(name):
        am = json.loads(p.read_text())
        clash = sorted(set(am.get("adds", {})) & set(design))
        if clash:
            raise FreezeError(f"{p.name} would change published design fields {clash}")
        design.update(am.get("adds", {}))
        design.setdefault("amendments", []).append({k: am[k] for k in ("file", "written_at", "rationale") if k in am})
    return design


def write_amendment(name: str, number: int, adds: dict, rationale: str) -> dict:
    """An add-only, dated clarification of a published design, allowed only
    before any input is made."""
    p = paths(name)
    if p["inputs"].exists() or p["freeze"].exists() or Ledger(p["ledger"]).entries():
        raise FreezeError("amendments must be written before any input or measurement")
    path = p["out"] / f"design_amendment_{number}.json"
    if path.exists():
        raise FileExistsError(f"{_shown(path)} exists")
    base = json.loads(p["design"].read_text())
    clash = sorted(set(adds) & set(base))
    if clash:
        raise FreezeError(f"an amendment may not change published fields: {clash}")
    now = time.time()
    doc = dict(file=path.name, registration=name, amends_design_sha256=sha256_file(p["design"]),
               written_at=_dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat(), written_unix=now,
               rationale=rationale, adds=adds)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def write_inputs(name: str, files: list[dict], *, provenance: Optional[dict] = None,
                 audit_paths: tuple = (), replace: bool = False) -> dict:
    """Record the input files (paths relative to the repository, sha256
    verified here) and the ledger state at the end of input generation."""
    p = paths(name)
    if not p["design"].exists():
        raise FreezeError("write the design first")
    if p["freeze"].exists():
        raise FreezeError("already frozen")
    if p["inputs"].exists() and not replace:
        raise FileExistsError(f"{_shown(p['inputs'])} exists")
    for f in files:
        if sha256_file(REPO_ROOT / f["path"]) != f["sha256"]:
            raise FreezeError(f"input {f['path']} does not match its sha256")
    design = load_design(name)
    a = audit(_tokens(design, {"files": files}), default_audit_roots() + [Path(x) for x in audit_paths],
              skip=(p["out"],))
    if a["hits"]:
        raise FreezeError(f"seeds or inputs of '{name}' already appear in: {a['hits'][:3]}")
    bad = [e for e in Ledger(p["ledger"]).entries() if e.get("kind") not in (None, "input")]
    if bad:
        raise FreezeError(f"ledger of '{name}' has non-input entries before the freeze: {bad[:2]}")
    now = time.time()
    doc = dict(registration=name, design_sha256=sha256_file(p["design"]), amendments=amendment_hashes(name),
               created_at=_dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat(), created_unix=now,
               files=files, provenance=dict(provenance or {}), ledger_after_inputs=ledger_snapshot(p["ledger"]),
               audit=dict(a, result="no seed or input hash of this registration found"))
    p["inputs"].write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def load_inputs(name: str) -> dict:
    return json.loads(paths(name)["inputs"].read_text())


# ------------------------------------------------------------------ freeze ---

def harness_ready(name: str) -> list[str]:
    """Missing modules/functions the registration's run needs."""
    missing = []
    for mod, attr in REGISTRY[name]["requires"]:
        try:
            m = importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001 - report any import failure
            missing.append(f"{mod} ({type(exc).__name__})")
            continue
        if not hasattr(m, attr):
            missing.append(f"{mod}.{attr}")
    return missing


def import_closure(entries: list[str], *, exclude: tuple = ()) -> list[str]:
    """Every ``mirror`` source file reachable from ``entries`` through import
    statements anywhere in a module (function-level imports included) plus the
    package ``__init__`` files on each path; relative to ``mirror/``.
    Conservative: an import that is never executed is still followed."""
    import ast
    from .paired import PACKAGE_ROOT, _excluded

    def file_of(mod: str) -> Optional[Path]:
        parts = mod.split(".")
        if parts[0] != "mirror":
            return None
        p = PACKAGE_ROOT.joinpath(*parts[1:])
        if (p / "__init__.py").exists():
            return p / "__init__.py"
        if p.with_suffix(".py").exists():
            return p.with_suffix(".py")
        return None

    def parents(mod: str) -> list[str]:
        parts = mod.split(".")
        return [".".join(parts[:i]) for i in range(1, len(parts))]

    seen, todo = set(), list(entries)
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        todo += [m for m in parents(mod) if m not in seen]
        f = file_of(mod)
        if f is None:
            continue
        pkg = mod if f.name == "__init__.py" else mod.rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                todo += [a.name for a in node.names if a.name.startswith("mirror")]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg.split(".")
                    base = base[:len(base) - (node.level - 1)]
                    m = ".".join(base + ([node.module] if node.module else []))
                else:
                    m = node.module or ""
                if m.startswith("mirror"):
                    todo.append(m)
                    todo += [f"{m}.{a.name}" for a in node.names]       # submodule imports
    files = sorted({file_of(m).relative_to(PACKAGE_ROOT).as_posix() for m in seen if file_of(m)})
    return [f for f in files if not _excluded(f, exclude)]


def closure_hash(files: list[str]) -> str:
    from .paired import PACKAGE_ROOT
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.encode())
        h.update(b"\0")
        h.update((PACKAGE_ROOT / rel).read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _code(name: str) -> tuple[str, int, list[str], Optional[list[str]]]:
    """(hash, file count, excluded patterns, hashed files or None). With
    ``code_entries`` the hash covers the import closure of the harness only."""
    ex = tuple(REGISTRY[name]["code_exclude"])
    entries = REGISTRY[name].get("code_entries")
    if entries:
        files = import_closure(entries, exclude=ex)
        return closure_hash(files), len(files), list(ex), files
    d, n = code_hash(exclude=ex)
    return d, n, list(ex), None


def freeze(name: str, *, audit_paths: tuple = (), allow_missing: bool = False) -> dict:
    """Write freeze.json: locks code, scripts, data, versions and the design."""
    p = paths(name)
    if p["freeze"].exists():
        raise FileExistsError(f"{_shown(p['freeze'])} exists; a freeze is written once")
    if not p["design"].exists():
        raise FreezeError(f"no design for '{name}'; write the design first")
    missing = harness_ready(name)
    if missing and not allow_missing:
        raise FreezeError(f"harness not ready for '{name}': missing {', '.join(missing)}")
    design = load_design(name)
    if not p["inputs"].exists():
        raise FreezeError(f"no inputs.json for '{name}'; generate the inputs first")
    inputs = load_inputs(name)
    if inputs["design_sha256"] != sha256_file(p["design"]) or inputs.get("amendments", {}) != amendment_hashes(name):
        raise FreezeError("design or its amendments changed after the inputs were made")
    for f in inputs["files"]:
        if sha256_file(REPO_ROOT / f["path"]) != f["sha256"]:
            raise FreezeError(f"input {f['path']} changed since the inputs stage")
    if ledger_snapshot(p["ledger"]) != inputs["ledger_after_inputs"]:
        raise FreezeError(f"ledger of '{name}' changed after the inputs stage")
    a = audit(_tokens(design, inputs), default_audit_roots() + [Path(x) for x in audit_paths], skip=(p["out"],))
    if a["hits"]:
        raise FreezeError(f"seeds or inputs of '{name}' already appear in: {a['hits'][:3]}")
    digest, n, ex, files = _code(name)
    now = time.time()
    doc = dict(registration=name, created_at=_dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat(),
               created_unix=now, design_sha256=sha256_file(p["design"]), amendments=amendment_hashes(name),
               inputs_sha256=sha256_file(p["inputs"]),
               code_sha256=digest, code_files=n, code_scope=("import closure of " + ", ".join(REGISTRY[name]["code_entries"])
                                                             if files is not None else "all of mirror/ minus exclusions"),
               code_file_list=files, code_exclude=ex, file_hashes=file_hashes(), versions=runtime_versions(),
               harness_missing=missing, audit=dict(a, result="no seed or input hash of this registration found"))
    p["freeze"].write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def verify(name: str) -> dict:
    p = paths(name)
    if not p["freeze"].exists():
        raise FreezeError(f"'{name}' is not frozen; run freeze-final --registration {name} first")
    fz = json.loads(p["freeze"].read_text())
    problems = []
    if sha256_file(p["design"]) != fz["design_sha256"]:
        problems.append("design.json changed")
    if sha256_file(p["inputs"]) != fz["inputs_sha256"]:
        problems.append("inputs.json changed")
    if amendment_hashes(name) != fz.get("amendments", {}):
        problems.append("design amendments changed")
    inputs = load_inputs(name)
    for f in inputs["files"]:
        if sha256_file(REPO_ROOT / f["path"]) != f["sha256"]:
            problems.append(f"input {f['path']} changed")
    if fz.get("code_file_list"):
        missing = [f for f in fz["code_file_list"] if not (REPO_ROOT / "mirror" / f).exists()]
        now_hash = None if missing else closure_hash(fz["code_file_list"])
        if now_hash != fz["code_sha256"]:
            problems.append("code changed since the freeze")
        elif import_closure(REGISTRY[name]["code_entries"], exclude=tuple(fz["code_exclude"])) != fz["code_file_list"]:
            problems.append("the harness now imports different modules")
    elif code_hash(exclude=tuple(fz["code_exclude"]))[0] != fz["code_sha256"]:
        problems.append("code changed since the freeze")
    now = file_hashes()
    problems += [f"{g} changed" for g, v in fz["file_hashes"].items() if now.get(g, {}).get("sha256") != v["sha256"]]
    cur = runtime_versions()
    problems += [f"{k} {v} -> {cur.get(k)}" for k, v in fz["versions"].items() if cur.get(k) != v]
    snap = inputs["ledger_after_inputs"]
    data = p["ledger"].read_bytes() if p["ledger"].exists() else b""
    if hashlib.sha256(data[:snap["bytes"]]).hexdigest() != snap["sha256"]:
        problems.append("ledger lines from the inputs stage changed")
    later = [json.loads(x) for x in data[snap["bytes"]:].decode().splitlines() if x.strip()]
    early = [e for e in later if not isinstance(e.get("unix"), (int, float)) or e["unix"] < fz["created_unix"]]
    if early:
        problems.append(f"{len(early)} ledger entries between the inputs stage and the freeze")
    if problems:
        raise FreezeError(f"'{name}': " + "; ".join(problems))
    return fz


# --------------------------------------------------------------------- run ---

def _runner(name: str) -> Callable:
    mod, fn = REGISTRY[name]["runner"].split(":")
    return getattr(importlib.import_module(mod), fn)


def run(name: str, **kwargs) -> dict:
    """Verify, then run the registration's harness once. The harness appends
    every attempt to the ledger and returns a JSON-serialisable report."""
    fz = verify(name)
    p = paths(name)
    if p["report_json"].exists():
        raise FreezeError(f"{_shown(p['report_json'])} exists; a registration is run once")
    os.environ["MIRROR_EVAL_LEDGER"] = str(p["ledger"])     # layout sampler writes here too
    return execute(name, load_design(name), load_inputs(name), out_dir=p["out"], ledger_path=p["ledger"],
                   stamp=dict(design_sha256=fz["design_sha256"], inputs_sha256=fz["inputs_sha256"],
                              freeze_code_sha256=fz["code_sha256"]), **kwargs)


def execute(name: str, design: dict, inputs: dict, *, out_dir: Path, ledger_path: Path, stamp: dict,
            rehearsal: bool = False, **kwargs) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(ledger_path)
    budget = Budget(design.get("budget_caps_amended") or design.get("budget_caps"))
    started = time.time()
    ledger.append(kind="run_start", registration=name, rehearsal=rehearsal)
    try:
        result = _runner(name)(design, inputs, ledger=ledger, budget=budget, out_dir=out_dir, **kwargs)
        status = f"stopped early: {result['stopped']}" if isinstance(result, dict) and result.get("stopped") else "completed"
    except BudgetExceeded as exc:
        result, status = {"stopped": str(exc)}, "stopped: budget cap reached"
    ledger.append(kind="run_end", registration=name, status=status)
    report = dict(registration=name, title=design.get("title", name), rehearsal=rehearsal, status=status, **stamp,
                  started_unix=started, wall_time_s=round(time.time() - started, 1), budget=budget.snapshot(),
                  ledger_entries=len(ledger.entries()), hypotheses=design.get("hypotheses", []),
                  thresholds=design.get("thresholds", {}), result=result)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    md = _runner_markdown(name)
    if md:
        (out_dir / "report.md").write_text(md(report, design))
    return report


def rehearse(name: str, design: dict, inputs: dict, out_dir: Path, **kwargs) -> dict:
    """Run the harness on development inputs, outside the registration (no
    freeze, own ledger under ``out_dir``). The report is labelled REHEARSAL."""
    out_dir = Path(out_dir)
    led = out_dir / "ledger.jsonl"
    if led.exists():
        led.unlink()
    stamp = dict(design_sha256=sha256_json(design), inputs_sha256=sha256_json(inputs),
                 freeze_code_sha256=_code(name)[0])
    return execute(name, design, inputs, out_dir=out_dir, ledger_path=led, stamp=stamp, rehearsal=True, **kwargs)


def _runner_markdown(name: str) -> Optional[Callable]:
    mod = REGISTRY[name]["runner"].split(":")[0]
    return getattr(importlib.import_module(mod), "markdown", None)
