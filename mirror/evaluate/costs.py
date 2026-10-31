"""Model spend from client usage records (Meta Model API list prices).

Prices: dev.meta.ai pricing pages, read 31 October 2026, Standard mode,
pay-as-you-go. Only network calls cost money; recorded (cassette) responses
are counted separately at zero cost.
"""
from __future__ import annotations

from typing import Iterable, Optional

import numpy as np

PRICES = {
    "muse-wonder-1.0": dict(input=4.00, cached=0.20, output=20.00),          # USD per 1M tokens
    "muse-spark-1.3": dict(input=1.25, cached=0.15, output=4.25),
    "muse-image-1.0": dict(per_image=0.01),
    "muse-voice-transcribe-1.0": dict(per_hour=0.18),
    "sam-3.1": dict(per_image=0.0025, per_video_frame=0.0002, min_video_frames=50),
}
PRICES_SOURCE = "dev.meta.ai/docs/pricing-rate-limits and dev.meta.ai/products/meta-model-api, read 2026-10-31"
WAV_BYTES_PER_S = 32_000          # 16 kHz mono 16-bit


def _model_key(model: str) -> Optional[str]:
    m = (model or "").lower()
    for k in PRICES:
        if m.startswith(k) or m.replace("_", "-").startswith(k.replace(".", "-")):
            return k
    if m.startswith("sam"):
        return "sam-3.1"
    return None


def record_cost(rec: dict, *, audio_seconds: Optional[float] = None) -> float:
    """Cost of one usage record (MetaClient or LLMClient). Zero if served
    from a recording."""
    if rec.get("from_cassette"):
        return 0.0
    key = _model_key(rec.get("model") or rec.get("requested_model") or "")
    if key is None:
        return 0.0
    p = PRICES[key]
    u = dict(rec.get("usage") or {})
    for k in ("prompt_tokens", "completion_tokens"):          # LLMClient records are flat
        if k in rec and rec[k] is not None:
            u.setdefault({"prompt_tokens": "input_tokens", "completion_tokens": "output_tokens"}[k], rec[k])
    if "input" in p:
        cached = int(((u.get("input_tokens_details") or {}).get("cached_tokens")) or 0)
        inp = int(u.get("input_tokens") or 0)
        out = int(u.get("output_tokens") or 0)
        return ((inp - cached) * p["input"] + cached * p["cached"] + out * p["output"]) / 1e6
    if "per_hour" in p:
        secs = audio_seconds
        if secs is None:
            secs = max(int(u.get("audio_bytes") or 0) - 44, 0) / WAV_BYTES_PER_S
        return secs / 3600.0 * p["per_hour"]
    if key == "sam-3.1":
        frames = u.get("frames_processed") or u.get("video_frames")
        if frames or "video" in (rec.get("name") or rec.get("call") or ""):
            return max(int(frames or 0), p["min_video_frames"]) * p["per_video_frame"]
        return int(u.get("images_processed") or 1) * p["per_image"]
    if "per_image" in p:
        return int(u.get("images") or u.get("n_images") or 1) * p["per_image"]
    return 0.0


def summarize_usage(records: Iterable[dict]) -> dict:
    """Per model: network calls, replayed calls, tokens, latency, cost."""
    out: dict[str, dict] = {}
    for r in records:
        model = r.get("model") or r.get("requested_model") or "?"
        s = out.setdefault(model, dict(network_calls=0, replayed_calls=0, input_tokens=0, output_tokens=0,
                                       cost_usd=0.0, latencies=[]))
        if r.get("from_cassette"):
            s["replayed_calls"] += 1
            continue
        s["network_calls"] += 1
        u = r.get("usage") or {}
        s["input_tokens"] += int(u.get("input_tokens") or r.get("prompt_tokens") or 0)
        s["output_tokens"] += int(u.get("output_tokens") or r.get("completion_tokens") or 0)
        s["cost_usd"] += record_cost(r)
        if r.get("latency_s") is not None:
            s["latencies"].append(float(r["latency_s"]))
    for s in out.values():
        lat = s.pop("latencies")
        s["latency_s_median"] = float(np.median(lat)) if lat else None
        s["latency_s_total"] = round(float(np.sum(lat)), 1) if lat else 0.0
        s["cost_usd"] = round(s["cost_usd"], 4)
    return out


def total_cost(summary: dict) -> float:
    return round(sum(v["cost_usd"] for v in summary.values()), 4)
