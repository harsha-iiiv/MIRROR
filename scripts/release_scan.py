#!/usr/bin/env python
"""Scan publishable files for secrets, credentials in URLs and personal data.

  python scripts/release_scan.py                 # files tracked by git
  python scripts/release_scan.py --root DIR      # any directory (e.g. an exported static site)
  python scripts/release_scan.py --json out.json

Values are never printed: each finding shows the file, line, rule and a masked
preview (first four characters and the length). Exit status 1 when a
high-severity finding exists. Media files are listed with their size so a
person can confirm their provenance.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RULES = [  # (name, severity, regex)
    ("private_key", "high", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", "high", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", "high", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("github_fine_grained", "high", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("openai_style_key", "high", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("elevenlabs_style_key", "high", re.compile(r"\bsk_[a-f0-9]{40,}\b")),
    ("slack_token", "high", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", "high", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("netlify_token", "high", re.compile(r"\bnf[pc]_[A-Za-z0-9]{30,}\b")),
    ("jwt", "high", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer_token", "high", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{24,}")),
    ("credential_assignment", "high", re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token)\b[\"']?\s*[:=]\s*[\"']([^\"'\s]{12,})[\"']")),
    ("credential_in_url", "high", re.compile(
        r"(?i)[?&](token|key|api_key|access_token|sig|signature|x-amz-signature|auth)=([A-Za-z0-9%._~-]{16,})")),
    ("email", "medium", re.compile(r"(?<![A-Za-z0-9+/=_.-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,6}(?![A-Za-z0-9+/=_-])")),
    ("phone_number", "medium", re.compile(r"(?<![\w.])\+?\d{1,2}[ .-]?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])")),
    ("home_path", "medium", re.compile(r"(/home/[a-z][a-z0-9_-]+/|/Users/[A-Za-z][A-Za-z0-9_-]+/|C:\\Users\\)")),
    ("ipv4", "low", re.compile(r"(?<![\d.=v])(?!127\.|0\.|10\.|192\.168\.|255\.)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")),
]
ALLOW = [  # strings that match a rule but are known to be public or placeholders
    re.compile(r"users\.noreply\.github\.com$"), re.compile(r"@example\.(com|org)$"),
    re.compile(r"^your[-_]"), re.compile(r"^\.\.\.$"), re.compile(r"^<"), re.compile(r"\$\{"),
    re.compile(r"(?i)^(dummy|fake|test|example|placeholder|changeme)|secret-key-\d+$|^x+$"),
]
MEDIA = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".npz", ".gz"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}


def files(root: Path, tracked: bool) -> list[Path]:
    if tracked:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
        return [root / p for p in out.decode().split("\0") if p]
    return [p for p in root.rglob("*") if p.is_file() and not set(p.relative_to(root).parts) & SKIP_DIRS]


def mask(s: str) -> str:
    return f"{s[:4]}… ({len(s)} chars)"


def _allowed(v: str) -> bool:
    return any(a.search(v) for a in ALLOW)


def _in_encoded(line: str, m) -> bool:
    """True when the match sits inside a long run without spaces or quotes
    (base64, segmentation run-length strings) on a long line."""
    if len(line) < 300:
        return False
    window = line[max(0, m.start() - 30):m.end() + 30]
    return len(window) >= 40 and not re.search(r'[\s"]', window)


def scan(root: Path, tracked: bool = True) -> dict:
    findings, media, unreadable, encoded = [], [], [], 0
    for p in files(root, tracked):
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() in MEDIA:
            media.append({"file": rel, "bytes": p.stat().st_size})
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            unreadable.append(rel)
            continue
        if len(text) > 20_000_000:
            unreadable.append(rel)
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if len(line) > 200_000:
                line = line[:200_000]
            for name, sev, rx in RULES:
                for m in rx.finditer(line):
                    v = m.group(m.lastindex or 0) if m.lastindex else m.group(0)
                    if _allowed(v) or _allowed(m.group(0)):
                        continue
                    if sev != "high" and _in_encoded(line, m):
                        encoded += 1
                        continue
                    findings.append({"file": rel, "line": n, "rule": name, "severity": sev, "preview": mask(v)})
    sev = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev[f["severity"]] += 1
    return {"root": root.name, "tracked_only": tracked, "counts": sev, "findings": findings,
            "media": sorted(media, key=lambda m: -m["bytes"]), "binary_or_large": unreadable,
            "skipped_inside_encoded_data": encoded}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else ROOT
    rep = scan(root, tracked=a.root is None)
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1) + "\n")
    print(f"{rep['root']}: high {rep['counts']['high']}, medium {rep['counts']['medium']}, "
          f"low {rep['counts']['low']}; {len(rep['media'])} media files; "
          f"{len(rep['binary_or_large'])} binary or very large files not text-scanned; "
          f"{rep['skipped_inside_encoded_data']} medium/low matches inside encoded data skipped")
    for f in rep["findings"][:200]:
        print(f"  [{f['severity']}] {f['file']}:{f['line']} {f['rule']} {f['preview']}")
    return 1 if rep["counts"]["high"] else 0


if __name__ == "__main__":
    sys.exit(main())
