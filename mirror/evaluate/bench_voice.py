"""Spoken-correction benchmark.

Inputs (frozen in the design): the 44 frozen and 28 held-out correction
sentences, synthesised to speech with two fixed voices by a non-Meta
text-to-speech engine (disclosed test audio), 16 kHz mono 16-bit WAV; plus a
mild-noise copy of every file (pink noise, fixed seed, fixed SNR, mixed here).

Measured run: each file is transcribed once by the speech model; the
transcript goes through the same correction compiler and scorer as the text
baseline (the sentence itself, run in the same session with the same
recordings). Metrics: word error rate against the sentence; exact operation
match for audio vs text, paired per sentence, voice and condition.
"""
from __future__ import annotations

import io
import json
import re
import time
import wave
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from . import stats

SAMPLE_RATE = 16_000
NOISE_SNR_DB = 15.0
NOISE_SEED = 20261031


# ----------------------------------------------------------------- audio io ---

def wav_bytes(pcm16: np.ndarray, sr: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.asarray(pcm16, dtype="<i2").tobytes())
    return buf.getvalue()


def read_wav(data: bytes | str | Path) -> tuple[np.ndarray, int]:
    src = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else open(data, "rb")
    with src, wave.open(src, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("expected 16-bit mono WAV")
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").copy(), wf.getframerate()


def pink_noise(n: int, seed: int) -> np.ndarray:
    """Unit-RMS 1/f noise, deterministic for (n, seed)."""
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(spec.size, dtype=float)
    f[0] = 1.0
    x = np.fft.irfft(spec / np.sqrt(f), n)
    return x / np.sqrt(np.mean(x ** 2))


def mix_snr(clean: np.ndarray, seed: int, snr_db: float = NOISE_SNR_DB) -> np.ndarray:
    """Add pink noise at ``snr_db`` relative to the clean signal's RMS over
    its non-silent samples; returns int16."""
    x = clean.astype(np.float64)
    active = np.abs(x) > 0.02 * (np.max(np.abs(x)) or 1.0)
    rms = np.sqrt(np.mean(x[active] ** 2)) if active.any() else 0.0
    noise = pink_noise(x.size, seed) * rms / (10 ** (snr_db / 20.0))
    return np.clip(np.round(x + noise), -32768, 32767).astype(np.int16)


def noise_seed(case_id: str, voice_key: str) -> int:
    import hashlib
    h = hashlib.sha256(f"{NOISE_SEED}:{case_id}:{voice_key}".encode()).hexdigest()
    return int(h[:8], 16)


# ----------------------------------------------------------------- synthesis ---

def elevenlabs_synthesizer(*, base_url: str = "https://api.elevenlabs.io", headers: Optional[dict] = None,
                           model_id: str = "eleven_v4_turbo", seed: int = 7, timeout: float = 60.0
                           ) -> Callable[[str, str], tuple[np.ndarray, dict]]:
    """``synth(text, voice_id) -> (int16 samples at 16 kHz, provenance)``.
    Without ``headers`` the key is read from ELEVENLABS_API_KEY."""
    import os
    import httpx
    hdrs = dict(headers or {"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")})

    def synth(text: str, voice_id: str) -> tuple[np.ndarray, dict]:
        r = httpx.post(f"{base_url.rstrip('/')}/v1/text-to-speech/{voice_id}", headers=hdrs,
                       params={"output_format": "pcm_16000"},
                       json={"text": text, "model_id": model_id, "seed": seed},
                       follow_redirects=False, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"text-to-speech HTTP {r.status_code}: {r.text[:200]}")
        pcm = np.frombuffer(r.content, dtype="<i2").copy()
        return pcm, {"request_id": r.headers.get("request-id") or r.headers.get("x-request-id"),
                     "model_id": model_id, "seed": seed}
    return synth


def sentences() -> list[dict]:
    from mirror.interpret.benchmark.score import BENCH_DIR, load_cases
    out = []
    for fname, group in (("cases.json", "frozen"), ("cases_holdout.json", "holdout")):
        doc = load_cases(BENCH_DIR / fname)
        for c in doc["cases"]:
            out.append(dict(id=c["id"], set=group, text=c["text"], category=c["category"],
                            gold_outcome=c["gold"]["outcome"], cases_sha256=doc["sha256"]))
    return out


