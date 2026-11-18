---
name: cic-monitor
description: Refresh and publish the CIC live monitor on the QB Sev1 dashboard (https://shubhav-crypto.github.io/qb-sev1-dashboard/). Pulls open CIC-engaged Sev1s for the six managers from OrgCS, reads each Sev1 Slack channel (read-only), rebuilds live-cic.json and pushes it to main so GitHub Pages redeploys. Use when asked to refresh/run/update the CIC monitor, or when you are on shift and need to keep it live (loop it every 10 minutes).
user-invocable: true
disable-model-invocation: false
allowed-tools:
  - Bash
  - Read
  - Write
---

# CIC monitor refresh (shareable)

One run = one refresh. For a continuous shift, run `/loop 10m /cic-monitor` (see "Running a shift").
Everything is read-only against OrgCS and Slack. The only thing written outside your machine is `live-cic.json` and `live-cic-history.json` pushed to `main`.

## Before you start (check once, stop and tell the user if any fails)

1. Your working directory is a clone of `shubhav-crypto/qb-sev1-dashboard` (`git remote -v`) and you have push access to it (`gh api repos/shubhav-crypto/qb-sev1-dashboard --jq .permissions.push` returns `true`).
2. You have the OrgCS MCP tool `mcp__orgcs__soqlQuery` and the Slack read tools `mcp__plugin_slack_slack__slack_search_channels` and `mcp__plugin_slack_slack__slack_read_channel`. If any is missing or unauthenticated, stop and say which one.
3. Never post, react or send in Slack. Never put Slack message text, case comments or any link (meeting, bridge, Slack, GUS) in a published file. The site is public.

## Steps

### 1. Guard against double runs
```bash
python3 skills/live-sev1/scripts/cic_publish.py check
```
It pulls `main` and prints JSON. If `"skip": true`, someone else refreshed less than 8 minutes ago: reply "Skipped: refreshed <ageMin> min ago by <refreshedBy>" and stop. If `"ok": false`, report the error and stop.
`previousSnapshot` is the timestamp of the last refresh; use it as the cutoff in step 3.

### 2. OrgCS: open CIC-engaged Sev1s under the six managers
```sql
SELECT CaseNumber, Subject, Status, Sub_Status__c, Account.Name, Owner.Name, OwnerId, CreatedDate,
       AX_Sev1_Start_Time__c, Escalate_to_CIC__c, AX_CIC_Substatus__c, Last_Public_Activity_DateTime__c,
       LastModifiedDate, Active_Swarm__c, IsEscalated, Open_Red_Account__c, AX_Internal_Support_Bridge__c,
       AX_Resolver_Bridge__c, Out_of_Impact_Service_Restored__c, Age_days__c
FROM Case
WHERE IsClosed = false AND Severity_Level__c = 'Level 1 - Critical' AND Escalate_to_CIC__c = true
AND OwnerId IN (SELECT Id FROM User WHERE Manager.Name IN
  ('Nikhil Srivastava','Jyothirmayee Ramineni','Shubha V','Mirza Ameer Ali Baig','Sai Vikram Kinnera','Rohit Kala'))
ORDER BY CreatedDate DESC
```
Notes: `Owner.Manager` is not a valid path, so do not use it. To get each owner's manager run `SELECT Name, Manager.Name FROM User WHERE Id IN (<OwnerIds>)` and map to the short names `Nikhil, Jyothirmayee, Shubha, Mirza, Sai Vikram, Rohit`. Reuse the manager already stored in the previous `live-cic.json` for an owner you have seen before.

Write `.agents/artifacts/live/cic_cases.json` (create the folder; it is not committed). A list with one object per case:
```json
{"caseNum":"484960341","subject":"…","status":"Working","subStatus":"In Progress","account":"…","owner":"…","manager":"Sai Vikram",
 "created":"<CreatedDate>","sev1Start":"<AX_Sev1_Start_Time__c>","cicFlag":true,"cicSubstatus":null,
 "lastPublicActivity":"<Last_Public_Activity_DateTime__c or null>","lastModified":"<LastModifiedDate>","lastComment":"",
 "activeSwarm":true,"isEscalated":false,"redAccount":false,"internalBridge":null,"resolverBridge":null,
 "serviceRestored":false,"ageDays":0.08}
```
Keep `lastComment` empty and set the bridge fields to `null` (the builder also strips them).
If the query returns zero cases, write `[]` to both JSON files and still build and publish, so the page shows "none".

### 3. Slack: one channel per case (read-only)
Channels are named `sev1-<caseNumber>-<account-slug>-sig`.
- For a case already in the previous `live-cic.json`, reuse `slack.channelId` and read only messages since `previousSnapshot` (`oldest` = its epoch seconds, `response_format: concise`, `limit` 50).
- For a new case, `slack_search_channels` with keywords `["sev1-<caseNumber>"]` (public and private), then read the latest 40 messages.
- If no channel exists yet, set `channelId` to `null`.

"Human" = a real person's message: not a bot, not a "has joined the channel" line, not a swarm-record or checklist post. The CIC commander is named in the Communication Console post "Added CIC Case Commander".

Write `.agents/artifacts/live/cic_slack.json`, keyed by case number:
```json
{"484960341":{"channelId":"C…","channelName":"sev1-484960341-…-sig","channelCreated":"<ISO>",
 "cicCommander":"Name","cicJoinedAt":"<ISO>","lastHumanTs":"<ISO>","lastCicTs":"<ISO or null>",
 "humanMsgLast30m":0,"humanMsgLast60m":3,"humanMsgLast24h":12,
 "participants":["Name"],"bridgeMentioned":true,"lastBridgeTs":"<ISO or null>",
 "customerUpdateGiven":null,"nextStepOrEtaStated":true,"csmPresent":true,"backlineEngaged":null,
 "recent":[{"ts":"<ISO>","author":"Name","text":""}],"summary":"one factual sentence, no links, no quoted message text"}}
```
- Counts are relative to now. For an existing case carry the previous `humanMsgLast24h` forward and add the new human messages; recompute the 30m and 60m counts from timestamps. A slightly low 24h count is acceptable.
- Carry forward unchanged fields (`cicCommander`, `cicJoinedAt`, `channelCreated`, `participants`) from the previous snapshot and add new people.
- Use `null` for booleans you cannot tell. `recent.text` is always `""`.
- Timestamps are ISO UTC. Slack shows IST: subtract 5:30.

### 4. Build
```bash
python3 skills/live-sev1/scripts/build_cic.py
```
It stores who refreshed (your GitHub login) in `meta.refreshedBy`, strips comments and bridge links, and appends the history file. Then confirm it is clean: `grep -c http live-cic.json live-cic-history.json` must print `0` for both.

### 5. Publish
```bash
python3 skills/live-sev1/scripts/cic_publish.py publish
```
It refuses if a URL slipped in, stages only the two data files, skips the commit if nothing changed, rebases on `origin/main` and pushes. Never force-push and never stage any other file. If your session requires a commit attribution line, pass it with `--trailer "<line>"`. If it prints `PUSH FAILED`, report that and stop; do not retry with force.

### 6. Report one line
`<N> CIC cases · Needs action: <cases> · Watch: <cases> · push <succeeded|failed>`

Judge Needs action / Watch with the page's thresholds:
| Signal | Watch | Needs action |
|---|---|---|
| Last human Slack message | 30 min | 60 min |
| Last CIC post | 60 min | 120 min |
| Last public OrgCS update | 1 h | 2 h |
Also Watch: no bridge after 60 min, or no next step / ETA stated. Case age under the threshold is exempt.

## Running a shift
- One person at a time. Step 1 skips automatically if someone else refreshed in the last 8 minutes, so a short overlap at handoff is safe.
- Start: `/loop 10m /cic-monitor`. Stop: end the loop or close the session. The loop lives only in your session and expires after 7 days.
- Keep the laptop awake and online while on shift (on a Mac: `caffeinate -dims`).
- Handoff: outgoing person stops the loop, incoming person starts theirs. The page shows who last refreshed and turns red with "STALE" if nobody has for 25 minutes.

## What not to do
Do not edit `index.html` or other data files as part of a refresh. Do not commit `.agents/`. Do not publish message text, comments, bridge/meeting links or Slack permalinks. Do not post to Slack.
