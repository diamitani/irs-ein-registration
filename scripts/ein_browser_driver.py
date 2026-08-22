#!/usr/bin/env python3
"""
ein_browser_driver.py
Browser automation for IRS EIN Assistant using Playwright.
Handles the full flow from irs.gov to EIN confirmation.
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import zoneinfo
import hashlib
import base64

try:
    from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None
    Page = None
    Browser = None
    PlaywrightTimeout = Exception

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    Fernet = None
    hashes = None
    PBKDF2HMAC = None


class SecureStorage:
    """Encrypt SSN/ITIN at rest using user-provided passphrase."""

    def __init__(self, passphrase: str):
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography package required. Run: pip install cryptography")
        salt = b'ein_skill_salt_v1'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


class IRSAvailability:
    """Check IRS EIN Assistant availability window."""

    @staticmethod
    def check() -> tuple[bool, str]:
        """Returns (is_available, message)."""
        tz = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(tz)
        wd = now.weekday()
        hour = now.hour
        minute = now.minute

        windows = {
            0: ("Mon", 6, 25),   # Mon 6am - 1am Tue
            1: ("Tue", 6, 25),
            2: ("Wed", 6, 25),
            3: ("Thu", 6, 25),
            4: ("Fri", 6, 25),   # Fri 6am - 1am Sat
            5: ("Sat", 6, 21),   # Sat 6am - 9pm
            6: ("Sun", 18, 24),  # Sun 6pm - midnight
        }

        day_name, open_hour, close_hour = windows[wd]
        current_time = hour + minute/60

        if wd <= 4:
            is_open = current_time >= open_hour or current_time < 1
        elif wd == 5:
            is_open = open_hour <= current_time <= close_hour
        else:
            is_open = current_time >= open_hour

        if is_open:
            return True, f"IRS EIN Assistant is open. Current ET: {now.strftime('%I:%M %p %Z')}"

        next_open = IRSAvailability._next_window(now)
        return False, f"IRS EIN Assistant is CLOSED. Current ET: {now.strftime('%I:%M %p %Z')}. Opens: {next_open}"

    @staticmethod
    def _next_window(now: datetime) -> str:
        wd = now.weekday()
        if wd == 5 and now.hour >= 21:
            return "Sunday 6:00 PM ET"
        elif wd == 6 and now.hour < 18:
            return "Today (Sunday) 6:00 PM ET"
        elif wd == 6 and now.hour >= 24:
            return "Monday 6:00 AM ET"
        else:
            return "Next business day 6:00 AM ET"


class EINFormDriver:
    """Drive the IRS EIN application form via Playwright."""

    IRS_START = "https://www.irs.gov/businesses/small-businesses-self-employed/get-an-employer-identification-number"
    EIN_ASSISTANT = "https://sa.www4.irs.gov/applyein/"
    SCREENSHOTS_DIR = Path("outputs/screenshots")
    DOWNLOADS_DIR = Path("outputs/documents/ein")

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.screenshots: list[str] = []
        self.error_log: list[dict] = []

    async def start(self):
        """Initialize browser session."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")

        self.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        self.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.page = await self.browser.new_page()
        await self.page.set_viewport_size({"width": 1280, "height": 900})

    async def stop(self):
        """Close browser and cleanup."""
        if self.browser:
            await self.browser.close()
        if hasattr(self, '_playwright'):
            await self._playwright.stop()

    async def _screenshot(self, name: str) -> str:
        """Capture screenshot for audit trail."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.SCREENSHOTS_DIR / f"{ts}_{name}.png"
        await self.page.screenshot(path=str(path), full_page=True)
        self.screenshots.append(str(path))
        return str(path)

    async def _safe_click(self, selector: str, timeout: int = 10000) -> bool:
        """Click with error handling."""
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            await self.page.click(selector)
            return True
        except PlaywrightTimeout:
            self.error_log.append({
                "action": "click",
                "selector": selector,
                "error": "Element not found",
                "solution": "The IRS page may have changed. Try refreshing or check if IRS site is down."
            })
            return False
        except Exception as e:
            self.error_log.append({
                "action": "click",
                "selector": selector,
                "error": str(e),
                "solution": "Check network connection. If persistent, IRS site may be experiencing issues."
            })
            return False

    async def _safe_fill(self, selector: str, value: str, timeout: int = 10000) -> bool:
        """Fill input with error handling."""
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            await self.page.fill(selector, value)
            return True
        except PlaywrightTimeout:
            self.error_log.append({
                "action": "fill",
                "selector": selector,
                "error": "Input field not found",
                "solution": "IRS form structure may have changed. Contact support or try paper SS-4."
            })
            return False
        except Exception as e:
            self.error_log.append({
                "action": "fill",
                "selector": selector,
                "error": str(e),
                "solution": "Try clearing browser cache or use a different network."
            })
            return False

    async def navigate_to_application(self) -> dict:
        """Navigate to EIN Assistant and return status."""
        try:
            await self.page.goto(self.IRS_START, wait_until="networkidle", timeout=30000)
            await self._screenshot("01_irs_landing")

            await self.page.goto(self.EIN_ASSISTANT, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            await self._screenshot("02_ein_assistant_start")

            return {"success": True, "message": "Navigated to IRS EIN Assistant"}
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "solution": "IRS website may be down or slow. Check https://www.irs.gov/help/irs-operations-status"
            }

    async def fill_legal_structure(self, entity_type: str, llc_details: dict = None) -> dict:
        """Fill the legal structure/entity type page."""
        try:
            entity_map = {
                "sole_proprietor": "input[value='soleProprietor']",
                "partnership": "input[value='partnership']",
                "corporation": "input[value='corporation']",
                "llc": "input[value='limitedLiabilityCompany']",
                "estate": "input[value='estateDeceased']",
                "trust": "input[value='trust']",
                "church_or_church_controlled_org": "input[value='churchOrg']",
                "other_nonprofit": "input[value='nonprofit']",
            }

            selector = entity_map.get(entity_type)
            if not selector:
                return {"success": False, "error": f"Unknown entity type: {entity_type}",
                        "solution": "Choose from: sole_proprietor, partnership, corporation, llc, estate, trust"}

            if not await self._safe_click(selector):
                return {"success": False, "error": self.error_log[-1]}

            await self._screenshot("03_legal_structure")

            if not await self._safe_click("button[type='submit'], input[type='submit']"):
                return {"success": False, "error": "Could not continue to next page"}

            await asyncio.sleep(2)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e),
                    "solution": "Try refreshing the page. IRS session may have timed out."}

    async def fill_responsible_party(self, name: str, ssn: str) -> dict:
        """Fill responsible party information (name and SSN/ITIN)."""
        try:
            if not await self._safe_fill("input[name='responsiblePartyName'], #responsiblePartyName", name):
                return {"success": False, "error": "Could not fill responsible party name"}

            ssn_clean = ssn.replace("-", "").replace(" ", "")
            if not await self._safe_fill("input[name='responsiblePartySsn'], #responsiblePartySsn", ssn_clean):
                return {"success": False, "error": "Could not fill SSN/ITIN"}

            await self._screenshot("04_responsible_party_REDACTED")

            if not await self._safe_click("button[type='submit'], input[type='submit']"):
                return {"success": False, "error": "Could not continue"}

            await asyncio.sleep(2)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def fill_address(self, address: dict) -> dict:
        """Fill mailing address."""
        try:
            await self._safe_fill("input[name='addressLine1'], #addressLine1", address["line1"])
            await self._safe_fill("input[name='city'], #city", address["city"])
            await self._safe_fill("select[name='state'], #state", address["state"])
            await self._safe_fill("input[name='zip'], #zip", address["zip"])

            await self._screenshot("05_address")

            if not await self._safe_click("button[type='submit'], input[type='submit']"):
                return {"success": False, "error": "Could not continue"}

            await asyncio.sleep(2)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def fill_business_details(self, details: dict) -> dict:
        """Fill business details (activity, reason, dates)."""
        try:
            if details.get("trade_name"):
                await self._safe_fill("input[name='tradeName']", details["trade_name"])

            await self._safe_fill("input[name='countyState']", details["county_state"])
            await self._safe_fill("input[name='principalActivity']", details["principal_activity"])
            await self._safe_fill("input[name='principalProduct']", details["principal_product_or_service"])
            await self._safe_fill("input[name='dateStarted']", details["date_business_started"])
            await self._safe_fill("input[name='closingMonth']", details.get("closing_month", "December"))
            await self._safe_fill("input[name='expectedEmployees']", str(details["expected_employees_12mo"]))

            await self._screenshot("06_business_details")

            if not await self._safe_click("button[type='submit'], input[type='submit']"):
                return {"success": False, "error": "Could not continue"}

            await asyncio.sleep(2)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_review_screen(self) -> dict:
        """Capture the final review screen for user confirmation."""
        try:
            await self._screenshot("07_final_review")

            review_text = await self.page.inner_text("body")

            return {
                "success": True,
                "screenshot": self.screenshots[-1],
                "review_text": review_text[:2000]
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def submit_application(self) -> dict:
        """Submit the application ONLY after explicit user confirmation."""
        try:
            if not await self._safe_click("button[type='submit'], input[value='Submit']"):
                return {"success": False, "error": "Submit button not found",
                        "solution": "Session may have timed out. Restart the application."}

            await asyncio.sleep(5)
            await self._screenshot("08_confirmation")

            page_text = await self.page.inner_text("body")

            ein_match = None
            import re
            ein_pattern = r'\b\d{2}-\d{7}\b'
            matches = re.findall(ein_pattern, page_text)
            if matches:
                ein_match = matches[0]

            return {
                "success": True if ein_match else False,
                "ein": ein_match,
                "confirmation_screenshot": self.screenshots[-1],
                "page_text": page_text[:3000]
            }
        except Exception as e:
            return {"success": False, "error": str(e),
                    "solution": "IRS may have experienced an error. Check screenshot for details."}

    async def download_confirmation_letter(self) -> dict:
        """Download the EIN confirmation letter (CP 575 equivalent)."""
        try:
            async with self.page.expect_download(timeout=30000) as download_info:
                await self._safe_click("a[href*='download'], button:has-text('Download'), a:has-text('Print')")

            download = await download_info.value
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = self.DOWNLOADS_DIR / f"EIN_Confirmation_{ts}.pdf"
            await download.save_as(str(save_path))

            return {"success": True, "file_path": str(save_path)}
        except PlaywrightTimeout:
            return {
                "success": False,
                "error": "Download not available or timed out",
                "solution": "You can print the confirmation page to PDF, or request CP 575 letter by calling IRS at 1-800-829-4933"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_errors(self) -> list[dict]:
        """Return all errors with solutions."""
        return self.error_log


async def run_ein_application(intake_data: dict, headless: bool = False) -> dict:
    """Main entry point for EIN application."""

    is_available, availability_msg = IRSAvailability.check()
    if not is_available:
        return {
            "success": False,
            "error": "IRS EIN Assistant is closed",
            "message": availability_msg,
            "solution": "Wait for the next availability window. You can prepare your data now."
        }

    driver = EINFormDriver(headless=headless)
    result = {"screenshots": [], "errors": []}

    try:
        await driver.start()

        nav_result = await driver.navigate_to_application()
        if not nav_result["success"]:
            return {**result, **nav_result}

        struct_result = await driver.fill_legal_structure(
            intake_data["entity_type"],
            intake_data.get("llc_details")
        )
        if not struct_result["success"]:
            return {**result, **struct_result, "errors": driver.get_errors()}

        party_result = await driver.fill_responsible_party(
            intake_data["responsible_party_name"],
            intake_data["responsible_party_tin"]
        )
        if not party_result["success"]:
            return {**result, **party_result, "errors": driver.get_errors()}

        addr_result = await driver.fill_address(intake_data["mailing_address"])
        if not addr_result["success"]:
            return {**result, **addr_result, "errors": driver.get_errors()}

        details_result = await driver.fill_business_details(intake_data)
        if not details_result["success"]:
            return {**result, **details_result, "errors": driver.get_errors()}

        review = await driver.get_review_screen()
        result["review_screenshot"] = review.get("screenshot")
        result["review_text"] = review.get("review_text")
        result["awaiting_confirmation"] = True
        result["screenshots"] = driver.screenshots

        return result

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "errors": driver.get_errors(),
            "screenshots": driver.screenshots,
            "solution": "An unexpected error occurred. Check screenshots for state. Restart if needed."
        }
    finally:
        await driver.stop()


if __name__ == "__main__":
    print("EIN Browser Driver - Test availability check")
    avail, msg = IRSAvailability.check()
    print(f"Available: {avail}")
    print(f"Message: {msg}")

    if not PLAYWRIGHT_AVAILABLE:
        print("\nWARNING: playwright not installed. Run: pip install playwright && playwright install chromium")
    if not CRYPTO_AVAILABLE:
        print("\nWARNING: cryptography not installed. Run: pip install cryptography")
