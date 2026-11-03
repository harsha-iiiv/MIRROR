// Plain wording for where a demonstration came from. Perception may supply its own wording
// (demo.provenance.label / demo.provenance.description, or video_demo.media.kind); otherwise
// the wording is derived from the fields the snapshot already carries. Never claims a person
// or a real camera unless the data says so.
// Pure functions: no DOM, no network.

const KIND_TEXT = {
  rendered_simulation: { short: 'Rendered simulator video, tracked from pixels',
    long: 'a video rendered from the simulator; the objects were tracked in its pixels and grounded to simulator objects. It is not footage of a person or from a real camera' },
  camera: { short: 'Camera footage, tracked from pixels',
    long: 'camera footage; the objects were tracked in its pixels and grounded to simulator objects using the stated calibration' },
  upload: { short: 'Uploaded video, tracked from pixels',
    long: 'an uploaded video; the objects were tracked in its pixels and grounded to simulator objects, assuming the simulator\'s table and kit geometry' },
};

// What kind of video a video-grounded demonstration came from.
export function videoMediaKind(snap) {
  const vd = snap && snap.video_demo;
  const pv = snap && snap.demo && snap.demo.provenance;
  const k = (vd && vd.media && vd.media.kind) || (pv && pv.media_kind) || (vd && vd.media_kind);
  if (k && KIND_TEXT[k]) return k;
  if (vd && vd.bundled) return 'rendered_simulation';   // the bundled demonstrations are MuJoCo renders
  return 'upload';
}

export function demoSource(snap) {
  const d = snap && snap.demo;
  if (!d) return { short: '', long: 'no demonstration loaded' };
  const pv = d.provenance || {};
  if (pv.label || pv.description) {
    return { short: pv.label || pv.description, long: pv.description || pv.label };
  }
  switch (d.source) {
    case 'teleop_scripted':
      return { short: 'Scripted teleoperation in simulation', long: 'a scripted teleoperation of the simulated gripper: a simulator trace, not a video of a person' };
    case 'teleop_keyboard':
      return { short: 'Keyboard teleoperation in simulation', long: 'keyboard teleoperation of the simulated gripper: a simulator trace, not a video of a person' };
    case 'replay':
      return { short: 'Replayed simulator trace', long: 'a replayed simulator trace, not a video of a person' };
    case 'video_grounded': {
      const t = KIND_TEXT[videoMediaKind(snap)];
      return { short: t.short, long: t.long };
    }
    default:
      return { short: d.source || 'Recorded trace', long: d.source || 'a recorded trace' };
  }
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
export function provenanceText(p) { return PROVENANCE_TEXT[p] || p || 'unknown'; }
// CSS tone for a provenance chip: fresh work, recordings, and stand-ins look different.
export function provenanceTone(p) {
  if (p === 'fresh_model_call' || p === 'simulated' || p === 'live') return 'live';
  if (p === 'offline_interpreter' || p === 'fixture' || p === 'offline') return 'offline';
  return 'recorded';
}
