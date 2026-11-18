#!/usr/bin/env python3
"""Merge OrgCS open-Sev1 facts with Slack channel signals into live-sev1.json.

Inputs (in --work dir, default .agents/artifacts/live):
  cases.json            list of open Sev1 cases from OrgCS (see SKILL.md for the SOQL)
  batch*_slack.json     {caseNumber: {...slack signals...}} written by the Slack fan-out agents

Outputs (repo root):
  live-sev1.json          snapshot the dashboard tab reads (raw facts only; the page
                          computes ages/flags against the browser clock)
  live-sev1-history.json  rolling snapshots used for trend lines (last 200)
"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANAGERS = ["Nikhil", "Jyothirmayee", "Shubha", "Mirza", "Sai Vikram", "Rohit"]
HISTORY_CAP = 200


def public_case(case):
    """The site is public: drop free text and bridge links, keep booleans."""
    out = {k: v for k, v in case.items() if k not in {"lastComment", "internalBridge", "resolverBridge"}}
    out["hasBridge"] = bool(case.get("internalBridge") or case.get("resolverBridge"))
    return out


def scrub_recent(messages):
    """The site is public: keep who/when, never the message body."""
    return [{"ts": m.get("ts"), "author": m.get("author"), "text": ""} for m in (messages or [])]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_slack(work: Path) -> dict:
    merged: dict = {}
    for path in sorted(glob.glob(str(work / "batch*_slack.json"))):
        try:
            merged.update(json.loads(Path(path).read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warn: skipped {path}: {exc}")
    return merged


def build(work: Path) -> dict:
    cases = json.loads((work / "cases.json").read_text())
    slack = load_slack(work)
    out = []
    for case in cases:
        sig = slack.get(case["caseNum"]) or {}
        found = bool(sig.get("channelId"))
        out.append(
            {
                **public_case(case),
                "slack": {
                    "found": found,
                    "channelId": sig.get("channelId"),
                    "channelName": sig.get("channelName"),
                    "channelCreated": sig.get("channelCreated"),
                    "archived": sig.get("channelArchived"),
                    "msgTotal": sig.get("msgCountTotal"),
                    "msg24h": sig.get("msgCount24h"),
                    "msg72h": sig.get("msgCount72h"),
                    "lastMessageTs": sig.get("lastMessageTs"),
                    "lastHumanMessageTs": sig.get("lastHumanMessageTs"),
                    "participants": sig.get("participants") or [],
                    "roles": sig.get("roleSignals") or {},
                    "recent": scrub_recent(sig.get("recentMessages")),
                    "summary": sig.get("summary"),
                    "error": sig.get("error"),
                },
            }
        )
    generated = now_iso()
    counts = {m: 0 for m in MANAGERS}
    for case in out:
        counts[case["manager"]] = counts.get(case["manager"], 0) + 1
    return {
        "meta": {
            "generated": generated,
            "source": "OrgCS (Case, Severity_Level__c = Level 1 - Critical, open) + Slack sev1 channels",
            "managers": MANAGERS,
            "totalOpen": len(out),
            "slackChannelsFound": sum(1 for c in out if c["slack"]["found"]),
            "byManager": counts,
        },
        "cases": out,
    }


def append_history(snapshot: dict, path: Path) -> None:
    history = json.loads(path.read_text()) if path.exists() else []
    history.append(
        {
            "ts": snapshot["meta"]["generated"],
            "total": snapshot["meta"]["totalOpen"],
            "byManager": snapshot["meta"]["byManager"],
            "cases": {
                c["caseNum"]: {
                    "status": c["status"],
                    "msg24h": c["slack"]["msg24h"],
                    "lastHuman": c["slack"]["lastHumanMessageTs"],
                    "lastPublic": c.get("lastPublicActivity"),
                }
                for c in snapshot["cases"]
            },
        }
    )
    path.write_text(json.dumps(history[-HISTORY_CAP:], separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=ROOT / ".agents/artifacts/live")
    parser.add_argument("--out", type=Path, default=ROOT / "live-sev1.json")
    parser.add_argument("--history", type=Path, default=ROOT / "live-sev1-history.json")
    args = parser.parse_args()
    snapshot = build(args.work)
    args.out.write_text(json.dumps(snapshot, indent=1) + "\n")
    append_history(snapshot, args.history)
    meta = snapshot["meta"]
    print(f"Wrote {args.out}: {meta['totalOpen']} open Sev1s, {meta['slackChannelsFound']} Slack channels found")
    print("by manager:", meta["byManager"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
