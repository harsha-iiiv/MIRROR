"""Shared client for the Meta Model API (https://api.meta.ai/v1).

One client covers every Meta model MIRROR uses: the Responses API (Muse Spark,
Muse Wonder, SAM 3.1), the Files API, speech to text (Muse Voice Transcribe)
and image generation/editing (Muse Image). Every call goes through one
cassette mechanism so that recorded runs replay without network access.

    from mirror.meta import MetaClient, text_part, image_part, file_part
    client = MetaClient.from_env()
    res = client.responses("muse-spark-1.3", [text_part("..."), image_part("frame.png")],
                           schema=MyModel.model_json_schema())
    seg = client.sam_segment("demo.mp4", "blue cube")      # -> SamResult
    tr = client.asr_transcribe("correction.wav")            # -> MetaResult (text)
    out = client.images_edit(["frame.png"], "make the room dimmer")  # -> MetaResult (images)
"""
from mirror.meta.cassette import MetaCassetteMiss, request_key
from mirror.meta.client import (DEFAULT_BASE_URL, MetaAuthError, MetaClient, MetaError, MetaOffline,
                                MetaQuotaError, MetaResult, audio_part, file_part, image_part, text_part)
from mirror.meta.sam import SamBox, SamFrame, SamObject, SamResult, parse_sam_output

__all__ = [
    "DEFAULT_BASE_URL", "MetaAuthError", "MetaCassetteMiss", "MetaClient", "MetaError", "MetaOffline",
    "MetaQuotaError", "MetaResult", "SamBox", "SamFrame", "SamObject", "SamResult", "audio_part",
    "file_part", "image_part", "parse_sam_output", "request_key", "text_part",
]
