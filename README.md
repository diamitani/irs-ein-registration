# IRS EIN Registration Skill

Automate EIN applications via IRS.gov browser automation. Asks questions one at a time, checks IRS hours, encrypts SSN locally.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Test IRS availability
python scripts/ein_browser_driver.py

# Validate intake data
python scripts/validate_ein_intake.py test/sample_intake.json
```

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Skill definition — conversation flow, procedure, error handling |
| `scripts/ein_browser_driver.py` | Playwright automation for IRS.gov |
| `scripts/conversational_intake.py` | One-by-one field collection with validation |
| `scripts/validate_ein_intake.py` | Pre-flight validator (availability, daily limit, required fields) |
| `schemas/ein-intake.schema.json` | JSON Schema for SS-4 fields |
| `test/sample_intake.json` | Example valid intake payload |

## IRS Hours (Eastern Time)

- **Mon-Fri**: 6am - 1am (next day)
- **Saturday**: 6am - 9pm  
- **Sunday**: 6pm - midnight

## Security

- SSN encrypted with Fernet (AES-128-CBC) at `~/.ein_secure/`
- 600 permissions, user-only access
- Never logged in plaintext
- Secure deletion (overwrite with zeros before unlink)

## Error Handling

Every error returns:
1. What failed
2. Why it might have failed
3. What to do next

Fallback options:
- Phone: 1-800-829-4933
- Fax SS-4: 855-641-6935
