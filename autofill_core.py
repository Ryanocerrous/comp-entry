#!/usr/bin/env python3
"""
Core utilities shared by the CLI and GUI front-ends for the smart autofill tool.
Encapsulates Selenium setup, heuristic field matching, and submission workflow.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# --- Logging setup ---
logger = logging.getLogger("autofill")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


@dataclass
class FillAction:
    label: str
    tag: str
    input_type: str
    mapped_key: Optional[str]
    value: Optional[str]
    score: int
    filled: bool


@dataclass
class AutofillOutcome:
    fill_actions: List[FillAction]
    screenshot_path: Optional[Path] = None
    post_submit_screenshot_path: Optional[Path] = None
    submitted: bool = False
    aborted_reason: Optional[str] = None
    error: Optional[str] = None


# --- Heuristics: keywords mapped to data keys ---
FIELD_KEYWORDS: Dict[str, Sequence[str]] = {
    "email": ["email", "e-mail", "your email", "mail"],
    "first_name": ["first", "given", "forename"],
    "last_name": ["last", "surname", "family"],
    "phone": ["phone", "tel", "mobile", "contact"],
    "address": ["address", "addr", "street"],
    "city": ["city", "town"],
    "postcode": ["post", "zip", "postcode", "postal"],
    "comments": ["comment", "message", "tell us", "why", "entry"],
    "name": ["name", "full name"],
}

# Order to attempt for text inputs (prefer type=email / tel)
INPUT_TYPES_PRIORITY = ["email", "tel", "text", "search", "url"]


def load_json(path: Path | str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def human_delay(min_seconds: float = 0.5, max_seconds: float = 1.5) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def find_visible_inputs(driver: webdriver.Chrome) -> List[Any]:
    ensure_active_window(driver)
    inputs = driver.find_elements(By.CSS_SELECTOR, "input, textarea, select")
    visible = []
    for element in inputs:
        try:
            if (
                element.is_displayed()
                and element.size.get("height", 0) > 0
                and element.size.get("width", 0) > 0
            ):
                visible.append(element)
        except Exception:
            continue
    return visible


def element_label_text(driver: webdriver.Chrome, element: Any) -> str:
    """Try multiple ways to get a label for the element for heuristic matching."""
    try:
        aria = element.get_attribute("aria-label") or ""
        placeholder = element.get_attribute("placeholder") or ""
        name = element.get_attribute("name") or ""
        element_id = element.get_attribute("id") or ""
        label_text = ""

        if element_id:
            try:
                label_el = driver.find_element(By.CSS_SELECTOR, f"label[for='{element_id}']")
                label_text = (label_el.text or label_el.get_attribute("innerText") or "")
            except NoSuchElementException:
                label_text = ""

        combined = " ".join([aria, placeholder, label_text, name, element_id]).strip()
        return combined.lower()
    except Exception:
        return ""


def score_field(label_lower: str) -> tuple[Optional[str], int]:
    """Return (best_match_key, score) where higher score = more confident."""
    best_key = None
    best_score = 0
    if not label_lower:
        return None, 0
    for key, keywords in FIELD_KEYWORDS.items():
        for keyword in keywords:
            if keyword in label_lower:
                score = len(keyword) + (2 if label_lower.startswith(keyword) else 0)
                if score > best_score:
                    best_score = score
                    best_key = key
    return best_key, best_score


def choose_value_for_field(key: str, data: Dict[str, Any]) -> Optional[str]:
    if key in data and data[key]:
        return str(data[key])
    if key == "first_name" and "name" in data:
        return str(data["name"]).split()[0]
    if key == "last_name" and "name" in data:
        parts = str(data["name"]).split()
        return parts[-1] if len(parts) > 1 else ""
    if key == "name" and "first_name" in data and "last_name" in data:
        return f"{data['first_name']} {data['last_name']}".strip()
    return None


def safe_send_keys(element: Any, value: str) -> bool:
    try:
        element.clear()
    except Exception:
        pass
    try:
        element.send_keys(value)
        return True
    except Exception as exc:
        logger.debug("send_keys failed: %s", exc)
        return False


def _status(message: str, status_callback: Optional[Callable[[str], None]]) -> None:
    logger.info(message)
    if status_callback:
        try:
            status_callback(message)
        except Exception:
            logger.debug("Status callback raised", exc_info=True)

def ensure_active_window(driver: webdriver.Chrome, wait_seconds: float = 5.0) -> None:
    """Ensure Selenium is focused on a valid, open window."""
    end_time = time.time() + wait_seconds
    while True:
        try:
            _ = driver.current_window_handle  # access to confirm window is alive
            return
        except WebDriverException:
            handles = driver.window_handles
            if handles:
                for handle in reversed(handles):
                    try:
                        driver.switch_to.window(handle)
                        _ = driver.current_window_handle
                        return
                    except WebDriverException:
                        continue
        if time.time() > end_time:
            raise
        time.sleep(0.2)


def perform_autofill(
    cfg: Dict[str, Any],
    data: Dict[str, Any],
    confirm_submit: Optional[Callable[[List[FillAction], Path], bool]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> AutofillOutcome:
    """
    Execute the autofill workflow.

    confirm_submit is invoked with the list of FillAction entries and the pre-submit
    screenshot path. It should return True to proceed, False to abort.
    """
    outcome = AutofillOutcome(fill_actions=[])

    url = cfg.get("url", "").strip()
    if not url:
        outcome.error = "Configuration missing a URL to open."
        _status(outcome.error, status_callback)
        return outcome

    headless = bool(cfg.get("headless", False))
    wait_timeout = int(cfg.get("wait_timeout", 10))
    submit_selector = cfg.get("submit_selector") or "button[type='submit'], input[type='submit'], button"
    human_delay_bounds = cfg.get("human_delay_seconds", [0.4, 1.0])
    if not isinstance(human_delay_bounds, (list, tuple)) or len(human_delay_bounds) != 2:
        human_delay_bounds = [0.4, 1.0]
    human_delay_bounds = [float(human_delay_bounds[0]), float(human_delay_bounds[1])]
    if human_delay_bounds[0] > human_delay_bounds[1]:
        human_delay_bounds.sort()

    screenshot_dir = Path(cfg.get("screenshot_dir") or Path.cwd())
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    pause_on_captcha = bool(cfg.get("pause_on_captcha", False))

    chrome_opts = Options()
    if headless:
        chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--start-maximized")
    chrome_opts.add_argument("--disable-blink-features=AutomationControlled")

    driver = None
    driver = None
    webdriver_path = cfg.get("webdriver_path")
    try:
        if webdriver_path:
            driver = webdriver.Chrome(service=Service(webdriver_path), options=chrome_opts)
        else:
            driver = webdriver.Chrome(options=chrome_opts)
    except WebDriverException as exc:
        msg = str(exc).lower()
        if "unable to obtain driver" in msg or "driver location" in msg:
            try:
                from webdriver_manager.chrome import ChromeDriverManager  # type: ignore

                _status("Attempting to download ChromeDriver via webdriver_manager...", status_callback)
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_opts)
            except ImportError:
                outcome.error = (
                    "webdriver_manager is not installed. Install it with `pip install webdriver-manager` "
                    "or provide 'webdriver_path' in config.json."
                )
                _status(outcome.error, status_callback)
                return outcome
            except Exception as mgr_exc:  # pragma: no cover - fallback path
                outcome.error = (
                    "WebDriver error: failed to locate ChromeDriver automatically. "
                    "Install ChromeDriver manually or set 'webdriver_path' in config.\n"
                    f"Original error: {exc}\nwebdriver_manager: {mgr_exc}"
                )
                _status(outcome.error, status_callback)
                return outcome
        else:
            outcome.error = f"WebDriver error: {exc}"
            _status(outcome.error, status_callback)
            return outcome

    closing_delay = float(cfg.get("close_delay_seconds", 10))

    try:
        _status(f"Opening {url}", status_callback)
        driver.get(url)
        try:
            ensure_active_window(driver)
        except WebDriverException as exc:
            outcome.error = f"WebDriver window error: {exc}"
            _status(outcome.error, status_callback)
            return outcome
        WebDriverWait(driver, wait_timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        human_delay(*human_delay_bounds)

        try:
            visible_inputs = find_visible_inputs(driver)
        except WebDriverException as exc:
            outcome.error = f"WebDriver window error: {exc}"
            _status(outcome.error, status_callback)
            return outcome
        _status(f"Found {len(visible_inputs)} visible inputs", status_callback)

        for element in visible_inputs:
            tag = element.tag_name.lower()
            input_type = (element.get_attribute("type") or "").lower()
            if tag == "textarea":
                input_type = "textarea"

            label_text = element_label_text(driver, element)
            key_candidate, score = score_field(label_text)
            if input_type in INPUT_TYPES_PRIORITY:
                score += len(INPUT_TYPES_PRIORITY) - INPUT_TYPES_PRIORITY.index(input_type)

            if tag == "select" and score < 3:
                outcome.fill_actions.append(
                    FillAction(label_text, tag, input_type, None, None, score, False)
                )
                continue

            value = None
            filled = False
            if score >= 3 and key_candidate:
                value = choose_value_for_field(key_candidate, data)
                if value:
                    filled = safe_send_keys(element, value)
                    human_delay(*human_delay_bounds)
            outcome.fill_actions.append(
                FillAction(label_text, tag, input_type, key_candidate, value, score, filled)
            )

        timestamp = int(time.time())
        screenshot_path = screenshot_dir / f"autofill_preview_{timestamp}.png"
        driver.save_screenshot(str(screenshot_path))
        outcome.screenshot_path = screenshot_path
        _status(f"Saved snapshot for review: {screenshot_path}", status_callback)

        if confirm_submit:
            should_submit = confirm_submit(outcome.fill_actions, screenshot_path)
            if not should_submit:
                outcome.aborted_reason = "Submission cancelled by user."
                _status("Submission cancelled; exiting without submit.", status_callback)
                return outcome

        page_html = driver.page_source.lower()
        if any(token in page_html for token in ("recaptcha", "g-recaptcha", "captcha")):
            outcome.aborted_reason = "CAPTCHA-like content detected; submission skipped."
            _status(outcome.aborted_reason, status_callback)
            if pause_on_captcha:
                if headless:
                    _status("Headless mode prevents manual CAPTCHA solving. Consider setting headless=false.", status_callback)
                else:
                    try:
                        user_input = input(
                            "CAPTCHA detected. Solve it manually in the open browser.\n"
                            "Press ENTER to skip, or type SUBMITTED once you have sent the entry: "
                        ).strip().lower()
                        if user_input == "submitted":
                            outcome.submitted = True
                            outcome.aborted_reason = None
                    except EOFError:
                        pass
            return outcome

        submit_element = None
        try:
            submit_element = driver.find_element(By.CSS_SELECTOR, submit_selector)
        except NoSuchElementException:
            candidates = driver.find_elements(By.CSS_SELECTOR, "button, input[type='submit']")
            for candidate in candidates:
                text = (candidate.text or candidate.get_attribute("value") or "").lower()
                if any(k in text for k in ["submit", "enter", "confirm", "join", "register", "enter now"]):
                    submit_element = candidate
                    break

        if not submit_element:
            outcome.aborted_reason = f"Submit button not found using selector '{submit_selector}'."
            _status(outcome.aborted_reason, status_callback)
            return outcome

        human_delay(*human_delay_bounds)
        submit_element.click()
        _status("Clicked submit element; waiting for post-submit page.", status_callback)
        time.sleep(3)
        post_screenshot = screenshot_dir / f"autofill_after_submit_{timestamp}.png"
        driver.save_screenshot(str(post_screenshot))
        outcome.post_submit_screenshot_path = post_screenshot
        outcome.submitted = True
        _status(f"Submitted. Post-submit screenshot saved: {post_screenshot}", status_callback)
        return outcome
    except Exception as exc:
        outcome.error = f"Unexpected error: {exc}"
        _status(outcome.error, status_callback)
        return outcome
    finally:
        try:
            if closing_delay > 0:
                time.sleep(closing_delay)
        except KeyboardInterrupt:
            pass
        finally:
            if driver:
                driver.quit()
