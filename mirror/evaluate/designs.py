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
ROBUST_SEEDS = list(range(80000, 80008))    # new seeds, one carry frame each
SCORED_OBJECTS = ("cup", "block", "marker")
ROBUST_FIXED_PROMPTS = {"cup": "open box", "block": "small cube", "marker": "cylinder"}


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
    n_ins = len(payload["instructions"])
    k = len(ROBUST_SEEDS)
    return dict(
        title="Perception robustness: muse-image-1.0 counterfactual edits (plus local sensor edits), sam-3.1 under "
              "two prompt conditions, simulator ground-truth masks",
        frames=dict(seeds=ROBUST_SEEDS, split="eval", count=k, per_seed=1, moment="carry",
                    cameras="top for even positions in the seed list, oblique for odd; a seed whose top frame leaves "
                            "the cup, block or marker under frames_for_seeds' visibility minimum (ground truth only) "
                            "uses its oblique frame instead; every fallback is listed in inputs.json",
                    made_by="mirror.robustness.frames.frames_for_seeds (executed reference task on "
                            "sample_layout(seed, 'eval'), segmentation render for masks), run at the inputs stage"),
        instructions=payload, instructions_sha256=ins.instructions_sha256(),
        scored_objects=list(SCORED_OBJECTS),
        prompt_conditions=dict(
            A_chooser=dict(procedure="mirror.interpret.sam_prompts.choose_prompts(original frame, 'muse-spark-1.3', "
                                     "kinds=(cup, block, marker), retry=True, verify=False) once per original frame; "
                                     "its accepted phrases are reused for every edit of that frame; an object "
                                     "without an accepted phrase counts as not found"),
            B_fixed=dict(prompts=dict(ROBUST_FIXED_PROMPTS),
                         note="colour-free phrases from the plan track, chosen on development frames: 'cube' matched up "
                              "to 5 objects in a top view, 'small cube' ranked the block first in both frames tried; "
                              "for the cup, 'open box' and 'small box' both find it but can rank the tray or a box "
                              "obstacle first (in a one-frame rehearsal 'open box' matched the tray); the tray has no "
                              "ground-truth mask and is not scored")),
        correction=dict(method="mirror.robustness.geometry.check_geometry fits one whole-frame similarity (scale, "
                               "shift) per model edit, the editor's drift, and undoes it before segmentation; "
                               "uncorrected = the edit only resampled to the source size",
                        primary="corrected", uncorrected_for=["A"],
                        also_reported="uncorrected scores for condition A (both would pass the SAM cap), and every "
                                      "per-edit correction"),
        measured_path="mirror.robustness.evaluate_robustness (the plan track's function, the same path as its "
                      "development rehearsal) with conditions A (chooser) and B (fixed), wrapped so that every model "
                      "request is charged to the caps below; geometry verdicts (kept / weakened / moved / resized / "
                      "missing / untested) as defined there",
        local_edits="geometry kept by construction (the same pixels through a known kernel); no correction",
        edit_format="webp",
        models=dict(edit="muse-image-1.0", segmentation="sam-3.1", phrases="muse-spark-1.3"),
        plan=[f"{k} ground-truth frames (one per new seed) x {n_ins} frozen instructions ({n_model} by muse-image-1.0, "
              f"{n_ins - n_model} local and deterministic): {k * n_model} model edits.",
              "sam-3.1 runs once per phrase on every original and edited frame, under both prompt conditions; its top "
              "match is scored against the true visible-pixel mask (the best of all matches is also recorded).",
              "Geometry of each model edit is judged after the whole-frame correction: kept, changed (an object moved, "
              "resized or missing) or reframed (no correction brings it into register). Changed and reframed edits "
              "are reported in their own groups.",
              "Every frame, instruction and request is reported, including failures."],
        metrics=["IoU of the sam-3.1 mask with the true mask; change from the unedited frame (same condition)",
                 "found rate (sam-3.1 returned a mask for the object)", "centroid error in pixels at 1024x576",
                 "all of the above corrected (primary) and uncorrected, per group, condition and instruction"],
        thresholds=dict(delta_iou_median_min=-0.05, found_rate_min=0.9, centroid_px_median_max=4.0),
        threshold_scope="objects whose geometry was kept (evaluate_robustness' kept_* summary over all edits: model "
                        "edits judged kept after correction, plus local edits), corrected scores, evaluated and "
                        "reported for conditions A and B separately",
        hypotheses=["On edits that keep geometry, sam-3.1 segmentation is stable: median IoU change >= -0.05, found "
                    "rate >= 0.9 and median centroid error <= 4 px (corrected), under each prompt condition."],
        dev_run_disclosure=("The thresholds were written in the first draft of this design (31 October 2026, before the "
                            "plan track's development run) and are unchanged. That development run (3 development "
                            "frames x 11 instructions, results/robustness_dev) and a one-frame rehearsal of this harness "
                            "were seen before the design was finalised; the frames, prompt conditions and correction "
                            "rule were chosen after seeing them, the thresholds were not."),
        budget_caps={"muse-image-1.0": 80, "sam-3.1": 850, "muse-spark-1.3:chooser": 40},
        inputs_rule="eval samples each seed's layout into this registration's ledger and calls frames_for_seeds; "
                    "PNG frames and masks listed with sha256 in inputs.json; no model call",
    )


