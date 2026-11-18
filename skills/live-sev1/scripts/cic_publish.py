#!/usr/bin/env python3
"""Guard + publish helpers for the shareable /cic-monitor skill.

  cic_publish.py check   [--window 8]   pull latest; say whether someone else just refreshed
  cic_publish.py publish [--trailer ..] verify nothing sensitive, commit ONLY the two data
                                        files, rebase on origin/main, push (never force)

Run from anywhere inside the clone. Exit code 0 = ok / nothing to do, 1 = failed (report it).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    IST = timezone.utc

ROOT = Path(__file__).resolve().parents[3]
FILES = ["live-cic.json", "live-cic-history.json"]


def sh(*cmd, check=True):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {r.stderr.strip() or r.stdout.strip()}")
    return r


def me() -> str:
    for cmd in (["gh", "api", "user", "--jq", ".login"], ["git", "config", "user.name"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out:
            return out
    return "unknown"


def cmd_check(args) -> int:
    try:
        sh("git", "pull", "--rebase", "--autostash", "origin", "main")
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    path = ROOT / "live-cic.json"
    meta = json.loads(path.read_text()).get("meta", {}) if path.exists() else {}
    gen = meta.get("generated")
    age = None
    if gen:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(gen)).total_seconds() / 60
    who = meta.get("refreshedBy")
    mine = me()
    skip = age is not None and age < args.window and who not in (None, "unknown", mine)
    print(json.dumps({"ok": True, "skip": skip, "ageMin": None if age is None else round(age, 1),
                      "refreshedBy": who, "me": mine, "windowMin": args.window,
                      "previousSnapshot": gen}))
    return 0


def cmd_publish(args) -> int:
    data = (ROOT / "live-cic.json").read_text()
    hist = (ROOT / "live-cic-history.json").read_text()
    for name, body in (("live-cic.json", data), ("live-cic-history.json", hist)):
        if "http" in body or "lastComment" in body or '"internalBridge"' in body:
            print(f"REFUSING to publish: {name} contains a URL, comment or bridge field")
            return 1
    sh("git", "add", *FILES)
    if sh("git", "diff", "--cached", "--quiet", "--", *FILES, check=False).returncode == 0:
        print("nothing changed; no commit")
        return 0
    stamp = datetime.now(IST).strftime("%H:%M")
    msg = f"CIC monitor refresh {stamp} IST by {me()}"
    if args.trailer:
        msg += f"\n\n{args.trailer}"
    sh("git", "commit", "-q", "-m", msg, "--", *FILES)
    try:
        sh("git", "pull", "--rebase", "--autostash", "origin", "main")
        sh("git", "push", "origin", "main")
    except RuntimeError as exc:
        print(f"PUSH FAILED: {exc}")
        sh("git", "rebase", "--abort", check=False)
        return 1
    print(f"PUSHED {stamp} IST")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--window", type=float, default=8, help="skip if someone else refreshed within N minutes")
    c.set_defaults(fn=cmd_check)
    p = sub.add_parser("publish")
    p.add_argument("--trailer", default="", help="attribution line for the commit message, if your session requires one")
    p.set_defaults(fn=cmd_publish)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
