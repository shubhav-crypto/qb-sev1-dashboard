#!/usr/bin/env python3
"""
QB Sev1 Dashboard Live Updater
Reads today's Sev1 alerts from #support-alerts-service-sig, resolves
quality from each case's Sev1 Slack channel, and updates data.json.

Required env vars:
  SLACK_BOT_TOKEN   - Bot token with channels:history, channels:read scopes
  SLACK_USER_TOKEN  - User token (needed for private channel reads)

Run: python3 updater.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

import urllib.request
import urllib.parse
import urllib.error

# ── CONFIG ──────────────────────────────────────────────────────────────────
ALERTS_CHANNEL     = "C02K54WLXM2"   # #support-alerts-service-sig
TEAM_CHANNEL       = "C026E3HCG04"   # #nikhil-team-signature (schedule posts)
DATA_FILE          = os.path.join(os.path.dirname(__file__), "data.json")
IST                = ZoneInfo("Asia/Kolkata")
QB_START_HOUR_IST  = 12   # QB window: 12:30 PM IST
QB_START_MIN_IST   = 30
QB_END_HOUR_IST    = 21   # 9:00 PM IST
QB_END_MIN_IST     = 0

# Manager Slack IDs  (used to find channel engage)
MANAGER_IDS = {
    "Nikhil":       "U01FU2F5RN3",
    "Jyothirmayee": "WN8LA3U5D",
    "Sai Vikram":   "W017X5CVB9R",
    "Rohit":        "U01G5N5SZC5",
    "Mirza":        "U04219TJTK5",
    "Shubha":       "U04999GSQ13",
}

# ── SLACK API HELPERS ────────────────────────────────────────────────────────
TOKEN = os.environ.get("SLACK_USER_TOKEN") or os.environ.get("SLACK_BOT_TOKEN")

def slack_get(method, params=None, retries=5, delay=2):
    """Call a Slack Web API method (POST with Authorization header — works with all token types)."""
    params = params or {}
    url = f"https://slack.com/api/{method}"
    body = urllib.parse.urlencode(params).encode()
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            if not data.get("ok"):
                err = data.get("error", "unknown")
                if err == "ratelimited":
                    wait = int(data.get("headers", {}).get("Retry-After", delay * (attempt + 1)))
                    print(f"  rate-limited, waiting {wait}s …", flush=True)
                    time.sleep(wait)
                    continue
                # Some errors are acceptable (channel not found, no messages)
                if err in ("channel_not_found", "not_in_channel", "missing_scope"):
                    return None
                print(f"  Slack error [{method}]: {err}", flush=True)
                return None
            return data
        except Exception as exc:
            print(f"  Request error ({attempt+1}/{retries}): {exc}", flush=True)
            time.sleep(delay * (attempt + 1))
    return None

def history(channel_id, oldest=None, latest=None, limit=200):
    """Fetch message history for a channel."""
    params = {"channel": channel_id, "limit": limit}
    if oldest: params["oldest"] = str(oldest)
    if latest: params["latest"] = str(latest)
    data = slack_get("conversations.history", params)
    if data is None:
        return []
    return data.get("messages", [])

def search_channel_by_case(case_num):
    """Search for a Sev1 channel whose name contains the case number."""
    data = slack_get("conversations.list", {
        "types": "private_channel,public_channel",
        "limit": 200,
        "exclude_archived": "false",
    })
    if not data:
        return None
    # Try patterns: sev1-<casenum>, <casenum>-sev1, etc.
    pattern = re.compile(rf"(sev1.*{case_num}|{case_num}.*sev1)", re.IGNORECASE)
    for ch in data.get("channels", []):
        if pattern.search(ch.get("name", "")):
            return ch
    # Paginate if needed
    cursor = data.get("response_metadata", {}).get("next_cursor", "")
    while cursor:
        data = slack_get("conversations.list", {
            "types": "private_channel,public_channel",
            "limit": 200,
            "exclude_archived": "false",
            "cursor": cursor,
        })
        if not data:
            break
        for ch in data.get("channels", []):
            if pattern.search(ch.get("name", "")):
                return ch
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
    return None

def parse_seconds(s):
    """Parse '2m 18s', '1h 5m 30s', '45s' → total seconds."""
    if not s:
        return None
    m = re.match(r"(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?", s)
    if not m:
        return None
    h, mn, sc = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
    return h * 3600 + mn * 60 + sc

def fmt_duration(secs):
    """Format seconds as '2m 18s' / '1h 5m 30s' / '45s'."""
    if secs is None:
        return None
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    parts = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)

# ── QUALITY DETECTION ────────────────────────────────────────────────────────
# Keywords that indicate quality in a channel message
STRONG_RE   = re.compile(r"\bstrong\b",   re.IGNORECASE)
MODERATE_RE = re.compile(r"\bmoderate\b", re.IGNORECASE)
NEEDS_RE    = re.compile(r"\bneeds.follow.?up\b|\bneeds improvement\b", re.IGNORECASE)

QUALITY_KEYWORDS = re.compile(
    r"(quality|work\s*behavior|wb\s*quality|cic\s*quality|"
    r"strong|moderate|needs\s*follow|needs\s*improvement)",
    re.IGNORECASE
)

def detect_quality_from_messages(messages):
    """
    Scan a list of channel messages for quality assessment.
    Returns 'STRONG', 'MODERATE', 'NEEDS', or None.
    Looks for explicit quality-assessment lines (e.g. from canvas bot,
    QB manager summary, or any message with quality + rating).
    """
    # Sort newest-first so we pick up the final assessment
    for msg in sorted(messages, key=lambda m: float(m.get("ts", 0)), reverse=True):
        text = msg.get("text", "")
        if not QUALITY_KEYWORDS.search(text):
            continue
        if NEEDS_RE.search(text):
            return "NEEDS"
        if STRONG_RE.search(text):
            return "STRONG"
        if MODERATE_RE.search(text):
            return "MODERATE"
    return None

def get_channel_quality(channel_id):
    """Read up to 200 messages from a Sev1 channel and return quality rating."""
    if not channel_id:
        return None
    msgs = history(channel_id, limit=200)
    return detect_quality_from_messages(msgs)

def get_channel_engage(channel_id, channel_created_ts, manager_id):
    """
    Find QB manager's first TEXT message in channel after creation.
    Returns (engage_str, first_text_ts) or (None, None).
    """
    if not channel_id:
        return None, None
    msgs = history(channel_id, oldest=channel_created_ts, limit=200)
    for msg in sorted(msgs, key=lambda m: float(m.get("ts", 0))):
        if msg.get("user") != manager_id:
            continue
        if msg.get("subtype"):           # skip join/leave system messages
            continue
        if not msg.get("text", "").strip():
            continue
        engage_secs = int(float(msg["ts"])) - int(float(channel_created_ts))
        if engage_secs < 0:
            engage_secs = 0
        return fmt_duration(engage_secs), float(msg["ts"])
    return None, None

# ── TODAY'S ALERTS READER ────────────────────────────────────────────────────
SEV1_ALERT_RE = re.compile(
    r"Sev1\|Signature Success",
    re.IGNORECASE
)
CASE_NUM_RE   = re.compile(r"\b(47\d{7})\b")
ACCOUNT_RE    = re.compile(r"Account:\s*(.+?)(?:\n|$)", re.IGNORECASE)
PRODUCT_RE    = re.compile(r"Product:\s*(.+?)(?:\n|$)", re.IGNORECASE)
RESOLVED_RE   = re.compile(r"resolved|resolve", re.IGNORECASE)
GREEN_CIRCLE  = "\U0001f7e2"  # 🟢

def ist_day_timestamps(day: date):
    """Return (oldest_unix, latest_unix) for a full IST calendar day."""
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=IST)
    end   = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=IST)
    return start.timestamp(), end.timestamp()

def ist_qb_timestamps(day: date):
    """Return (oldest_unix, latest_unix) for QB window on a given IST day."""
    start = datetime(day.year, day.month, day.day,
                     QB_START_HOUR_IST, QB_START_MIN_IST, 0, tzinfo=IST)
    end   = datetime(day.year, day.month, day.day,
                     QB_END_HOUR_IST, QB_END_MIN_IST, 0, tzinfo=IST)
    return start.timestamp(), end.timestamp()

def ts_to_ist_str(ts):
    """Convert unix timestamp to 'H:MM AM/PM' IST string."""
    dt = datetime.fromtimestamp(float(ts), tz=IST)
    return dt.strftime("%-I:%M %p")

def read_todays_sev1_alerts(target_date: date):
    """
    Read #support-alerts-service-sig for target_date (IST).
    Returns list of dicts: { caseNum, alertTs, resolvedTs, account, product }
    Excludes: Esc|Sev2, Esc|Sev3, Esc|Sev4 (escalated cases).
    """
    oldest, latest = ist_day_timestamps(target_date)
    print(f"Reading alerts channel for {target_date} (oldest={oldest:.0f}, latest={latest:.0f}) …", flush=True)

    # Collect all messages — may need multiple pages
    all_msgs = []
    cursor = None
    while True:
        params = {
            "channel": ALERTS_CHANNEL,
            "oldest": str(oldest),
            "latest": str(latest),
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = slack_get("conversations.history", params)
        if not data:
            break
        all_msgs.extend(data.get("messages", []))
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
        time.sleep(0.5)

    print(f"  Got {len(all_msgs)} messages", flush=True)

    # Group PD alert threads by case number
    # Each alert is a parent message; resolve/ack come as thread replies
    case_alerts = {}   # caseNum -> { alertTs, resolvedTs, account, product }

    # Separate parent messages vs threaded replies
    parent_msgs = [m for m in all_msgs if not m.get("thread_ts") or m.get("thread_ts") == m.get("ts")]

    for msg in parent_msgs:
        text = msg.get("text", "")
        # Must be a Sev1|Signature Success alert
        if not SEV1_ALERT_RE.search(text):
            continue
        # Must not be escalated (Esc|Sev2/3/4)
        if re.search(r"Esc\|Sev[234]", text, re.IGNORECASE):
            continue
        case_match = CASE_NUM_RE.search(text)
        if not case_match:
            continue
        case_num = case_match.group(1)
        account = ""
        product = ""
        am = ACCOUNT_RE.search(text)
        pm = PRODUCT_RE.search(text)
        if am: account = am.group(1).strip()
        if pm: product = pm.group(1).strip()

        if case_num not in case_alerts:
            case_alerts[case_num] = {
                "caseNum":    case_num,
                "alertTs":    msg["ts"],
                "resolvedTs": None,
                "account":    account,
                "product":    product,
            }
        else:
            # Keep earliest alert timestamp
            if float(msg["ts"]) < float(case_alerts[case_num]["alertTs"]):
                case_alerts[case_num]["alertTs"] = msg["ts"]
                if account: case_alerts[case_num]["account"] = account
                if product: case_alerts[case_num]["product"] = product

    # Look for resolve messages (green circle or "resolved" text in replies)
    reply_msgs = [m for m in all_msgs if m.get("thread_ts") and m.get("thread_ts") != m.get("ts")]
    for msg in reply_msgs:
        text = msg.get("text", "")
        if GREEN_CIRCLE in text or RESOLVED_RE.search(text):
            # Find which case this thread belongs to
            thread_ts = msg.get("thread_ts")
            for case_num, alert in case_alerts.items():
                if alert["alertTs"] == thread_ts:
                    if not alert["resolvedTs"] or float(msg["ts"]) < float(alert["resolvedTs"]):
                        alert["resolvedTs"] = msg["ts"]
                    break

    return list(case_alerts.values())

# ── SCHEDULE READER ──────────────────────────────────────────────────────────
def get_todays_manager(target_date: date):
    """
    Read today's schedule from #nikhil-team-signature daily notification.
    Returns manager name string or None.
    """
    oldest, latest = ist_day_timestamps(target_date)
    msgs = history(TEAM_CHANNEL, oldest=oldest, latest=latest, limit=50)
    # Daily notification bot posts "QB Manager: <Name>"
    qb_re = re.compile(r"QB Manager:\s*(.+?)(?:\n|$)", re.IGNORECASE)
    for msg in sorted(msgs, key=lambda m: float(m.get("ts", 0))):
        m = qb_re.search(msg.get("text", ""))
        if m:
            return m.group(1).strip()
    return None

# ── MAIN UPDATE LOGIC ────────────────────────────────────────────────────────
def load_data():
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {DATA_FILE}", flush=True)

def find_or_create_day(data, target_date: date, manager: str):
    """Find existing day entry or create a new one."""
    iso = target_date.isoformat()
    for day in data["days"]:
        if day["date"] == iso:
            return day
    # New day entry
    label_map = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                 7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    label = f"{label_map[target_date.month]} {target_date.day}"
    new_day = {
        "date": iso,
        "label": label,
        "manager": manager,
        "canvasId": None,
        "cases": []
    }
    data["days"].append(new_day)
    data["days"].sort(key=lambda d: d["date"])
    return new_day

def update_day(target_date: date):
    """Main update function for a given IST date."""
    if not TOKEN:
        print("ERROR: No SLACK_USER_TOKEN or SLACK_BOT_TOKEN set.", file=sys.stderr)
        sys.exit(1)

    data = load_data()

    # 1. Get today's QB manager
    manager = None
    # Check schedule in data.json first
    iso = target_date.isoformat()
    if iso in data.get("schedule", {}):
        manager = data["schedule"][iso]["manager"]
    if not manager:
        print("Looking up today's manager from Slack schedule …", flush=True)
        manager = get_todays_manager(target_date)
    if not manager:
        print(f"WARNING: Could not determine QB manager for {target_date}. Using 'Unknown'.", flush=True)
        manager = "Unknown"
    print(f"Manager for {target_date}: {manager}", flush=True)
    manager_id = MANAGER_IDS.get(manager)

    # 2. Read today's Sev1 alerts
    alerts = read_todays_sev1_alerts(target_date)
    print(f"Found {len(alerts)} Sev1 cases for {target_date}", flush=True)

    if not alerts:
        print("No new Sev1 cases found — nothing to update.", flush=True)
        return False

    # 3. Find/create day entry in data
    day_entry = find_or_create_day(data, target_date, manager)
    existing_cases = {c["caseNum"]: c for c in day_entry["cases"]}
    changed = False

    for alert in sorted(alerts, key=lambda a: float(a["alertTs"])):
        case_num = alert["caseNum"]
        alert_ts = float(alert["alertTs"])
        resolved_ts = float(alert["resolvedTs"]) if alert["resolvedTs"] else None

        # Compute resolve time
        resolve_str = None
        if resolved_ts:
            resolve_secs = int(resolved_ts - alert_ts)
            if resolve_secs >= 0:
                resolve_str = fmt_duration(resolve_secs)

        time_ist = ts_to_ist_str(alert_ts)

        if case_num in existing_cases:
            case = existing_cases[case_num]
            updated = False
            # Update resolve time if we now have it
            if resolve_str and not case.get("resolve"):
                case["resolve"] = resolve_str
                updated = True
            # Update time if missing
            if not case.get("timeIST"):
                case["timeIST"] = time_ist
                updated = True
            if updated:
                changed = True
                print(f"  Updated case {case_num}", flush=True)
        else:
            # New case — add it
            print(f"  New case {case_num} @ {time_ist}", flush=True)
            new_case = {
                "caseNum":      case_num,
                "account":      alert.get("account", ""),
                "product":      alert.get("product", ""),
                "timeIST":      time_ist,
                "quality":      "PENDING",
                "resolve":      resolve_str,
                "channelEngage": None,
            }
            day_entry["cases"].append(new_case)
            existing_cases[case_num] = new_case
            changed = True

    # 4. For each case without channel engage or quality, find its Sev1 channel
    print("Checking Sev1 channels for channel-engage and quality …", flush=True)
    for case in day_entry["cases"]:
        case_num = case["caseNum"]
        needs_engage  = not case.get("channelEngage")
        needs_quality = case.get("quality") in (None, "PENDING")
        if not (needs_engage or needs_quality):
            continue

        print(f"  Searching channel for case {case_num} …", flush=True)
        channel = search_channel_by_case(case_num)
        if not channel:
            print(f"    No channel found for {case_num}", flush=True)
            continue

        channel_id      = channel["id"]
        channel_created = channel.get("created")   # unix timestamp (integer)
        print(f"    Found channel {channel_id} (created {channel_created})", flush=True)

        if needs_engage and manager_id and channel_created:
            # Only compute engage if channel was created TODAY (not pre-existing)
            alert_date = target_date
            created_dt = datetime.fromtimestamp(channel_created, tz=IST).date()
            if created_dt == alert_date:
                engage, _ = get_channel_engage(channel_id, channel_created, manager_id)
                if engage:
                    case["channelEngage"] = engage
                    changed = True
                    print(f"    Channel engage: {engage}", flush=True)
            else:
                print(f"    Pre-existing channel (created {created_dt}) — skipping engage", flush=True)

        if needs_quality:
            quality = get_channel_quality(channel_id)
            if quality:
                case["quality"] = quality
                changed = True
                print(f"    Quality: {quality}", flush=True)
            else:
                print(f"    Quality not yet determined", flush=True)

        time.sleep(0.3)   # be gentle with Slack rate limits

    # 5. Update metadata
    total = sum(len(d["cases"]) for d in data["days"])
    data["meta"]["totalCases"] = total
    data["meta"]["daysTracked"] = len(data["days"])
    from datetime import datetime as dt
    data["meta"]["generated"] = dt.now(tz=IST).isoformat(timespec="seconds")

    if changed:
        save_data(data)
        return True
    else:
        print("No changes detected.", flush=True)
        return False

if __name__ == "__main__":
    # Determine target date
    if len(sys.argv) > 1:
        target = date.fromisoformat(sys.argv[1])
    else:
        now_ist = datetime.now(tz=IST)
        target = now_ist.date()

    print(f"QB Sev1 Dashboard Updater — target date: {target}", flush=True)
    result = update_day(target)
    sys.exit(0 if result else 0)   # always exit 0 (no changes is not an error)