def robust_camera(index: int) -> str:
    return ("top", "oblique")[index % 2]


def inputs_robustness() -> tuple[list[dict], dict]:
    """One carry frame per new seed from the plan track's frames_for_seeds,
    with this registration's ledger active (every eval layout it samples is
    ledgered). Camera: top for even positions, oblique for odd; when the top
    frame leaves the cup, block or marker under the visibility minimum (ground
    truth only, no model), that seed uses the oblique frame instead. Every file
    is frozen by sha256."""
    import json as _json
    import shutil
    from mirror.robustness.frames import FramesNotVisible, frames_for_seeds
    os.environ["MIRROR_EVAL_LEDGER"] = str(paths("robustness")["ledger"])
    out = paths("robustness")["out"] / "inputs" / "frames"
    stage = paths("robustness")["out"] / "inputs" / "manifests"
    out.mkdir(parents=True, exist_ok=True)
    stage.mkdir(parents=True, exist_ok=True)
    chosen, fallbacks = [], []
    for i, seed in enumerate(ROBUST_SEEDS):
        cam = robust_camera(i)
        tmp = stage / f"seed{seed}_{cam}"
        try:
            man = frames_for_seeds([seed], tmp, cameras=(cam,))
        except FramesNotVisible as exc:
            if cam != "top":
                raise
            fallbacks.append(dict(seed=seed, from_camera="top", reason=str(exc)[:300]))
            tmp = stage / f"seed{seed}_oblique"
            man = frames_for_seeds([seed], tmp, cameras=("oblique",))
        (entry,) = man["frames"]
        for name in (entry["png"], entry["masks"], entry["frame_id"] + ".json"):
            shutil.move(str(tmp / name), str(out / name))
        chosen.append(dict(seed=seed, frame=entry["frame_id"], camera=entry["camera"],
                           visible_pixels=entry["visible_pixels"]))
    files = [dict(path=fp.resolve().relative_to(REPO_ROOT).as_posix(), sha256=sha256_file(fp))
             for fp in sorted(out.iterdir()) if fp.is_file()]
    files.sort(key=lambda f: (not f["path"].endswith(".png"), f["path"]))      # a frame PNG first (frames dir)
    files += [dict(path=fp.resolve().relative_to(REPO_ROOT).as_posix(), sha256=sha256_file(fp))
              for fp in sorted(stage.rglob("*")) if fp.is_file()]
    return files, dict(frames=[c["frame"] for c in chosen], chosen=chosen, top_fallbacks=fallbacks,
                       renderer="mirror.robustness.frames.frames_for_seeds (mujoco offscreen, opaque walls)")


