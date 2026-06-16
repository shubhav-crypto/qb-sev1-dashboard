#!/bin/bash
# QB Dashboard local updater — runs every 5 mins during QB hours via launchd
# Reads Slack using your browser session token (xoxd-), pushes to GitHub

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$REPO_DIR/run_local.log"
SECRETS_FILE="$HOME/.qb_dashboard_secrets"

# ── Load token from secrets file ─────────────────────────────────────────────
if [ ! -f "$SECRETS_FILE" ]; then
  echo "$(date): ERROR — secrets file not found at $SECRETS_FILE" >> "$LOG_FILE"
  exit 1
fi
source "$SECRETS_FILE"

if [ -z "$SLACK_USER_TOKEN" ]; then
  echo "$(date): ERROR — SLACK_USER_TOKEN not set in $SECRETS_FILE" >> "$LOG_FILE"
  exit 1
fi

# ── Check if within QB hours (12:30–19:30 IST = 07:00–14:00 UTC) ─────────────
HOUR_UTC=$(TZ=UTC date +%H)
MIN_UTC=$(TZ=UTC date +%M)
TIME_UTC=$((HOUR_UTC * 60 + MIN_UTC))
QB_START=420   # 07:00 UTC
QB_END=840     # 14:00 UTC

if [ "$TIME_UTC" -lt "$QB_START" ] || [ "$TIME_UTC" -gt "$QB_END" ]; then
  echo "$(date): Outside QB hours (UTC $HOUR_UTC:$MIN_UTC) — skipping" >> "$LOG_FILE"
  exit 0
fi

# ── Run the updater ───────────────────────────────────────────────────────────
echo "$(date): Running updater …" >> "$LOG_FILE"
export SLACK_USER_TOKEN
cd "$REPO_DIR"
python3 updater.py >> "$LOG_FILE" 2>&1

# ── Git push if data.json changed ────────────────────────────────────────────
cd "$REPO_DIR"
git add data.json
if git diff --cached --quiet; then
  echo "$(date): No changes to push." >> "$LOG_FILE"
else
  TIMESTAMP=$(TZ='Asia/Kolkata' date '+%Y-%m-%d %H:%M IST')
  git commit -m "Live update: ${TIMESTAMP}" >> "$LOG_FILE" 2>&1
  git pull --rebase origin main >> "$LOG_FILE" 2>&1
  git push origin main >> "$LOG_FILE" 2>&1
  echo "$(date): Pushed to GitHub." >> "$LOG_FILE"
fi
