#!/usr/bin/env python3
"""Build live-cic.json (CIC-engaged open Sev1s) and append to live-cic-history.json.

Inputs (in --work dir, default .agents/artifacts/live):
  cic_cases.json   list of OrgCS facts for open Sev1s with Escalate_to_CIC__c = true
  cic_slack.json   {caseNumber: {...slack signals...}} (see SKILL.md, "CIC monitor")

The page recomputes every age/flag against the browser clock; this script only stores
raw facts plus a rolling per-case history used for the activity bars.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HISTORY_CAP = 288  # 48h at 10-minute cadence
INTERVAL_MIN = 10


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


def build(work: Path) -> dict:
    cases = json.loads((work / "cic_cases.json").read_text())
    slack = json.loads((work / "cic_slack.json").read_text())
    out = []
    for case in cases:
        sig = slack.get(case["caseNum"]) or {}
        out.append(
            {
                **public_case(case),
                "slack": {
                    "found": bool(sig.get("channelId")),
                    "channelId": sig.get("channelId"),
                    "channelName": sig.get("channelName"),
                    "channelCreated": sig.get("channelCreated"),
                    "cicCommander": sig.get("cicCommander"),
                    "cicJoinedAt": sig.get("cicJoinedAt"),
                    "lastHumanTs": sig.get("lastHumanTs"),
                    "lastCicTs": sig.get("lastCicTs"),
                    "humanMsgLast30m": sig.get("humanMsgLast30m"),
                    "humanMsgLast60m": sig.get("humanMsgLast60m"),
                    "humanMsgLast24h": sig.get("humanMsgLast24h"),
                    "participants": sig.get("participants") or [],
                    "bridgeMentioned": sig.get("bridgeMentioned"),
                    "lastBridgeTs": sig.get("lastBridgeTs"),
                    "customerUpdateGiven": sig.get("customerUpdateGiven"),
                    "nextStepOrEtaStated": sig.get("nextStepOrEtaStated"),
                    "csmPresent": sig.get("csmPresent"),
                    "backlineEngaged": sig.get("backlineEngaged"),
                    "recent": scrub_recent(sig.get("recent")),
                    "summary": sig.get("summary"),
                    "error": sig.get("error"),
                },
            }
        )
    return {
        "meta": {
            "generated": now_iso(),
            "intervalMin": INTERVAL_MIN,
            "totalCic": len(out),
            "source": "OrgCS Case (open, Level 1 - Critical, Escalate_to_CIC__c = true) + Slack sev1 channels",
        },
        "cases": out,
    }


def append_history(snapshot: dict, path: Path) -> list:
    history = json.loads(path.read_text()) if path.exists() else []
    history.append(
        {
            "ts": snapshot["meta"]["generated"],
            "cases": {
                c["caseNum"]: {
                    "m60": c["slack"]["humanMsgLast60m"],
                    "m30": c["slack"]["humanMsgLast30m"],
                    "lastHuman": c["slack"]["lastHumanTs"],
                    "status": c.get("status"),
                }
                for c in snapshot["cases"]
            },
        }
    )
    history = history[-HISTORY_CAP:]
    path.write_text(json.dumps(history, separators=(",", ":")) + "\n")
    return history


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=ROOT / ".agents/artifacts/live")
    parser.add_argument("--out", type=Path, default=ROOT / "live-cic.json")
    parser.add_argument("--history", type=Path, default=ROOT / "live-cic-history.json")
    args = parser.parse_args()
    snapshot = build(args.work)
    history = append_history(snapshot, args.history)
    # ship the recent history inside the snapshot so the page needs a single fetch
    snapshot["history"] = history[-12:]
    args.out.write_text(json.dumps(snapshot, indent=1) + "\n")
    print(f"Wrote {args.out}: {snapshot['meta']['totalCic']} CIC-engaged Sev1s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