def inputs_video(from_dir: Optional[Path] = None) -> tuple[list[dict], dict]:
    """Rendered demonstrations for the video seeds, each with its frozen
    SAM_FPS tracking copies. With ``from_dir`` (renders made elsewhere, e.g.
    on another machine with pinned versions) the demos are copied in and each
    scene is checked against sample_layout(seed, 'eval') here."""
    import json as _json
    import shutil
    from mirror.perception import load_demo_video, render_demo_video
    from mirror.scenes import sample_layout
    from .bench_video import make_sam_copies
    os.environ["MIRROR_EVAL_LEDGER"] = str(paths("video")["ledger"])
    out = paths("video")["out"] / "inputs"
    files, demos, render_info = [], [], {}
    staged = Path(from_dir).parent / "scenes" if from_dir is not None else None
    for seed in VIDEO_SEEDS:
        sf = staged / f"{seed}.json" if staged is not None else None
        if sf is not None and sf.exists():      # the layout sampled (and ledgered) when the renders were staged
            from mirror.contracts import SceneState
            scene = SceneState.model_validate_json(sf.read_text())
        else:
            scene = sample_layout(seed, "eval")
        dst = out / f"seed{seed}"
        if from_dir is not None:
            src = sorted(Path(from_dir).glob(f"seed{seed}/*/video.json"))
            if len(src) != 1:
                raise FreezeError(f"expected one rendered demo for seed {seed} under {from_dir}, found {len(src)}")
            shutil.copytree(src[0].parent, dst / src[0].parent.name, dirs_exist_ok=False)
            man = dst / src[0].parent.name / "video.json"
            rendered = _json.loads((man.parent / "scene.json").read_text())
            if rendered != _json.loads(scene.model_dump_json()):
                raise FreezeError(f"seed {seed}: the rendered scene differs from sample_layout(seed, 'eval')")
            vf = Path(from_dir) / "versions.json"
            render_info = _json.loads(vf.read_text()) if vf.exists() else {"note": "no versions.json supplied"}
        else:
            man, _ = render_demo_video(scene, dst, cams=("top", "side"))
            render_info = {"machine": "local"}
        dv, _gt = load_demo_video(man)
        make_sam_copies(dv, man.parent)
        demos.append(dict(seed=seed, manifest=man.resolve().relative_to(REPO_ROOT).as_posix(), demo_id=dv.demo_id,
                          fps=dv.fps, n_frames=dv.n_frames, duration_s=dv.duration_s))
        for fp in sorted(man.parent.iterdir()):
            if fp.is_file():
                files.append(dict(seed=seed, path=fp.resolve().relative_to(REPO_ROOT).as_posix(),
                                  sha256=sha256_file(fp)))
    return files, dict(demos=demos, renderer="mirror.perception.render_demo_video (top, side)",
                       sam_copies="every 3rd frame at 5 fps (mirror.evaluate.bench_video.make_sam_copies)",
                       render_environment=render_info)


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
        design["frames"] = dict(design["frames"], seeds=[REHEARSAL_FRAME_SEED], split="dev", count=1)
        design["budget_caps"] = {"muse-image-1.0": 1, "sam-3.1": 2 * 3 * (1 + len(keep)) + 12,
                                 "muse-spark-1.3:chooser": 2}
        fdir = out / "inputs" / "frames"
        frames = load_frames(fdir) if fdir.exists() and any(fdir.glob("*.json")) else \
            dev_frames([REHEARSAL_FRAME_SEED], cameras=("oblique",), moments=("carry",), out_dir=fdir)
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


# ---------------------------------------------------------- amendments -----

