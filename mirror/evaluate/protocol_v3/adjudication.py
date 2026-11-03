"""Independent semantic scoring for the language-understanding evaluation.

Two stages, both done by people who did not write the cases or MIRROR:

1. Gold. Two adjudicators each write, independently, the outcome (apply,
   asks, rejects) and the structured edit for every turn, plus any other
   edits they would accept as meaning the same. ``gold_agreement`` reports
   Cohen's kappa on outcomes and exact agreement on edits; a third
   adjudicator resolves disagreements and the resolution is kept.
2. Blind judgement. After the run, ``make_pack`` writes one sheet per turn
   showing the sentence, the base, and every condition's result as a plain
   change list under shuffled labels; the label key goes to the custodian
   only. Judges assign one label per output (JUDGEMENTS). ``unblind`` joins
   the judgements with the key and counts per condition.

Automatic scoring (semantic state equal to the gold or an accepted
alternative) is the primary measure; blind judgements are reported next to it
and every disagreement between the two is listed.
"""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

JUDGEMENTS = ("correct", "acceptable_alternative", "wrong_scope", "over_applied", "under_applied",
              "wrong_rule", "wrong_refusal", "wrong_acceptance", "correct_refusal", "unclear")


def describe_change(before: dict, after: dict) -> list[str]:
    """Human-readable change list between two semantic states (no condition names)."""
    out = []
    eb, ea = before["enforced"], after["enforced"]
    for cid in sorted(set(eb) | set(ea)):
        if cid not in eb:
            out.append(f"now enforced: {cid} {json.dumps(ea[cid], sort_keys=True)}")
        elif cid not in ea:
            out.append(f"no longer enforced: {cid}")
        elif eb[cid] != ea[cid]:
            out.append(f"changed: {cid} {json.dumps(eb[cid], sort_keys=True)} -> {json.dumps(ea[cid], sort_keys=True)}")
    for o in sorted(set(after["ordering"]) - set(before["ordering"])):
        out.append(f"order now required: {o}")
    for o in sorted(set(before["ordering"]) - set(after["ordering"])):
        out.append(f"order no longer required: {o}")
    for g in sorted(set(after["inactive_goals"]) - set(before["inactive_goals"])):
        out.append(f"goal dropped: {g}")
    for g in sorted(set(before["inactive_goals"]) - set(after["inactive_goals"])):
        out.append(f"goal restored: {g}")
    return out or ["no change to what is enforced"]


def make_pack(results: Iterable[dict], out_dir: Path, *, seed: int) -> dict[str, Any]:
    """Blinded judgement sheets (sheets.json) and the label key (key.json,
    for the custodian only). Returns counts."""
    rng = random.Random(seed)
    by_turn: dict[tuple[str, int], list[tuple[str, dict]]] = defaultdict(list)
    for r in results:
        for i, t in enumerate(r["turns"]):
            by_turn[(r["case_id"], i)].append((r["condition"], t))
    sheets, key = [], {}
    for (cid, i), outs in sorted(by_turn.items()):
        labels = [chr(ord("A") + k) for k in range(len(outs))]
        rng.shuffle(labels)
        text = outs[0][1].get("text", "")
        entries = []
        for (cond, t), lab in zip(outs, labels):
            shown = {"label": lab, "outcome": t["outcome"] if t["outcome"] != "abstain" else "no change made",
                     "question_or_reason": (t.get("detail") or "")[:300] if t["outcome"] in ("asks", "rejects") else "",
                     "changes": describe_change(t["state_before"], t["state_after"])
                     if t["outcome"] == "apply" else []}
            entries.append(shown)
            key[f"{cid}#{i}#{lab}"] = cond
        before = outs[0][1]["state_before"]
        sheets.append({"case_id": cid, "turn": i + 1, "text": text,
                       "before": {"enforced": before["enforced"], "ordering": before["ordering"],
                                  "dropped_goals": before["inactive_goals"]},
                       "outputs": sorted(entries, key=lambda e: e["label"]),
                       "judgement_options": list(JUDGEMENTS)})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sheets.json").write_text(json.dumps(sheets, indent=1, ensure_ascii=False) + "\n")
    (out_dir / "key.json").write_text(json.dumps(key, indent=1) + "\n")
    return {"turns": len(sheets), "outputs": len(key)}


def unblind(judgements: dict[str, str], key: dict[str, str]) -> dict[str, dict[str, int]]:
    """``judgements``: {"<case>#<turn index>#<label>": judgement}. Counts per condition."""
    bad = sorted(v for v in judgements.values() if v not in JUDGEMENTS)
    if bad:
        raise ValueError(f"unknown judgement labels: {bad[:5]}")
    missing = sorted(set(key) - set(judgements))
    out: dict[str, Counter] = defaultdict(Counter)
    for k, v in judgements.items():
        out[key[k]][v] += 1
    for k in missing:
        out[key[k]]["not_judged"] += 1
    return {c: dict(v) for c, v in sorted(out.items())}


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    if len(a) != len(b) or not a:
        raise ValueError("two equally long, non-empty label lists")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return None if pe == 1 else round((po - pe) / (1 - pe), 4)


def gold_agreement(first: dict[str, dict], second: dict[str, dict]) -> dict[str, Any]:
    """Each argument maps "<case>#<turn>" to {"outcome": ..., "edit": StructuredEdit dict or None}."""
    keys = sorted(set(first) & set(second))
    oa = [first[k]["outcome"] for k in keys]
    ob = [second[k]["outcome"] for k in keys]

    def norm(e):
        e = dict(e or {})
        return json.dumps({k: sorted(json.dumps(x, sort_keys=True) for x in v) for k, v in e.items() if v},
                          sort_keys=True)
    same_edit = [k for k in keys if first[k]["outcome"] == second[k]["outcome"] == "apply"
                 and norm(first[k]["edit"]) == norm(second[k]["edit"])]
    both_apply = [k for k in keys if first[k]["outcome"] == second[k]["outcome"] == "apply"]
    disagree = [k for k in keys if first[k]["outcome"] != second[k]["outcome"]
                or (first[k]["outcome"] == "apply" and k not in same_edit)]
    return {"turns": len(keys), "only_in_one": sorted(set(first) ^ set(second)),
            "outcome_kappa": cohen_kappa(oa, ob) if keys else None,
            "outcome_agreement": round(sum(x == y for x, y in zip(oa, ob)) / len(keys), 4) if keys else None,
            "edit_agreement_when_both_apply": f"{len(same_edit)}/{len(both_apply)}",
            "to_resolve": disagree}