def prepare_inputs(out_dir: Path, voices: list[dict], synth: Callable, *, repo_root: Path,
                   pause_s: float = 0.0, sents: Optional[list[dict]] = None) -> list[dict]:
    """Synthesise every sentence with every voice (clean) and mix a noisy
    copy. Returns the input records (path relative to repo, sha256, ...)."""
    import hashlib
    out_dir = Path(out_dir)
    recs = []
    for s in (sents if sents is not None else sentences()):
        for v in voices:
            clean_p = out_dir / "clean" / v["key"] / f"{s['id']}.wav"
            noisy_p = out_dir / "noise15db" / v["key"] / f"{s['id']}.wav"
            if clean_p.exists():
                pcm, _ = read_wav(clean_p)
                prov = json.loads((clean_p.with_suffix(".json")).read_text())
            else:
                pcm, prov = synth(s["text"], v["voice_id"])
                clean_p.parent.mkdir(parents=True, exist_ok=True)
                clean_p.write_bytes(wav_bytes(pcm))
                clean_p.with_suffix(".json").write_text(json.dumps(prov, sort_keys=True) + "\n")
                time.sleep(pause_s)
            seed = noise_seed(s["id"], v["key"])
            noisy_p.parent.mkdir(parents=True, exist_ok=True)
            noisy_p.write_bytes(wav_bytes(mix_snr(pcm, seed)))
            for cond, p in (("clean", clean_p), ("noise15db", noisy_p)):
                data = p.read_bytes()
                try:
                    rel = p.resolve().relative_to(repo_root).as_posix()
                except ValueError:
                    rel = str(p.resolve())
                recs.append(dict(case=s["id"], set=s.get("set"), voice=v["key"], condition=cond, path=rel,
                                 sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                                 duration_s=round(pcm.size / SAMPLE_RATE, 3),
                                 noise_seed=seed if cond != "clean" else None,
                                 tts_request_id=prov.get("request_id")))
    return recs


def verify_noisy(rec_clean: dict, rec_noisy: dict, repo_root: Path) -> bool:
    """The noisy file is exactly the frozen mix of the clean one."""
    pcm, _ = read_wav(repo_root / rec_clean["path"])
    return wav_bytes(mix_snr(pcm, rec_noisy["noise_seed"])) == (repo_root / rec_noisy["path"]).read_bytes()


# ----------------------------------------------------------------- metrics ---

_NUM = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
        "7": "seven", "8": "eight", "9": "nine", "10": "ten", "15": "fifteen", "20": "twenty",
        "30": "thirty", "45": "forty five", "90": "ninety"}


def normalize(text: str) -> list[str]:
    """Lower case, digits spelled for the small numbers that occur, units and
    punctuation removed. Applied identically to reference and hypothesis."""
    t = text.lower().replace("°", " degrees ").replace("%", " percent ")
    t = re.sub(r"(\d+)\s*(cm|mm)\b", lambda m: m.group(1) + " " + {"cm": "centimeters", "mm": "millimeters"}[m.group(2)], t)
    t = re.sub(r"(?<=\d)\.(?=\d)", " point ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t).replace("'", "")
    words = []
    for w in t.split():
        words += _NUM.get(w, w).split()
    return words


def wer(reference: str, hypothesis: str) -> dict:
    r, h = normalize(reference), normalize(hypothesis)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=int)
    d[:, 0] = np.arange(len(r) + 1)
    d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    errors = int(d[len(r), len(h)])
    return dict(errors=errors, ref_words=len(r), wer=errors / max(len(r), 1))


# --------------------------------------------------------------------- run ---

