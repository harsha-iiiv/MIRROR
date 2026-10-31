"""Designs, input builders and rehearsal material for the registrations in
``mirror.evaluate.prereg`` (kept apart so a registration's code hash covers
only what its harness imports)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from .paired import FreezeError
from .prereg import REPO_ROOT, paths, sha256_file

# ----------------------------------------------------------------- designs ---

VOICES = [dict(key="voice_a", voice_id="EXAVITQu4vr4xnSDxMaL",
               label="ElevenLabs premade voice 'Sarah' (US English, female)"),
          dict(key="voice_b", voice_id="JBFqnCBsd6RMkjVDRZzb",
               label="ElevenLabs premade voice 'George' (British English, male)")]


def inputs_voice(synth: Optional[Callable] = None) -> tuple[list[dict], dict]:
    """Audio files (generated with ``synth`` where missing) and provenance."""
    from . import bench_voice as bv

    def _no_synth(text, voice_id):
        raise FreezeError("audio missing: pass a synthesizer to generate it")
    files = bv.prepare_inputs(paths("voice")["out"] / "inputs", VOICES, synth or _no_synth, repo_root=REPO_ROOT)
    return files, dict(tts="ElevenLabs text-to-speech, eleven_v4_turbo, pcm_16000, seed 7; request ids per file",
                       noise="pink noise 15 dB SNR mixed by bench_voice.mix_snr; re-mixing reproduces each file")


def design_voice() -> dict:
    from . import bench_voice as bv
    from mirror.interpret.benchmark.score import BENCH_DIR
    sents = bv.sentences()
    return dict(
        title="Spoken corrections: muse-voice-transcribe-1.0, then muse-spark-1.3, against the text baseline",
        sentences=[{k: s[k] for k in ("id", "set", "text", "category", "gold_outcome")} for s in sents],
        cases_sha256={"cases.json": sha256_file(BENCH_DIR / "cases.json"),
                      "cases_holdout.json": sha256_file(BENCH_DIR / "cases_holdout.json")},
        voices=VOICES,
        tts=dict(engine="ElevenLabs text-to-speech API", model_id="eleven_v4_turbo", output="pcm_16000 (16 kHz, "
                 "mono, 16-bit) stored as WAV", seed=7,
                 disclosure="Test audio synthesised by a non-Meta speech engine (ElevenLabs eleven_v4_turbo, two "
                            "premade voices). It is clean synthetic speech, not human recordings; results on real "
                            "speakers may differ."),
        noise=dict(condition="noise15db", type="pink (1/f) noise", snr_db=bv.NOISE_SNR_DB,
                   seed_rule=f"first 8 hex of sha256('{bv.NOISE_SEED}:<case>:<voice>')",
                   level="relative to the RMS of samples above 2% of the clean clip's peak",
                   mixed_by="mirror.evaluate.bench_voice.mix_snr (re-mixing reproduces the files byte for byte)"),
        inputs_rule="72 sentences x 2 voices x {clean, noise15db} = 288 WAV files, listed with sha256 in inputs.json",
        models=dict(asr="muse-voice-transcribe-1.0 (/v1/asr/transcribe, PUSH_TO_TALK)",
                    correction="muse-spark-1.3"),
        correction=dict(cassette_dir="data/cassettes_muse/corrections_benchmark", scene="shortcut",
                        base_spec="mirror/interpret/benchmark/base_spec.json",
                        base_spec_sha256=sha256_file(BENCH_DIR / "base_spec.json"),
                        scorer="mirror.interpret.benchmark.score.score_case (exact match)",
                        environment="MIRROR_LLM_MODEL=muse-spark-1.3; recordings served first, live calls only "
                                    "for transcripts never seen before"),
        plan=["Each of the 288 files is sent to the speech model once (the client's own transport retry policy "
              "applies; every attempt is counted against the budget and recorded in the ledger).",
              "The text baseline (each sentence as text) runs in the same session through the same compiler, "
              "recordings and scorer.",
              "WER: word-level edit distance after the frozen normalisation in bench_voice.normalize, corpus "
              "WER = total errors / total reference words, per condition and voice.",
              "Operation accuracy: exact match by score_case; audio vs text paired per sentence, voice and "
              "condition; exact two-sided McNemar on the discordant pairs.",
              "All rows are reported; nothing is filtered by outcome."],
        metrics=["corpus WER and median sentence WER per condition and voice",
                 "exact operation match from audio vs from text (paired), discordant counts, McNemar p",
                 "ASR failures, latency"],
        thresholds=dict(wer_max_clean=0.05, wer_max_noise15db=0.10,
                        exact_drop_max_pp_clean=5, exact_drop_max_pp_noise15db=10),
        hypotheses=["Clean synthetic speech: corpus WER <= 5% and exact operation match from audio within 5 "
                    "percentage points of the text baseline.",
                    "Pink noise at 15 dB SNR: corpus WER <= 10% and exact match within 10 points of the text baseline."],
        budget_caps={"muse-voice-transcribe-1.0": 300},
    )


VIDEO_SEEDS = list(range(70_000, 70_020))
ROBUST_SEEDS = [8600, 8601, 8602]          # development layouts (perception study, no transfer claim)
ROBUST_CAMERAS = ("top", "oblique")
ROBUST_MOMENTS = ("carry",)


def design_video() -> dict:
    return dict(
        title="Video to specification: sam-3.1 tracking, muse-wonder-1.0 vs muse-spark-1.3 observations, "
              "muse-spark-1.3 specification",
        seeds=VIDEO_SEEDS, split="eval", family="mixed", cameras=["top", "side"],
        sam_prompts=["cup", "block", "marker", "tray", "wall"],
        sam_requests_per_video=6,
        models=dict(tracking="sam-3.1", observations=["muse-wonder-1.0", "muse-spark-1.3"],
                    specification="muse-spark-1.3"),
        spec_observations_model="muse-spark-1.3",
        plan=["Each seed: render the scripted demonstration (top + side), track with sam-3.1, rebuild the "
              "table-plane trace, compare with simulator ground truth per frame.",
              "Both observation models see the same videos once each; their events are matched to ground-truth "
              "grasp/release events (same type and object, one-to-one, closest first).",
              "Specification from the video trace (with the muse-spark-1.3 observations) vs specification from "
              "the ground-truth trace, both by muse-spark-1.3; compared by proposal signature (constraints not "
              "rejected: type, phase, selector, parameters; active goals: selector, target).",
              "Every seed is reported, including failures."],
        metrics=["object found rate (detection within 5 cm of ground truth, frames where visible)",
                 "table-plane centroid error (cm): median of per-object medians",
                 "grasp/release detection within 1 s and absolute timing error (s)",
                 "cup tilt error from the side view (deg)",
                 "proposal-signature equality, video vs ground-truth trace",
                 "observation models: event F1 at 0.5 s, relevant-object set correct, care cue for the cup "
                 "when the demonstration kept it upright, latency"],
        thresholds=dict(found_rate_min=0.95, centroid_cm_median_max=1.5, event_detection_min=0.9,
                        timing_s_median_max=0.3, tilt_deg_median_max=5.0, signature_equal_min=18),
        decision_rule=dict(min_f1_gain=0.05,
                           text="use muse-wonder-1.0 for observations only if its mean event F1 is at least "
                                "0.05 above muse-spark-1.3 and it gets the relevant-object set right at least as "
                                "often; otherwise keep muse-spark-1.3"),
        hypotheses=["Tracking from rendered video meets every pre-stated threshold.",
                    "The specification from video has the same proposal signature as the one from the "
                    "ground-truth trace on at least 18 of 20 seeds."],
        budget_caps={"muse-wonder-1.0": 60, "muse-spark-1.3:video": 60, "sam-3.1": 120},
        inputs_rule="for each seed: sample_layout(seed, 'eval'), the scripted demonstration, rendered by "
                    "mirror.perception.render_demo_video (top + side), with its ground truth; all files listed "
                    "with sha256 in inputs.json; no model call",
    )


def design_robustness() -> dict:
    from mirror.robustness import instructions as ins
    payload = ins.frozen_payload()
    n_model = sum(1 for i in ins.INSTRUCTIONS if i.kind == "model")
    n_frames = len(ROBUST_SEEDS) * len(ROBUST_CAMERAS) * len(ROBUST_MOMENTS)
    n_prompts = len(payload["sam_prompts"])
    return dict(
        title="Perception robustness: muse-image-1.0 counterfactual edits (plus local sensor edits), sam-3.1 "
              "re-run, simulator ground-truth masks",
        frames=dict(seeds=ROBUST_SEEDS, split="dev", cameras=list(ROBUST_CAMERAS), moments=list(ROBUST_MOMENTS),
                    count=n_frames, made_by="mirror.robustness.frames.dev_frames (executed reference task, "
                                            "segmentation render for masks)"),
        instructions=payload, instructions_sha256=ins.instructions_sha256(),
        models=dict(edit="muse-image-1.0", segmentation="sam-3.1"),
        plan=[f"{n_frames} ground-truth frames x {len(payload['instructions'])} frozen instructions ({n_model} by "
              f"muse-image-1.0, {len(payload['instructions']) - n_model} local and deterministic).",
              "sam-3.1 runs once per prompt on every original and every edited frame (one noun phrase per call); "
              "the highest-confidence object per prompt is scored.",
              "check_geometry (frozen in mirror.robustness.geometry) labels each edit preserved, changed or "
              "reframed; changed and reframed edits are reported in their own groups, never dropped.",
              "Per object: IoU with the ground-truth mask, centroid error (px), found or not. Main measure: "
              "IoU change from the original frame to the edited one, over edits that preserve geometry.",
              "Every frame x instruction pair is reported, including failures."],
        metrics=["median IoU change (edited minus original), preserved-geometry edits",
                 "found rate on edited frames", "median centroid error (px, at 1024 x 576)",
                 "per instruction and per family: geometry verdicts and median IoU change"],
        thresholds=dict(delta_iou_median_min=-0.05, found_rate_min=0.9, centroid_px_median_max=4.0),
        hypotheses=["On edits that keep geometry, segmentation degrades little: median IoU change >= -0.05, "
                    "found rate >= 0.9, median centroid error <= 4 px."],
        budget_caps={"muse-image-1.0": n_frames * n_model,
                     "sam-3.1": n_frames * (1 + len(payload["instructions"])) * n_prompts},
        list_source="instruction list and SAM prompts from the plan track (mirror/robustness/instructions.py, "
                    "sha256 above); frame selection proposed by eval",
        inputs_rule="the ground-truth frames (PNG, masks .npz, metadata JSON) rendered before the freeze and "
                    "listed with sha256 in inputs.json; no model call",
    )


def inputs_robustness() -> tuple[list[dict], dict]:
    from mirror.robustness.frames import dev_frames
    out = paths("robustness")["out"] / "inputs" / "frames"
    frames = dev_frames(ROBUST_SEEDS, cameras=ROBUST_CAMERAS, moments=ROBUST_MOMENTS, out_dir=out)
    files = []
    for g in frames:
        for suffix in (".png", "_masks.npz", ".json"):
            fp = out / f"{g.frame_id}{suffix}"
            files.append(dict(frame=g.frame_id, path=fp.resolve().relative_to(REPO_ROOT).as_posix(),
                              sha256=sha256_file(fp)))
    return files, dict(frames=[g.frame_id for g in frames], renderer="mujoco offscreen, opaque walls")


def inputs_video() -> tuple[list[dict], dict]:
    from mirror.perception import render_demo_video
    from mirror.scenes import sample_layout
    os.environ["MIRROR_EVAL_LEDGER"] = str(paths("video")["ledger"])
    out = paths("video")["out"] / "inputs"
    files, demos = [], []
    for seed in VIDEO_SEEDS:
        scene = sample_layout(seed, "eval")
        man, dv = render_demo_video(scene, out / f"seed{seed}", cams=("top", "side"))
        demos.append(dict(seed=seed, manifest=man.resolve().relative_to(REPO_ROOT).as_posix(), demo_id=dv.demo_id))
        for fp in sorted(man.parent.iterdir()):
            if fp.is_file():
                files.append(dict(seed=seed, path=fp.resolve().relative_to(REPO_ROOT).as_posix(),
                                  sha256=sha256_file(fp)))
    return files, dict(demos=demos, renderer="mirror.perception.render_demo_video (top, side)")


INPUTS = {"voice": inputs_voice, "video": inputs_video, "robustness": inputs_robustness}


# --------------------------------------------------------------- rehearsal ---
# Development material only: sentences not in either benchmark set, a third
# voice, a development layout and a development frame.

REHEARSAL_DIR = REPO_ROOT / "results" / "tmp"
REHEARSAL_VOICE = dict(key="voice_dev", voice_id="pNInz6obpgDQGcFmaJgB",
                       label="ElevenLabs premade voice 'Adam' (development only)")
_UPRIGHT_GOLD = {"outcome": "apply", "enforced": [{"id": "c_upright_final_cup", "params": {"max_tilt_deg": 15.0}},
                                                  {"id": "c_upright_transport_cup", "params": {"max_tilt_deg": 15.0}}],
                 "ordering": [], "inactive_goals": []}
_FINAL = [{"id": "c_upright_final_cup", "params": {"max_tilt_deg": 15.0}}]
REHEARSAL_SENTENCES = [
    dict(id="d01", set="dev", category="paraphrase", text="Please keep the cup upright for the whole trip to the "
         "tray, not only once it has arrived.", gold=_UPRIGHT_GOLD),
    dict(id="d02", set="dev", category="ordering", text="Put the block in the tray before the cup goes in.",
         gold={"outcome": "apply", "enforced": _FINAL, "ordering": ["o_block_before_cup"], "inactive_goals": []}),
    dict(id="d03", set="dev", category="negation", text="There is no need to pack the marker this time.",
         gold={"outcome": "apply", "enforced": _FINAL, "ordering": [], "inactive_goals": ["g_inside_marker"]}),
    dict(id="d04", set="dev", category="ambiguous", text="Keep this thing level.", gold={"outcome": "asks"}),
]
REHEARSAL_VIDEO_SEED = 8800
REHEARSAL_FRAME_SEED = 8700
REHEARSAL_INSTRUCTIONS = ("light_dim", "noise_gauss", "blur_gauss", "blur_motion")


def rehearsal_setup(name: str, synth: Optional[Callable] = None) -> tuple[dict, dict, Path]:
    """(design, inputs, out_dir) for a rehearsal on development material."""
    out = REHEARSAL_DIR / f"rehearsal_{name}"
    if name == "voice":
        from . import bench_voice as bv

        def _no_synth(text, voice_id):
            raise FreezeError("rehearsal audio missing: pass a synthesizer")
        design = design_voice()
        design.update(sentences=[{k: s[k] for k in ("id", "set", "text", "category", "gold")} for s in REHEARSAL_SENTENCES],
                      voices=[REHEARSAL_VOICE], budget_caps={"muse-voice-transcribe-1.0": 2 * len(REHEARSAL_SENTENCES)},
                      correction=dict(design["correction"], cassette_dir=str(out / "cassettes_correction")))
        files = bv.prepare_inputs(out / "inputs", [REHEARSAL_VOICE], synth or _no_synth, repo_root=REPO_ROOT,
                                  sents=REHEARSAL_SENTENCES)
        return design, dict(files=files, provenance={}), out
    if name == "robustness":
        from mirror.robustness.frames import dev_frames, load_frames
        design = design_robustness()
        keep = set(REHEARSAL_INSTRUCTIONS)
        design["instructions"] = dict(design["instructions"],
                                      instructions=[i for i in design["instructions"]["instructions"] if i["id"] in keep])
        design["frames"] = dict(design["frames"], seeds=[REHEARSAL_FRAME_SEED], cameras=["top"], count=1)
        design["budget_caps"] = {"muse-image-1.0": 1, "sam-3.1": 3 * (1 + len(keep))}
        fdir = out / "inputs" / "frames"
        frames = load_frames(fdir) if fdir.exists() and any(fdir.glob("*.json")) else \
            dev_frames([REHEARSAL_FRAME_SEED], cameras=("top",), moments=("carry",), out_dir=fdir)
        files = [dict(frame=g.frame_id, path=str(fdir / f"{g.frame_id}.png"), sha256=sha256_file(fdir / f"{g.frame_id}.png"))
                 for g in frames]
        return design, dict(files=files, provenance=dict(frames=[g.frame_id for g in frames])), out
    if name == "video":
        from mirror.perception import render_demo_video
        design = design_video()
        design.update(seeds=[REHEARSAL_VIDEO_SEED], split="dev",
                      budget_caps={"muse-wonder-1.0": 1, "muse-spark-1.3:video": 1, "sam-3.1": 0})
        vdir = out / "inputs" / f"seed{REHEARSAL_VIDEO_SEED}"
        mans = sorted(vdir.glob("*/video.json"))
        man = mans[0] if mans else render_demo_video(REHEARSAL_VIDEO_SEED, vdir)[0]
        return design, dict(files=[], provenance=dict(demos=[dict(seed=REHEARSAL_VIDEO_SEED, manifest=str(man))])), out
    raise KeyError(name)


DESIGNS = {"voice": design_voice, "video": design_video, "robustness": design_robustness}
