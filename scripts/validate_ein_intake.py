#!/usr/bin/env python3
"""
validate_ein_intake.py
Local validation for EIN skill intake payloads and hard-stop guardrails.
Run: python validate_ein_intake.py intake.json
Exit code 0 = pass, 1 = fail (with reasons printed).
"""
import json
import sys
from datetime import datetime, time
import zoneinfo

REQUIRED_FIELDS = [
    "legal_name", "responsible_party_name", "responsible_party_tin",
    "mailing_address", "county_state", "entity_type", "reason_for_applying",
    "date_business_started", "closing_month", "expected_employees_12mo",
    "principal_activity", "principal_product_or_service", "prior_ein_ever_issued",
    "explicit_submission_consent",
]

def check_availability_window(now_et=None):
    tz = zoneinfo.ZoneInfo("America/New_York")
    now = now_et or datetime.now(tz)
    wd = now.weekday()  # 0=Mon .. 6=Sun
    t = now.time()
    if wd <= 3:  # Mon-Thu: 6am - 1am next day
        ok = t >= time(6, 0) or t <= time(1, 0)
    elif wd == 4:  # Fri: 6am - 1am next day (Sat)
        ok = t >= time(6, 0) or t <= time(1, 0)
    elif wd == 5:  # Sat: 6am - 9pm
        ok = time(6, 0) <= t <= time(21, 0)
    else:  # Sun: 6pm - midnight
        ok = t >= time(18, 0)
    return ok

def validate(payload: dict):
    errors = []
    for f in REQUIRED_FIELDS:
        if f not in payload or payload[f] in (None, ""):
            errors.append(f"Missing required field: {f}")

    if not payload.get("eligibility_checks", {}).get("within_availability_window", False):
        if not check_availability_window():
            errors.append("Outside IRS EIN Assistant availability window (ET).")

    if not payload.get("eligibility_checks", {}).get(
        "no_ein_issued_today_for_this_responsible_party", False
    ):
        errors.append("Daily limit guard not confirmed: responsible party may have already used today's EIN.")

    entity_type = payload.get("entity_type")
    if entity_type in ("llc", "corporation") and not payload.get("state_formation_confirmed", False):
        errors.append(f"{entity_type} requires state formation confirmation before EIN application.")

    tin = payload.get("responsible_party_tin", "")
    if tin and not tin.replace("-", "").isdigit():
        errors.append("responsible_party_tin must be numeric (SSN/ITIN/EIN format).")

    if payload.get("explicit_submission_consent") is not True:
        errors.append("explicit_submission_consent must be True and freshly obtained before any submit action.")

    return errors

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python validate_ein_intake.py <intake.json>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        data = json.load(f)
    errs = validate(data)
    if errs:
        print("VALIDATION FAILED:")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print("VALIDATION PASSED: intake payload is complete and eligible to proceed.")
    sys.exit(0)