def run(design: dict, inputs: dict, *, ledger, budget, out_dir: Path, transcribe: Optional[Callable] = None,
        client=None, meta_client=None, scene=None, base_spec=None, repo_root: Optional[Path] = None) -> dict:
    """One measured pass. ``transcribe(path) -> object with .text`` (default:
    mirror.interpret.voice.transcribe with one shared Meta client);
    ``client`` is the correction compiler's model client (default: from the
    environment, Muse Spark, recordings in the design's cassette directory)."""
    from mirror.interpret.benchmark.score import BENCH_DIR, load_base_spec, load_cases, run_case, score_case, state_of
    from . import costs
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    if transcribe is None:
        from mirror.interpret.voice import transcribe as _tr
        if meta_client is None:
            from mirror.meta import MetaClient
            meta_client = MetaClient.from_env()
        transcribe = lambda p: _tr(p, client=meta_client)  # noqa: E731
    if client is None:
        from mirror.interpret import LLMClient
        cd = Path(design["correction"]["cassette_dir"])
        client = LLMClient.from_env(cassette_dir=cd if cd.is_absolute() else repo_root / cd)
    if scene is None:
        from mirror.scenes import reference_scene
        scene = reference_scene(design["correction"]["scene"])
    base = base_spec or load_base_spec()
    base_state = state_of(base)
    cases = {s["id"]: s for s in design["sentences"]}
    gold = {}
    for fname in ("cases.json", "cases_holdout.json"):
        for c in load_cases(BENCH_DIR / fname)["cases"]:
            gold[c["id"]] = c
    for cid, s in cases.items():              # development sentences carry their own gold
        if "gold" in s:
            gold[cid] = dict(id=cid, text=s["text"], gold=s["gold"])
    text_rows = {}
    for cid, s in cases.items():
        res = run_case(base, scene, s["text"], client)
        res["score"] = score_case(gold[cid], res, base_state)
        text_rows[cid] = res
        ledger.append(kind="text_baseline", case=cid, outcome=res["outcome"], exact=res["score"]["exact"])
    rows = []
    for rec in inputs["files"]:
        budget.charge("muse-voice-transcribe-1.0")
        t0 = time.monotonic()
        try:
            tr = transcribe(repo_root / rec["path"])
            text, err = getattr(tr, "text", str(tr)), None
            asr_model, replayed = getattr(tr, "model", None), getattr(tr, "from_cassette", None)
        except Exception as exc:  # every attempt is reported
            text, err, asr_model, replayed = "", f"{type(exc).__name__}: {exc}"[:300], None, None
        lat = round(time.monotonic() - t0, 3)
        w = wer(cases[rec["case"]]["text"], text)
        res = run_case(base, scene, text, client) if text else {"outcome": "error", "error": err or "empty transcript"}
        res["score"] = score_case(gold[rec["case"]], res, base_state)
        row = dict(case=rec["case"], set=rec.get("set"), voice=rec["voice"], condition=rec["condition"],
                   input_sha256=rec["sha256"], reference=cases[rec["case"]]["text"], transcript=text,
                   asr_model=asr_model, asr_replayed=replayed, asr_error=err, asr_latency_s=lat, **w,
                   outcome=res["outcome"], exact=res["score"]["exact"],
                   text_outcome=text_rows[rec["case"]]["outcome"], text_exact=text_rows[rec["case"]]["score"]["exact"])
        ledger.append(kind="transcribe", case=rec["case"], voice=rec["voice"], condition=rec["condition"],
                      input_sha256=rec["sha256"], asr_model=asr_model, error=err, wer=round(w["wer"], 4),
                      outcome=res["outcome"], exact=row["exact"])
        rows.append(row)
    usage = costs.summarize_usage(list(getattr(meta_client, "usage", []) or []) + list(getattr(client, "usage", []) or []))
    return dict(text_baseline={k: {"outcome": v["outcome"], "exact": v["score"]["exact"]} for k, v in text_rows.items()},
                rows=rows, usage=usage, cost_usd=costs.total_cost(usage), summary=summarize(rows, text_rows, design))


def summarize(rows: list[dict], text_rows: dict, design: dict) -> dict:
    out = {}
    groups = {}
    for r in rows:
        groups.setdefault((r["condition"], r["voice"]), []).append(r)
        groups.setdefault((r["condition"], "all"), []).append(r)
    for (cond, voice), rs in sorted(groups.items()):
        errs = sum(r["errors"] for r in rs)
        refs = sum(r["ref_words"] for r in rs)
        a = [r["exact"] for r in rs]
        t = [r["text_exact"] for r in rs]
        b = sum(1 for x, y in zip(t, a) if x and not y)      # text right, audio wrong
        c = sum(1 for x, y in zip(t, a) if y and not x)
        out[f"{cond}/{voice}"] = dict(
            n=len(rs), corpus_wer=errs / max(refs, 1),
            median_wer=float(np.median([r["wer"] for r in rs])) if rs else None,
            sentences_with_errors=sum(1 for r in rs if r["errors"]),
            asr_failures=sum(1 for r in rs if r["asr_error"]),
            audio_exact=sum(a), text_exact=sum(t), text_right_audio_wrong=b, audio_right_text_wrong=c,
            mcnemar_p=stats.mcnemar_exact(b, c),
            audio_exact_rate=sum(a) / len(rs), text_exact_rate=sum(t) / len(rs))
    th = design.get("thresholds", {})
    checks = {}
    for cond in ("clean", "noise15db"):
        s = out.get(f"{cond}/all")
        if not s:
            continue
        lim = th.get(f"wer_max_{cond}")
        if lim is not None:
            checks[f"corpus WER {cond} <= {lim}"] = s["corpus_wer"] <= lim
        drop = th.get(f"exact_drop_max_pp_{cond}")
        if drop is not None:
            checks[f"audio exact >= text exact - {drop} pp ({cond})"] = \
                (s["text_exact_rate"] - s["audio_exact_rate"]) * 100 <= drop
    return dict(groups=out, threshold_checks=checks)


