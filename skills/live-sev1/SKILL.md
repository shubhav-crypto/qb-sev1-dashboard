---
name: live-sev1
description: Refresh the "Live Sev1 Audit" tab of the QB Sev1 dashboard. Pulls every open Sev1 owned by engineers reporting to the six managers from OrgCS, reads each case's Slack Sev1 channel, and rewrites live-sev1.json. Use when asked to refresh/update the live Sev1 audit, or to check which open Sev1s are stalled.
user-invocable: true
disable-model-invocation: false
allowed-tools:
  - Bash
  - Read
  - Agent
---

# Live Sev1 Audit refresh

The dashboard is static (GitHub Pages). The tab reads `live-sev1.json` + `live-sev1-history.json`; ages and health flags are computed in the browser. This skill regenerates those files. It needs the OrgCS and Slack MCP tools, so it runs in an AI session, not as a cron script.

Managers (OrgCS `User.Name`): Nikhil Srivastava · Jyothirmayee Ramineni · Shubha V · Mirza Ameer Ali Baig · Sai Vikram Kinnera · Rohit Kala.

## Steps

1. **Owners → manager.** `Case.Owner.Manager` is not a valid path, and `Case_Owner_s_Manager__c` is mostly empty. Resolve via users:
   ```sql
   SELECT Name, Manager.Name FROM User
   WHERE Manager.Name IN ('Nikhil Srivastava','Jyothirmayee Ramineni','Shubha V','Mirza Ameer Ali Baig','Sai Vikram Kinnera','Rohit Kala')
   AND Id IN (SELECT OwnerId FROM Case WHERE IsClosed = false AND Severity_Level__c = 'Level 1 - Critical')
   ```
2. **Open Sev1s** (`Priority` is a numeric code in OrgCS; use `Severity_Level__c`):
   ```sql
   SELECT CaseNumber, Subject, Status, Sub_Status__c, Account.Name, Owner.Name, CreatedDate,
          AX_Sev1_Start_Time__c, Escalate_to_CIC__c, AX_CIC_Substatus__c,
          Last_Public_Activity_DateTime__c, LastModifiedDate, Last_Case_Comment__c, Active_Swarm__c,
          IsEscalated, Open_Red_Account__c, AX_Internal_Support_Bridge__c, AX_Resolver_Bridge__c,
          Out_of_Impact_Service_Restored__c, Age_days__c
   FROM Case
   WHERE IsClosed = false AND Severity_Level__c = 'Level 1 - Critical'
   AND OwnerId IN (SELECT Id FROM User WHERE Manager.Name IN (...six names...))
   ORDER BY CreatedDate DESC
   ```
   Save to `.agents/artifacts/live/cases.json` as a list of
   `{caseNum, subject, status, subStatus, account, owner, manager, created, sev1Start, cicFlag, cicSubstatus, lastPublicActivity, lastModified, lastComment, activeSwarm, isEscalated, redAccount, internalBridge, resolverBridge, serviceRestored, ageDays}` (manager = short name: Nikhil, Jyothirmayee, Shubha, Mirza, Sai Vikram, Rohit).
3. **Slack signals.** Channels are named `sev1-<caseNumber>-<account-slug>-sig`. Split the case numbers into 4 batches (`batch0.json`..`batch3.json`) and fan out read-only agents that write `batchN_slack.json` keyed by case number (channelId, channelName, channelCreated, msgCount24h/72h, lastHumanMessageTs, participants, roleSignals{cicEngaged, bridgeMentioned, customerUpdateGiven, csmOrTamPresent, engineeringOrBacklineEngaged, nextStepOrEtaStated}, recentMessages, summary). Never post to Slack. Booleans are `null` when unclear.
4. **Merge:** `python3 skills/live-sev1/scripts/build_live.py`
5. **Publish:** commit `live-sev1.json` and `live-sev1-history.json` and push to `main` (the Pages workflow deploys on push).

## CIC engaged vs not engaged

Every open Sev1 shows a CIC engaged / Not engaged badge (`Escalate_to_CIC__c`). For **non-CIC** cases the page judges OrgCS activity only: no Slack silence, missing-channel, next-step or CIC flags, and the Slack cell shows "not required". So the roster refresh only needs Slack reads for CIC-engaged cases (the CIC monitor already collects those); `batch*_slack.json` for non-CIC cases is optional context.

## Known limits

- Snapshot, not streaming: the tab shows its snapshot age and turns red after 6h.
- Slack `roleSignals` are model judgements from message text; treat "CIC not in channel" as a prompt to look, not proof.

## CIC monitor (every 10 minutes)

> Shareable version for collaborators: `.claude/skills/cic-monitor/SKILL.md` (run `/cic-monitor`, or `/loop 10m /cic-monitor`). It adds a double-run guard and records who refreshed.

"CIC-engaged" = open Level 1 Sev1 with `Escalate_to_CIC__c = true` under the six managers. Cases that have a disengagement `AX_CIC_Substatus__c` (e.g. "CIC no longer needed", "Resolved") have `Escalate_to_CIC__c = false` even though a CIC joined the channel earlier, so they stay in the full roster, not in the monitor.

Each run:
1. Run the step-2 SOQL with `AND Escalate_to_CIC__c = true`; write `.agents/artifacts/live/cic_cases.json` (same shape as `cases.json`, manager resolved through the step-1 owner map).
2. For each case find the channel (`slack_search_channels`, keyword = case number) and read the last ~60 messages. Write `cic_slack.json` keyed by case number with: `channelId, channelName, channelCreated, cicCommander, cicJoinedAt` (from the Communication Console "Added CIC Case Commander" post), `lastHumanTs, lastCicTs, humanMsgLast30m, humanMsgLast60m, humanMsgLast24h` (human = not a bot, not a "has joined" line), `participants, bridgeMentioned, lastBridgeTs, customerUpdateGiven, nextStepOrEtaStated, csmPresent, backlineEngaged` (null when unclear), `recent` (last 3 human messages, text <= 240 chars), `summary`.
3. `python3 skills/live-sev1/scripts/build_cic.py` writes `live-cic.json` and appends to `live-cic-history.json` (48h of 10-minute snapshots, used for the activity bars).
4. Publish (auto-publish is enabled by the user): `git add live-cic.json live-cic-history.json && git commit -m "CIC monitor refresh <time>" && git pull --rebase --autostash origin main && git push origin main`. Stage only those two files, never force-push, and skip the commit if nothing changed. The site is public: builders already strip Slack message bodies, case comments and bridge links; keep it that way.

Page thresholds (constants `CIC` in `index.html`): Slack human silence 30m warn / 60m critical, CIC commander silence 60m / 120m, OrgCS public update 1h Watch / 2h Needs action (CIC-engaged cases must be updated in OrgCS every 1-2h; the same limits apply in the roster table), no bridge after 60m, no customer update after 2h. The pulse turns red if the snapshot is older than 25 minutes.
