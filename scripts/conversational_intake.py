#!/usr/bin/env python3
"""
conversational_intake.py
Handles step-by-step field collection for EIN application.
Asks one question at a time, validates immediately, stores SSN securely.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import zoneinfo
import re
import getpass

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    import base64
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


class SecureFieldStorage:
    """Securely store sensitive fields like SSN."""

    SECURE_DIR = Path.home() / ".ein_secure"

    def __init__(self):
        self.SECURE_DIR.mkdir(mode=0o700, exist_ok=True)
        self._key = self._get_or_create_key()

    def _get_or_create_key(self) -> bytes:
        key_file = self.SECURE_DIR / ".key"
        if key_file.exists():
            return key_file.read_bytes()
        key = Fernet.generate_key()
        key_file.write_bytes(key)
        key_file.chmod(0o600)
        return key

    def store_ssn(self, ssn: str, reference_id: str) -> str:
        """Encrypt and store SSN, return reference ID."""
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography package required for secure storage")

        fernet = Fernet(self._key)
        encrypted = fernet.encrypt(ssn.encode())

        secure_file = self.SECURE_DIR / f"{reference_id}.enc"
        secure_file.write_bytes(encrypted)
        secure_file.chmod(0o600)

        return reference_id

    def retrieve_ssn(self, reference_id: str) -> Optional[str]:
        """Decrypt and retrieve SSN by reference ID."""
        if not CRYPTO_AVAILABLE:
            return None

        secure_file = self.SECURE_DIR / f"{reference_id}.enc"
        if not secure_file.exists():
            return None

        fernet = Fernet(self._key)
        encrypted = secure_file.read_bytes()
        return fernet.decrypt(encrypted).decode()

    def delete_ssn(self, reference_id: str):
        """Securely delete stored SSN."""
        secure_file = self.SECURE_DIR / f"{reference_id}.enc"
        if secure_file.exists():
            secure_file.write_bytes(b'\x00' * 100)
            secure_file.unlink()


class IRSAvailability:
    """Check IRS EIN Assistant availability."""

    @staticmethod
    def check() -> tuple[bool, str, Optional[str]]:
        """Returns (is_available, current_time_msg, next_window_if_closed)."""
        tz = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(tz)
        wd = now.weekday()
        hour = now.hour

        windows = {
            0: (6, 25), 1: (6, 25), 2: (6, 25), 3: (6, 25), 4: (6, 25),
            5: (6, 21),
            6: (18, 24),
        }
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        open_h, close_h = windows[wd]
        current = hour

        if wd <= 4:
            is_open = current >= open_h or current < 1
        elif wd == 5:
            is_open = open_h <= current < close_h
        else:
            is_open = current >= open_h

        time_str = now.strftime("%I:%M %p ET, %A")

        if is_open:
            return True, f"Current time: {time_str}. IRS is OPEN.", None

        if wd == 5 and current >= 21:
            next_open = "Sunday 6:00 PM ET"
        elif wd == 6 and current < 18:
            next_open = "Today 6:00 PM ET"
        else:
            next_day = (wd + 1) % 7
            if next_day == 6:
                next_open = "Sunday 6:00 PM ET"
            else:
                next_open = f"{days[next_day]} 6:00 AM ET"

        return False, f"Current time: {time_str}. IRS is CLOSED.", next_open


class ConversationalIntake:
    """Collect EIN application fields one at a time."""

    ENTITY_TYPES = {
        "1": ("sole_proprietor", "Sole Proprietor / Individual"),
        "2": ("llc", "Limited Liability Company (LLC)"),
        "3": ("corporation", "Corporation"),
        "4": ("partnership", "Partnership"),
        "5": ("trust", "Trust"),
        "6": ("estate", "Estate of a Deceased Individual"),
        "7": ("church_or_church_controlled_org", "Church or Religious Organization"),
        "8": ("other_nonprofit", "Other Nonprofit Organization"),
    }

    REASONS = {
        "1": ("started_new_business", "Started a new business"),
        "2": ("hired_employees", "Hired or will hire employees"),
        "3": ("banking_purpose", "Banking purposes only (no employees)"),
        "4": ("changed_type_of_organization", "Changed type of organization"),
        "5": ("purchased_going_business", "Purchased an existing business"),
        "6": ("created_a_trust", "Created a trust"),
        "7": ("created_a_pension_plan", "Created a pension plan"),
    }

    def __init__(self):
        self.data = {}
        self.secure_storage = SecureFieldStorage() if CRYPTO_AVAILABLE else None
        self.ssn_reference = None

    def get_questions(self) -> list[dict]:
        """Return ordered list of questions to ask."""
        return [
            {
                "field": "entity_type",
                "question": "What type of entity?",
                "options": self.ENTITY_TYPES,
                "validate": self._validate_entity_type,
            },
            {
                "field": "state_formation_confirmed",
                "question": "Is your LLC/Corporation registered with your state's Secretary of State?",
                "condition": lambda d: d.get("entity_type") in ("llc", "corporation"),
                "type": "yesno",
                "validate": self._validate_state_formation,
            },
            {
                "field": "legal_name",
                "question": "Legal name (exactly as registered)?",
                "validate": self._validate_not_empty,
            },
            {
                "field": "trade_name",
                "question": "Trade name / DBA (if different from legal name, or press Enter to skip)?",
                "optional": True,
            },
            {
                "field": "responsible_party_name",
                "question": "Responsible party full legal name (must be a person)?",
                "validate": self._validate_not_empty,
            },
            {
                "field": "ssn_permission",
                "question": "I need your SSN or ITIN to submit to the IRS. This will be encrypted and stored securely on your device only. It will NEVER be sent anywhere except the official IRS website. Do you consent?",
                "type": "yesno",
                "validate": self._validate_ssn_permission,
            },
            {
                "field": "responsible_party_tin",
                "question": "SSN or ITIN (format: XXX-XX-XXXX)?",
                "sensitive": True,
                "validate": self._validate_ssn,
            },
            {
                "field": "mailing_address.line1",
                "question": "Mailing address (street)?",
                "validate": self._validate_not_empty,
            },
            {
                "field": "mailing_address.city",
                "question": "City?",
                "validate": self._validate_not_empty,
            },
            {
                "field": "mailing_address.state",
                "question": "State (2-letter code, e.g., CA)?",
                "validate": self._validate_state,
            },
            {
                "field": "mailing_address.zip",
                "question": "ZIP code?",
                "validate": self._validate_zip,
            },
            {
                "field": "county_state",
                "question": "County and state of principal business location (e.g., 'Los Angeles, CA')?",
                "validate": self._validate_not_empty,
            },
            {
                "field": "reason_for_applying",
                "question": "Reason for applying?",
                "options": self.REASONS,
                "validate": self._validate_reason,
            },
            {
                "field": "date_business_started",
                "question": "Date business started (MM/DD/YYYY)?",
                "validate": self._validate_date,
            },
            {
                "field": "closing_month",
                "question": "Closing month of accounting year (default: December)?",
                "default": "December",
            },
            {
                "field": "expected_employees_12mo",
                "question": "Expected employees in next 12 months (0 if none)?",
                "validate": self._validate_number,
            },
            {
                "field": "principal_activity",
                "question": "Principal business activity (e.g., 'retail', 'consulting', 'manufacturing')?",
                "validate": self._validate_not_empty,
            },
            {
                "field": "principal_product_or_service",
                "question": "Principal product or service (e.g., 'software', 'legal services', 'clothing')?",
                "validate": self._validate_not_empty,
            },
            {
                "field": "prior_ein_ever_issued",
                "question": "Has this entity ever had an EIN before?",
                "type": "yesno",
            },
            {
                "field": "prior_ein",
                "question": "What was the prior EIN?",
                "condition": lambda d: d.get("prior_ein_ever_issued") == True,
                "validate": self._validate_ein_format,
            },
            {
                "field": "ein_today_check",
                "question": "Has this responsible party already received an EIN today? (IRS allows only 1 per day)",
                "type": "yesno",
                "validate": self._validate_daily_limit,
            },
        ]

    def _validate_not_empty(self, value: str) -> tuple[bool, str]:
        if value.strip():
            return True, ""
        return False, "This field is required."

    def _validate_entity_type(self, value: str) -> tuple[bool, str]:
        if value in self.ENTITY_TYPES:
            return True, ""
        return False, f"Choose 1-{len(self.ENTITY_TYPES)}."

    def _validate_state_formation(self, value: bool) -> tuple[bool, str]:
        if value:
            return True, ""
        return False, "STOP: You must register your LLC/Corporation with your state first. Apply for EIN after state formation is complete."

    def _validate_ssn_permission(self, value: bool) -> tuple[bool, str]:
        if value:
            return True, ""
        return False, "Cannot proceed without SSN consent. The IRS requires it for EIN applications."

    def _validate_ssn(self, value: str) -> tuple[bool, str]:
        clean = value.replace("-", "").replace(" ", "")
        if re.match(r'^\d{9}$', clean):
            return True, ""
        return False, "SSN must be 9 digits (XXX-XX-XXXX)."

    def _validate_state(self, value: str) -> tuple[bool, str]:
        states = ["AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"]
        if value.upper() in states:
            return True, ""
        return False, "Enter a valid 2-letter state code."

    def _validate_zip(self, value: str) -> tuple[bool, str]:
        if re.match(r'^\d{5}(-\d{4})?$', value):
            return True, ""
        return False, "Enter a valid ZIP (12345 or 12345-6789)."

    def _validate_reason(self, value: str) -> tuple[bool, str]:
        if value in self.REASONS:
            return True, ""
        return False, f"Choose 1-{len(self.REASONS)}."

    def _validate_date(self, value: str) -> tuple[bool, str]:
        try:
            datetime.strptime(value, "%m/%d/%Y")
            return True, ""
        except ValueError:
            return False, "Use MM/DD/YYYY format."

    def _validate_number(self, value: str) -> tuple[bool, str]:
        try:
            int(value)
            return True, ""
        except ValueError:
            return False, "Enter a number."

    def _validate_ein_format(self, value: str) -> tuple[bool, str]:
        if re.match(r'^\d{2}-\d{7}$', value):
            return True, ""
        return False, "EIN format: XX-XXXXXXX."

    def _validate_daily_limit(self, value: bool) -> tuple[bool, str]:
        if not value:
            return True, ""
        return False, "STOP: The IRS allows only 1 EIN per responsible party per day. Try again tomorrow."

    def set_field(self, field: str, value):
        """Set a field value, handling nested fields."""
        if "." in field:
            parts = field.split(".")
            if parts[0] not in self.data:
                self.data[parts[0]] = {}
            self.data[parts[0]][parts[1]] = value
        else:
            self.data[field] = value

    def store_ssn_securely(self, ssn: str) -> str:
        """Store SSN encrypted, return reference."""
        if self.secure_storage:
            ref = f"ein_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.secure_storage.store_ssn(ssn, ref)
            self.ssn_reference = ref
            return ref
        self.ssn_reference = "memory_only"
        return "memory_only"

    def get_collected_data(self) -> dict:
        """Return all collected data."""
        return self.data

    def get_summary(self) -> str:
        """Return human-readable summary for confirmation."""
        d = self.data
        lines = [
            "=== APPLICATION SUMMARY ===",
            f"Entity Type: {d.get('entity_type', 'N/A')}",
            f"Legal Name: {d.get('legal_name', 'N/A')}",
            f"Trade Name: {d.get('trade_name', 'None')}",
            f"Responsible Party: {d.get('responsible_party_name', 'N/A')}",
            f"SSN/ITIN: ***-**-{d.get('responsible_party_tin', '')[-4:] if d.get('responsible_party_tin') else 'N/A'}",
            f"Address: {d.get('mailing_address', {}).get('line1', '')}, {d.get('mailing_address', {}).get('city', '')}, {d.get('mailing_address', {}).get('state', '')} {d.get('mailing_address', {}).get('zip', '')}",
            f"County/State: {d.get('county_state', 'N/A')}",
            f"Reason: {d.get('reason_for_applying', 'N/A')}",
            f"Date Started: {d.get('date_business_started', 'N/A')}",
            f"Closing Month: {d.get('closing_month', 'December')}",
            f"Expected Employees: {d.get('expected_employees_12mo', 'N/A')}",
            f"Activity: {d.get('principal_activity', 'N/A')}",
            f"Product/Service: {d.get('principal_product_or_service', 'N/A')}",
            f"Prior EIN: {'Yes - ' + d.get('prior_ein', 'N/A') if d.get('prior_ein_ever_issued') else 'No'}",
            "=========================",
        ]
        return "\n".join(lines)


def format_question_for_claude(question: dict, current_data: dict) -> str:
    """Format a question for Claude to ask the user."""
    if question.get("condition") and not question["condition"](current_data):
        return None

    q = question["question"]

    if question.get("options"):
        opts = question["options"]
        opt_text = "\n".join([f"  {k}. {v[1]}" for k, v in opts.items()])
        q = f"{q}\n{opt_text}"

    if question.get("default"):
        q = f"{q} (default: {question['default']})"

    if question.get("type") == "yesno":
        q = f"{q} (yes/no)"

    return q


if __name__ == "__main__":
    is_open, time_msg, next_window = IRSAvailability.check()
    print(time_msg)
    if not is_open:
        print(f"Next opening: {next_window}")