def markdown(report: dict, design: dict) -> str:
    from . import costs
    res = report.get("result", {})
    L = [f"# {report.get('title', 'Spoken-correction benchmark (pre-registered)')}", ""]
    if report.get("rehearsal"):
        L += ["> REHEARSAL on development sentences and a development voice. Not an evaluation result.", ""]
    L += [f"Status: {report['status']}. Design `{report['design_sha256'][:16]}`, inputs `{report['inputs_sha256'][:16]}`, "
          f"code `{report['freeze_code_sha256'][:16]}`.", "", f"Speech used: {design['tts']['disclosure']}", ""]
    if "summary" not in res:
        return "\n".join(L + [f"Stopped: {res.get('stopped', '')}", ""])
    L += ["## What was measured", "",
          "Each recorded sentence was transcribed once by the speech model. The transcript then went through the same "
          "correction compiler as the sentence typed as text, and both results were scored against the expected "
          "change to the task. Word error rate compares the transcript with the sentence after the same normalisation "
          "(case, punctuation, small numbers spelled out).", "",
          "| condition / voice | recordings | word error rate (corpus) | median per sentence | right from audio "
          "| right from text | text right, audio wrong | audio right, text wrong | McNemar p | transcription failures |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in res["summary"]["groups"].items():
        L.append(f"| {k} | {s['n']} | {s['corpus_wer']:.3f} | {s['median_wer']:.3f} | {s['audio_exact']} | "
                 f"{s['text_exact']} | {s['text_right_audio_wrong']} | {s['audio_right_text_wrong']} | "
                 f"{s['mcnemar_p']:.3g} | {s['asr_failures']} |")
    L += ["", "## Pre-stated checks", ""]
    L += [f"- {k}: {'met' if v else 'not met'}" for k, v in res["summary"]["threshold_checks"].items()]
    L += ["", "Hypotheses (written in the design before any transcription):", ""] + [f"- {h}" for h in design.get("hypotheses", [])]
    bad = [r for r in res["rows"] if not r["exact"] and r["text_exact"]]
    L += ["", f"## Right as text, wrong from audio ({len(bad)})", ""]
    L += [f"- {r['case']}, {r['voice']}, {r['condition']}: word error rate {r['wer']:.2f}. Said: \"{r['reference']}\" "
          f"Heard: \"{r['transcript'][:200]}\" Result: {r['outcome']}." for r in bad] or ["none"]
    L += ["", "## Spend", "", "| model | network calls | replayed | input tokens | output tokens | median latency | cost |",
          "|---|---|---|---|---|---|---|"]
    for m, u in res.get("usage", {}).items():
        lat = "n/a" if u["latency_s_median"] is None else f"{u['latency_s_median']:.1f} s"
        L.append(f"| {m} | {u['network_calls']} | {u['replayed_calls']} | {u['input_tokens']} | {u['output_tokens']} | "
                 f"{lat} | ${u['cost_usd']:.3f} |")
    L += ["", f"Total ${res.get('cost_usd', 0):.3f} at list prices ({costs.PRICES_SOURCE}).", "",
          "## Every recording", "", "| sentence | voice | condition | WER | heard | from audio | from text |", "|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        heard = (r["transcript"] or ("(error: " + (r["asr_error"] or "") + ")")).replace("|", "/")[:140]
        L.append(f"| {r['case']} | {r['voice']} | {r['condition']} | {r['wer']:.2f} | {heard} | "
                 f"{r['outcome']}{' (right)' if r['exact'] else ''} | {r['text_outcome']}{' (right)' if r['text_exact'] else ''} |")
    L += ["", f"Budget lines: {json.dumps(report['budget'])}. Every attempt is in ledger.jsonl ({report['ledger_entries']} entries). "
          "No recording was excluded.", ""]
    return "\n".join(L)
