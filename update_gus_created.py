#!/usr/bin/env python3
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


DATA_PATH = Path(__file__).with_name("data.json")
IST = timezone(timedelta(hours=5, minutes=30))


def parse_timestamp(value):
    if not value:
        return None
    normalized = value.replace(" IST", "+05:30")
    normalized = re.sub(r"\.\d{3}\+0000$", "+00:00", normalized)
    parsed = datetime.fromisoformat(normalized)
    return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed


def case_number_from_subject(subject, case_numbers):
    matches = set(re.findall(r"(?<!\d)(47\d{7})(?!\d)", subject or ""))
    matches &= case_numbers
    return matches.pop() if len(matches) == 1 else None


def query_gus_records():
    query = (
        "SELECT Name, Subject__c, CreatedDate FROM ADM_Work__c "
        "WHERE CreatedDate >= 2026-04-01T00:00:00Z "
        "AND Subject__c LIKE '%47%'"
    )
    result = subprocess.run(
        [
            "sf",
            "data",
            "query",
            "--target-org",
            "gus",
            "--query",
            query,
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["result"]["records"]


def main():
    data = json.loads(DATA_PATH.read_text())
    case_numbers = {
        case["caseNum"] for day in data["days"] for case in day["cases"]
    }
    matches = {}
    for record in query_gus_records():
        case_number = case_number_from_subject(record.get("Subject__c"), case_numbers)
        if case_number:
            matches.setdefault(case_number, []).append(record)

    populated = 0
    for day in data["days"]:
        for case in day["cases"]:
            records = sorted(
                matches.get(case["caseNum"], []), key=lambda item: item["CreatedDate"]
            )
            existing_number = case.get("gusNumber") or case.get("gusInvestigation")
            record = next(
                (item for item in records if item["Name"] == existing_number), None
            )
            if record is None and records:
                record = records[0]

            case["gusNumber"] = record["Name"] if record else None
            case["gusCreatedAt"] = (
                parse_timestamp(record["CreatedDate"])
                .astimezone(IST)
                .isoformat(timespec="seconds")
                if record
                else None
            )
            channel_created = parse_timestamp(case.get("channelCreatedAt"))
            gus_created = parse_timestamp(case["gusCreatedAt"])
            case["gusPreExisting"] = (
                gus_created < channel_created if gus_created and channel_created else None
            )
            case.pop("gusInvestigation", None)
            populated += record is not None

    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Updated {populated} of {len(case_numbers)} unique case numbers")


if __name__ == "__main__":
    main()
