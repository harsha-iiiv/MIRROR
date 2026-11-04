"""User-reviewed mapping from real objects to supported simulation proxies.

A real mug is not the simulator's cup. The simulator runs fixed proxy
geometry (catalogue sizes), simulated beads and assumed mass and friction.
This module records, for every tracked real object, what it is, which proxy
stands in for it, which dimensions a person measured and which were supplied
or defaulted, which real features the proxy does not model (a mug handle),
and what was assumed. ``fidelity_statement`` is shown wherever the proxy is
used: proxy fidelity is never measured physical accuracy.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from mirror.geometry import BLOCK, CUP, MARKER, TRAY

DimSource = Literal["measured_ruler", "measured_from_video", "supplied_manually", "proxy_default"]

SUPPORTED = {
    "cup": {"sim": "cup", "examples": ["mug", "cup", "beaker"],
            "proxy_dims_m": [2 * CUP.outer_half[0], 2 * CUP.outer_half[1], 2 * CUP.outer_half[2]],
            "proxy_note": "open-top box cup with simulated beads", "feature_hints": ["handle", "round body", "tapered sides"]},
    "block": {"sim": "block", "examples": ["small box", "block", "cube"],
              "proxy_dims_m": [2 * h for h in BLOCK.half], "proxy_note": "rigid cube", "feature_hints": ["lid", "not a cube"]},
    "marker": {"sim": "marker", "examples": ["pen", "marker", "pencil"],
               "proxy_dims_m": [2 * MARKER.radius, 2 * MARKER.radius, 2 * MARKER.half_length],
               "proxy_note": "capped cylinder", "feature_hints": ["clip", "cap"]},
    "tray": {"sim": "tray", "examples": ["tray", "shallow box lid"],
             "proxy_dims_m": [2 * TRAY.inner_half[0], 2 * TRAY.inner_half[1], 0.03], "proxy_note": "open box destination",
             "feature_hints": ["sloped walls", "handles"]},
    "wall": {"sim": "wall obstacle", "examples": ["upright book", "board standing on edge"],
             "proxy_dims_m": None, "proxy_note": "static vertical wall, sized from the real object", "feature_hints": []},
}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dimension(_M):
    value_m: float
    source: DimSource
    note: str = ""


class Assumption(_M):
    name: str                      # mass_kg, friction, contents, ...
    value: str
    basis: Literal["assumed", "proxy_default", "measured"] = "assumed"


class ObjectMapping(_M):
    track_label: str                         # the label of the person's prompt / track
    real_identity: str                       # free text: "white ceramic mug, about 330 ml"
    proxy: Literal["cup", "block", "marker", "tray", "wall", "ignore"]
    real_dims: dict[str, Dimension] = Field(default_factory=dict)   # keys: width, depth, height (metres)
    unsupported_features: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    real_contents: Optional[str] = None      # "dry rice", "plastic beads"
    contents_observable: bool = False        # can the footage measure the contents? (almost never)
    confirmed_by_person: bool = False
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _drop_derived(cls, data):
        # fidelity_statement is derived from the fields above; a stored copy is ignored and recomputed
        if isinstance(data, dict) and "fidelity_statement" in data:
            data = {k: v for k, v in data.items() if k != "fidelity_statement"}
        return data

    @model_serializer(mode="wrap")
    def _with_statement(self, handler):
        out = handler(self)
        if isinstance(out, dict):
            out["fidelity_statement"] = self.fidelity_statement()
        return out

    def height_m(self) -> tuple[Optional[float], Optional[DimSource]]:
        d = self.real_dims.get("height")
        if d is not None:
            return d.value_m, d.source
        p = SUPPORTED.get(self.proxy, {}).get("proxy_dims_m")
        return (p[2], "proxy_default") if p else (None, None)

    def fidelity_statement(self) -> str:
        if self.proxy == "ignore":
            return f"{self.real_identity}: ignored, not part of the task."
        s = SUPPORTED[self.proxy]
        measured = [k for k, d in self.real_dims.items() if d.source.startswith("measured")]
        supplied = [k for k, d in self.real_dims.items() if not d.source.startswith("measured")]
        parts = [f"{self.real_identity} is represented in simulation by the {s['sim']} proxy ({s['proxy_note']})."]
        if measured:
            parts.append("Measured: " + ", ".join(measured) + ".")
        if supplied:
            parts.append("Supplied or defaulted, not measured: " + ", ".join(supplied) + ".")
        if self.unsupported_features:
            parts.append("Not modelled: " + ", ".join(self.unsupported_features) + ".")
        if self.proxy == "cup":
            real = self.real_contents or "its real contents"
            parts.append(f"Simulated beads stand in for {real}; spill counts are simulated, not measured from the footage"
                         + (" (contents measured independently)." if self.contents_observable else "."))
        if self.assumptions:
            parts.append("Assumed: " + "; ".join(f"{a.name} {a.value} ({a.basis})" for a in self.assumptions) + ".")
        parts.append("Proxy fidelity is not measured physical accuracy.")
        return " ".join(parts)


class WorkspacePlacement(_M):
    """Where the table frame (board origin) sits in the simulator's world. A
    person chooses this; it is not measured."""
    origin_xy_m: tuple[float, float] = (-0.075, 0.105)    # board centred on the sim table origin by default
    yaw_deg: float = 0.0
    source: Literal["default_centred", "user_supplied", "auto_layout_centred"] = "default_centred"
    # auto_layout_centred: chosen by software so the captured layout's bounding box sits at the simulator
    # table centre (a display/workspace convenience, not a measurement and not a person's choice)


def review_items(mappings: list[ObjectMapping]) -> list[dict]:
    """Items a person must resolve before a specification is proposed."""
    items = []
    for m in mappings:
        if not m.confirmed_by_person:
            items.append({"code": "mapping_unconfirmed", "severity": "abstain", "object": m.track_label,
                          "message": f"Confirm what '{m.track_label}' is and which proxy stands in for it."})
        if m.proxy == "ignore":
            continue
        s = SUPPORTED[m.proxy]
        if s["proxy_dims_m"] and m.real_dims:
            for k, i in (("width", 0), ("depth", 1), ("height", 2)):
                if m.proxy == "tray" and k != "height":
                    continue   # the simulated tray takes the measured inside footprint (see trace.to_scene)
                d = m.real_dims.get(k)
                if d is not None and s["proxy_dims_m"][i] > 0 and abs(d.value_m / s["proxy_dims_m"][i] - 1.0) > 0.5:
                    items.append({"code": "proxy_size_mismatch", "severity": "review", "object": m.track_label,
                                  "message": f"Real {k} {d.value_m * 100:.1f} cm vs proxy {s['proxy_dims_m'][i] * 100:.1f} cm: "
                                             "simulated clearance and grasp behaviour may differ from the real object."})
        if "height" not in m.real_dims and m.proxy in ("cup", "block", "marker", "wall"):
            items.append({"code": "height_not_measured", "severity": "review", "object": m.track_label,
                          "message": "Height not measured; single-camera positions use the proxy's default height."})
    kinds = [m.proxy for m in mappings if m.confirmed_by_person]
    if kinds.count("tray") > 1:
        items.append({"code": "multiple_trays", "severity": "abstain", "object": None,
                      "message": "More than one tray mapped; the supported task has one destination tray."})
    return items
