// Plain wording for where a demonstration came from, keyed on the perception evidence kind
// (demo.evidence.kind; wording mirrors mirror/observability.py ABOUT_WORDING). Older payloads
// without evidence fall back to the trace source. Never claims a person or a real camera
// unless the data says so.
// Pure functions: no DOM, no network.

export const EVIDENCE_WORDING = {
  simulator_ground_truth: {
    short: 'Recorded simulator trace',
    long: 'Recorded simulator trace: gripper and object poses logged by the physics simulator. Not video, and not a person.' },
  rendered_video_reconstruction: {
    short: 'Simulator-rendered video, rebuilt from pixels',
    long: 'Simulator-rendered video reconstructed from pixels: the video was rendered from a simulated demonstration, and the motion was rebuilt from the video frames alone. Not footage of a person.' },
  camera_video_reconstruction: {
    short: 'Camera footage, rebuilt from pixels',
    long: 'Real camera footage reconstructed from pixels, with a calibrated camera and object identities confirmed by a person.' },
  unverified_video_reconstruction: {
    short: 'Video with assumed calibration (unverified)',
    long: 'Video reconstructed with assumed camera calibration; positions, sizes and tilts are unverified.' },
  legacy_video_unannotated: {
    short: 'Video reconstruction (before observability notes)',
    long: 'Video reconstruction recorded before observability annotations; tilt values in it are not evidence.' },
  interface_fixture: {
    short: 'Interface fixture (no physics)',
    long: 'Interface fixture: key-framed motion for development, not a physics simulation.' },
};

export const MODEL_ORIGIN_WORDING = {
  fresh_model_call: 'fresh model call',
  recorded_replay: 'recorded model output (replayed, no network)',
  offline_rules: 'offline rules (deterministic, no model)',
  oracle_ground_truth: 'simulator ground truth (oracle, development only)',
  unknown: 'origin not recorded',
};

const SIM_SOURCES = ['teleop_keyboard', 'teleop_scripted', 'replay'];

export function demoEvidence(snap) {
  const d = snap && snap.demo;
  return (d && d.evidence) || (snap && snap.video_demo && snap.video_demo.evidence) || null;
}

// The evidence kind, with the fallback perception defines for payloads that predate it.
export function evidenceKind(snap) {
  const ev = demoEvidence(snap);
  if (ev && ev.kind && EVIDENCE_WORDING[ev.kind]) return ev.kind;
  const d = snap && snap.demo;
  if (!d) return null;
  if (snap.mode === 'fixture' && d.source === 'video_grounded') return 'interface_fixture';
  if (SIM_SOURCES.includes(d.source)) return 'simulator_ground_truth';
  if (d.source === 'video_grounded') return 'legacy_video_unannotated';
  return null;
}

// What kind of video a video-grounded demonstration came from (for short labels).
export function videoMediaKind(snap) {
  const k = evidenceKind(snap);
  if (k === 'rendered_video_reconstruction') return 'rendered_simulation';
  if (k === 'camera_video_reconstruction') return 'camera';
  if (k === 'legacy_video_unannotated' && snap.video_demo && snap.video_demo.bundled) return 'rendered_simulation';
  return k && k.includes('video') ? 'upload' : null;
}

// A demonstration rebuilt from rendered test clips (phone-capture path) carries SYNTHETIC everywhere.
export function isSyntheticDemo(snap) {
  const g = snap && snap.generation;
  return !!((g && g.calibration && g.calibration.synthetic) || (snap && snap.demo && snap.demo.synthetic)
    || (snap && snap.demo && snap.demo.evidence && snap.demo.evidence.synthetic));
}
export const SYNTHETIC_DEMO_TEXT = 'SYNTHETIC test render: this demonstration was reconstructed from rendered clips, not real footage.';

export function demoSource(snap) {
  const out = demoSource0(snap);
  if (!isSyntheticDemo(snap)) return out;
  return { ...out, synthetic: true, short: 'SYNTHETIC · ' + (out.short || 'phone-capture reconstruction'), long: SYNTHETIC_DEMO_TEXT + ' ' + (out.long || '') };
}

function demoSource0(snap) {
  const d = snap && snap.demo;
  if (!d) return { short: '', long: 'No demonstration loaded.', kind: null };
  const kind = evidenceKind(snap);
  if (!kind) return { short: d.source || 'Recorded trace', long: d.source || 'A recorded trace.', kind };
  // A legacy trace from the bundled demonstration is a simulator render: say so, keep the caveat.
  if (kind === 'legacy_video_unannotated' && snap.video_demo && snap.video_demo.bundled) {
    return { kind, short: 'Simulator-rendered video, rebuilt from pixels',
      long: 'Simulator-rendered video reconstructed from pixels: the video was rendered from a simulated demonstration, and the motion was rebuilt from the video frames alone. Not footage of a person. It was recorded before observability annotations, so tilt values in it are not evidence.' };
  }
  return { kind, ...EVIDENCE_WORDING[kind] };
}

// Items a person must look at; `abstain` blocks a proposal until resolved.
export function reviewItems(snap) {
  const ev = demoEvidence(snap);
  return ((ev && ev.review) || []).filter((r) => r && r.message);
}

// Observation status of an object's tilt at trace frame i ('measured' | 'inferred' |
// 'interpolated' | 'unobserved'), or null when the trace has no annotation.
export function tiltStatusAt(evidence, objectId, i) {
  const spans = evidence && evidence.objects && evidence.objects[objectId] && evidence.objects[objectId].tilt;
  if (!spans || !spans.length) return null;
  for (const s of spans) if (i >= s.start && i <= s.end) return s.status;
  return 'unobserved';
}

// The provenance vocabulary of the studio API, as visitors read it.
export const PROVENANCE_TEXT = {
  recorded: 'recorded (nothing computed now)',
  fresh_model_call: 'fresh model call',
  offline_interpreter: 'offline interpreter (no model)',
  replayed_cassette: 'replayed model response',
  fixture: 'fixture (no physics)',
  simulated: 'simulated now in MuJoCo',
};
export function provenanceText(p) { return PROVENANCE_TEXT[p] || MODEL_ORIGIN_WORDING[p] || p || 'unknown'; }
// CSS tone for a provenance chip: fresh work, recordings, and stand-ins look different.
export function provenanceTone(p) {
  if (p === 'fresh_model_call' || p === 'simulated' || p === 'live') return 'live';
  if (p === 'offline_interpreter' || p === 'offline_rules' || p === 'fixture' || p === 'offline') return 'offline';
  return 'recorded';
}
