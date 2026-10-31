"""The recorded fixture calls (one per endpoint). Re-record with
MIRROR_META_MODE=record python -m tests.meta.calls (needs MODEL_API_KEY)."""
from pathlib import Path

from mirror.meta import MetaClient, file_part, text_part

REPO = Path(__file__).resolve().parents[2]
KEYFRAME = REPO / "data" / "cassettes" / "inputs" / "keyframes_reference" / "00.png"
CLIP = REPO / "tests" / "meta" / "fixtures" / "keyframes_clip.mp4"         # 8 frames, 480x270, 2 s
WAV = REPO / "tests" / "meta" / "fixtures" / "correction_espeak.wav"      # espeak-ng, 16 kHz mono

TINY_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
               "required": ["answer"], "additionalProperties": False}
VIDEO_SCHEMA = {"type": "object", "properties": {"objects": {"type": "array", "items": {"type": "string"}},
                                                  "moving": {"type": "array", "items": {"type": "string"}}},
                "required": ["objects", "moving"], "additionalProperties": False}


def text(c: MetaClient):
    return c.responses("muse-spark-1.3", [text_part("Reply with the word ok.")], schema=TINY_SCHEMA)


def sam_image(c: MetaClient):
    return c.sam_segment(KEYFRAME, "blue cube")


def sam_video(c: MetaClient):
    return c.sam_segment(CLIP, "blue cube")


def video(c: MetaClient):
    fid = c.upload_file(CLIP, mime="video/mp4")
    return c.responses("muse-spark-1.3", [
        text_part("This is a short simulator rendering. List the objects you can see and say whether any "
                  "object moves."), file_part(fid)], schema=VIDEO_SCHEMA)


def asr(c: MetaClient):
    return c.asr_transcribe(WAV)


def edit(c: MetaClient):
    return c.images_edit([KEYFRAME], "Make the lighting dimmer, as if at dusk. Keep every object, its "
                                     "position, size and shape unchanged.")


ALL = {"text": text, "sam_image": sam_image, "sam_video": sam_video, "video": video, "asr": asr, "edit": edit}

if __name__ == "__main__":
    import sys
    client = MetaClient.from_env()
    for name in sys.argv[1:] or ALL:
        r = ALL[name](client)
        print(name, "cassette" if getattr(r, "from_cassette", False) else "network")