def amendment2_video() -> tuple[dict, str]:
    """(adds, rationale) for results/eval_video/design_amendment_2.json."""
    adds = dict(
        render=dict(
            function="mirror.perception.render_demo_video(scene, out_dir, cams=('top', 'side')) with the scripted "
                     "teleoperation demonstration (same operator as the reference demonstration)",
            width=960, height=540, fps=15, encoding="H.264 yuv420p", cameras=["top", "side"],
            camera_settings="mirror.perception.cameras as frozen: top lookat (0, 0, 0), distance 1.30, azimuth 90, "
                            "elevation -86, vertical field of view 45; side lookat (0, 0, 0.06), distance 1.75, azimuth "
                            "60, elevation -20, vertical field of view 45",
            overlays="none (no text, labels, paths or readouts)",
            machine="an NVIDIA L4 GPU compute box with EGL (image with pinned mujoco 3.17.0, numpy 2.4.6, scipy 1.17.1, "
                    "imageio 2.38.1, pydantic 2.14.2); layouts sampled and ledgered here, rendered there from the saved "
                    "scenes; GPU, driver and package versions in versions.json, copied into inputs.json. On a "
                    "development demonstration, EGL frames differed from software (OSMesa) renders by about 1/255 on "
                    "average (p99 4/255) with identical poses and events."),
        tracking=dict(
            input_fps=5,
            frames="every 3rd frame of each 15 fps render, starting at frame 0, re-encoded once at the inputs stage and "
                   "frozen by sha256 (bench_video.make_sam_copies); the video models watch the full 15 fps top video",
            procedure="mirror.perception.track.track_videos as frozen, on the 5 fps copies: per camera, three "
                      "muse-spark-1.3 grounding calls on frame 0 (full frame for the table; a table crop for boxes and "
                      "the visible ball count; one multi-image call of zoomed crops for cup, block and marker), then "
                      "sam-3.1 box prompts: one video request (cup, block, marker, gripper) and one image request "
                      "(table hypotheses, tray, wall) on frame 0",
            re_seeding="for gaps of at least 2 s in a moving object's track (at most 2 per camera, ranked: the other "
                       "camera also missing it, then length), SAM tracks backwards over the gap from where the object "
                       "is visible again (SAM's own box; no model call), or, when the gap runs to the end of the clip, "
                       "from one muse-spark-1.3 grounding call on the last frame; one SAM video request per re-seed; "
                       "merged only into missing frames and only if the seed mask is 0.3x to 3x the object's earlier "
                       "median area; every re-seed is recorded and reported. Worst case per demonstration: 10 Spark "
                       "calls and 8 SAM requests.",
            reconstruction="mirror.perception reconstruction as frozen (estimate, scene_from_video, trace_from_estimates), "
                           "on the 5 fps tracks; scoring uses the ground-truth frames at the sampled times",
            five_fps_check=("before this amendment, development demonstration 9001 was tracked live at 5 fps ($0.24): "
                            "found rate 0.98, median centroid error cup 0.39 / block 0.58 / marker 0.90 cm, 5 of 6 "
                            "events within 1 s, one re-seed; sim's 15 fps run of the same demonstration: block 2.46 cm, "
                            "5 of 6 within 1 s. Reconstruction from every 3rd frame of the 15 fps tracks gave the same "
                            "centroid errors. Fewer frames carry a cup tilt estimate at 5 fps (59 vs 168 sampled).")),
        observation_runs="one muse-wonder-1.0 and one muse-spark-1.3 observation per seed, no repeats",
        spend_cap_usd=14.0,
        spend_rule=("all model calls of this registration (grounding, SAM, re-seeds, observations, proposals) count "
                    "against the dollar cap. Before each seed: if spent + projected full seed > cap, the seed runs "
                    "without muse-wonder-1.0 when that fits; otherwise the run stops and the remaining seeds are listed "
                    "as not run. Tracking is never dropped. Projection = mean measured cost of earlier full seeds; for "
                    "the first seed, spend_projection_per_seed_usd."),
        spend_projection_per_seed_usd={"sam-3.1": 0.2, "muse-spark-1.3": 0.18, "muse-wonder-1.0": 0.25},
        budget_caps_amended={"muse-wonder-1.0": None, "muse-spark-1.3:video": None, "sam-3.1": None},
        budget_note=("Request counts are bookkeeping only (still counted and reported); the dollar cap is the binding "
                     "limit. This changes a resource limit only; no threshold, metric, seed or decision rule changes."),
        spec_comparison=dict(
            pass_fail="the published strict proposal signature (signature_equal_min = 18 of 20), unchanged",
            reported_only="same structure ignoring numeric limits (items, phases, selectors and parameter names equal; "
                          "numbers may differ), with every numeric difference listed per seed (for example a cup tilt "
                          "limit of 10 vs 15 degrees); not a pass/fail measure, added before any input exists"),
        development_status=("perception was tuned on the reference demonstration and development seeds 9001 and 9002 "
                            "only. On dev 9002 it still misses the marker's grasp and release (3 of 6 events; the "
                            "marker's carry is reconstructed wrongly) and its strict signature differs offline. Nothing "
                            "in the design was loosened after these development results."),
        disclosures=[
            "camera intrinsics known (45 degree vertical field of view); table size known (1.2 x 1.0 m); object, tray "
            "and wall-thickness sizes from the kit catalogue",
            "the camera is assumed to be on the operator's (-y) side of the table, which resolves the table rectangle's "
            "symmetry",
            "the bead count is the number of balls visible to the grounding model, not the true count",
            "the gripper opening is not observed: closed while an object is carried, open otherwise",
            "cup tilt only where the cup is clearly visible in the side view (visibility, occlusion and temporal "
            "consistency gates), from an in-image silhouette fit",
            "the render look was adjusted for segmentation (soft wood grain, dark floor, opaque walls)",
            "perception was tuned on the reference demonstration and dev 9001/9002; evaluation seeds were never "
            "rendered or sampled by the perception track",
            "SAM 3.1 noun-phrase prompts alone found only the blue cube, the table (top) and the wall (side) on these "
            "renders, so muse-spark-1.3 grounding seeds box prompts",
            "static scene parts are segmented once on frame 0 and held",
            "one-view frames: resting objects are placed on their support; carried objects hidden under the gripper "
            "follow it; gripper depth from apparent size",
            "rendered video, not camera footage, rendered on an NVIDIA L4 with EGL"])
    rationale = ("Fixes the settings amendment 1 left pending, from the lead's decisions of 31 October 2026 (15:00, "
                 "17:10 and 17:50 PT) and the perception track's final description, written before any input of this "
                 "registration exists and before any of its layouts is sampled: per-camera box-prompt tracking with "
                 "re-seeding, at 5 fps (checked on dev 9001 first); a $14 cap; one Wonder and one Spark run per seed; a "
                 "reported-only structure comparison next to the unchanged strict signature; rendering on a GPU box. "
                 "Development numbers seen before writing it: reference 0.88 cm and 6/6 events, dev 9001 1.67 cm and "
                 "6/6 (2 s tolerance) with a cup tilt limit of 10 vs 15 degrees in one Spark proposal, dev 9002 3/6 "
                 "events.")
    return adds, rationale
