#!/usr/bin/env python3
"""
Weekly competition discovery script.

Currently supports:
  - competitions-time.co.uk (via RSS + HTML detail scrape)
  - theprizefinder.com (via RSS + detail page scrape)

Other requested sources are stubbed with explanatory warnings because they
either block scripted access (Latest Deals), require a login (Woman Magazine,
Loquax), or render their listings client-side (MyOffers). See the README
section at the bottom of this file for suggested manual next steps.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree
import os
import smtplib
from email.message import EmailMessage

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook

from state_utils import CompetitionState, load_state, save_state

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    )
}

logger = logging.getLogger("competition_scraper")

EXCLUDE_KEYWORDS = (
    "instagram",
    "instagram.com",
    "insta",
    "tiktok",
    "tiktok.com",
    "tik tok",
)


@dataclass(slots=True)
class CompetitionEntry:
    source: str
    title: str
    link: str
    closing_date: Optional[dt.date]
    closing_text: str
    prize: str
    successful_submission: Optional[bool] = None
    is_new: bool = field(default=False, compare=False)
    raw_text: str = ""

    def as_row(self) -> List[str]:
        return [
            self.source,
            self.title,
            self.prize,
            self.link,
            self.closing_date.isoformat() if self.closing_date else "",
            self.closing_text,
            "" if self.successful_submission is None else str(self.successful_submission),
            "YES" if self.is_new else "",
        ]


class CompetitionSource:
    name: str = "Unknown"

    def fetch(self) -> List[CompetitionEntry]:
        raise NotImplementedError


def _strip_ordinal_suffix(value: str) -> str:
    return re.sub(r"(\d+)(st|nd|rd|th)", r"\1", value)


def _parse_human_date(value: str) -> Optional[dt.date]:
    value = value.strip()
    if not value:
        return None
    cleaned = _strip_ordinal_suffix(value)
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(cleaned)
    except ValueError:
        return None


class CompetitionsTimeSource(CompetitionSource):
    name = "Competitions Time"
    _rss_url = "https://www.competitions-time.co.uk/competitions/rss"

    def __init__(self) -> None:
        self._page_cache: Dict[str, BeautifulSoup] = {}

    def fetch(self) -> List[CompetitionEntry]:
        logger.info("Fetching RSS from %s", self._rss_url)
        feed_xml = self._request_text(self._rss_url)
        root = ElementTree.fromstring(feed_xml)

        entries: List[CompetitionEntry] = []
        for item in root.findall("./channel/item"):
            title = _xml_text(item, "title")
            link = _xml_text(item, "link")
            if not link:
                continue

            parsed = urlparse(link)
            slug = parsed.fragment
            if not slug:
                logger.debug("Skipping %s (no fragment/slug to locate card)", link)
                continue

            page_key = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
            soup = self._get_page_soup(page_key)
            card = soup.find(id=slug)
            if not card:
                logger.warning("Could not locate competition card '%s' on %s", slug, page_key)
                continue

            lines = list(card.stripped_strings)
            closing_text = ""
            closing_date = None
            prize_text = title.strip()

            for idx, text in enumerate(lines):
                if text.lower().startswith("closing"):
                    if idx + 2 < len(lines):
                        closing_text = lines[idx + 2]
                        closing_date = _parse_human_date(closing_text)
                if idx == 3:
                    prize_text = text

            entry_link = ""
            button = card.find("a", class_="entry-btn")
            if button and button.get("href"):
                entry_link = requests.compat.urljoin(page_key, button["href"])

            raw_text = " ".join(lines).strip()
            entries.append(
                CompetitionEntry(
                    source=self.name,
                    title=title.strip(),
                    prize=prize_text.strip(),
                    link=entry_link or link,
                    closing_date=closing_date,
                    closing_text=closing_text.strip(),
                    successful_submission=False,
                    raw_text=raw_text,
                )
            )

        return entries

    def _request_text(self, url: str) -> str:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return response.text

    def _get_page_soup(self, url: str) -> BeautifulSoup:
        if url not in self._page_cache:
            logger.info("Fetching competitions page %s", url)
            html = self._request_text(url)
            self._page_cache[url] = BeautifulSoup(html, "html.parser")
        return self._page_cache[url]


class PrizeFinderSource(CompetitionSource):
    name = "The Prize Finder"
    _rss_url = "https://www.theprizefinder.com/rss.xml"

    def fetch(self) -> List[CompetitionEntry]:
        logger.info("Fetching RSS from %s", self._rss_url)
        feed_xml = self._request_text(self._rss_url)
        root = ElementTree.fromstring(feed_xml)

        entries: List[CompetitionEntry] = []
        for item in root.findall("./channel/item"):
            title = _xml_text(item, "title")
            link = _xml_text(item, "link")
            if not link:
                continue

            try:
                detail_html = self._request_text(link)
            except requests.HTTPError as exc:
                logger.warning("Failed to fetch detail page %s: %s", link, exc)
                continue

            soup = BeautifulSoup(detail_html, "html.parser")
            closing_text = ""
            closing_date = None
            entry_link = link

            for field in soup.select("div.field"):
                label = field.select_one("div.field--label")
                content = field.select_one("div.field--item")
                if not label or not content:
                    continue
                label_text = label.get_text(strip=True)
                if label_text.startswith("Closing Date"):
                    closing_text = content.get_text(strip=True)
                    closing_date = _parse_human_date(closing_text)
                if label_text.startswith("Website Name"):
                    entry_link = link

            button = soup.select_one(".view-competition-button a")
            if button and button.get("href"):
                entry_link = requests.compat.urljoin(link, button["href"])

            page_text = soup.get_text(" ", strip=True)
            entries.append(
                CompetitionEntry(
                    source=self.name,
                    title=title.strip(),
                    prize=title.strip(),
                    link=entry_link,
                    closing_date=closing_date,
                    closing_text=closing_text,
                    successful_submission=False,
                    raw_text=page_text,
                )
            )

        return entries

    def _request_text(self, url: str) -> str:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return response.text


class BlockedSource(CompetitionSource):
    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def fetch(self) -> List[CompetitionEntry]:
        logger.warning("Skipping %s: %s", self.name, self.reason)
        return []


def _xml_text(node: ElementTree.Element, tag: str) -> str:
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _deduplicate(entries: Iterable[CompetitionEntry]) -> List[CompetitionEntry]:
    dedup: Dict[str, CompetitionEntry] = {}
    for entry in entries:
        key = _entry_key(entry)
        if key not in dedup:
            dedup[key] = entry
    return list(dedup.values())


def _entry_key(entry: CompetitionEntry) -> str:
    return entry.link or entry.title


def _sort(entries: List[CompetitionEntry]) -> List[CompetitionEntry]:
    def sort_key(item: CompetitionEntry) -> tuple:
        sentinel = dt.date.max
        return (item.closing_date or sentinel, item.source, item.title.lower())

    return sorted(entries, key=sort_key)


def export_to_excel(entries: List[CompetitionEntry], dest: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Competitions"
    ws.append(
        [
            "Source",
            "Title",
            "Prize",
            "Link",
            "Closing Date (ISO)",
            "Closing Date (Raw)",
            "Successful Submission",
            "Is New This Run",
        ]
    )
    for entry in entries:
        ws.append(entry.as_row())
    for column in ws.columns:
        max_length = max(len(str(cell.value)) if cell.value else 0 for cell in column)
        column_letter = column[0].column_letter
        ws.column_dimensions[column_letter].width = min(max_length + 2, 80)
    wb.save(dest)
    logger.info("Wrote %d entries to %s", len(entries), dest)


def build_summary(entries: List[CompetitionEntry], new_count: int) -> str:
    today = dt.date.today()
    closing_threshold = today + dt.timedelta(days=7)
    closing_soon = [
        e for e in entries if e.closing_date and today <= e.closing_date <= closing_threshold
    ]
    lines = [
        f"Total competitions: {len(entries)}",
        f"New this run: {new_count}",
        f"Closing within 7 days: {len(closing_soon)}",
    ]
    if closing_soon:
        lines.append("Soonest closing entries:")
        for entry in sorted(closing_soon, key=lambda e: e.closing_date):
            lines.append(f"- {entry.closing_date}: {entry.title} ({entry.source})")
    return "\n".join(lines)


def write_summary(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote summary report to %s", path)


def send_webhook(url: str, summary: str) -> None:
    try:
        response = requests.post(url, json={"text": summary}, timeout=10)
        response.raise_for_status()
        logger.info("Summary posted to webhook.")
    except Exception as exc:
        logger.warning("Failed to post summary to webhook: %s", exc)


def send_email(recipients: Sequence[str], summary: str) -> None:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "")

    if not smtp_host or not smtp_from:
        logger.warning("SMTP configuration incomplete; skipping email notification.")
        return

    msg = EmailMessage()
    msg["Subject"] = "Competition discovery summary"
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(summary)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info("Summary email sent to %s.", recipients)
    except Exception as exc:
        logger.warning("Failed to send summary email: %s", exc)


def run(
    output_path: Path,
    state_path: Path,
    summary_path: Optional[Path],
    summary_webhook: Optional[str],
    summary_email: Optional[List[str]],
) -> None:
    sources: List[CompetitionSource] = [
        CompetitionsTimeSource(),
        PrizeFinderSource(),
        BlockedSource("Latest Deals", "CloudFront blocks scripted requests (HTTP 403)."),
        BlockedSource("MyOffers", "Listings render client-side; requires site-specific API access."),
        BlockedSource("Loquax", "Competition index requires authenticated session."),
        BlockedSource("Woman Magazine", "Competition hub requires login and captcha checks."),
    ]

    collected: List[CompetitionEntry] = []
    for source in sources:
        try:
            items = source.fetch()
            logger.info("Collected %d entries from %s", len(items), source.name)
            collected.extend(items)
        except Exception as exc:
            logger.exception("Failed to collect from %s: %s", source.name, exc)

    deduped = _sort(_deduplicate(collected))
    if not deduped:
        logger.warning("No competition entries collected. Check connectivity or selectors.")
        export_to_excel(deduped, output_path)
        return

    state: CompetitionState = load_state(state_path)
    logger.info(
        "Loaded %d seen / %d submitted entries from %s",
        len(state.seen),
        len(state.submitted),
        state_path,
    )

    new_count = 0
    filtered_entries: List[CompetitionEntry] = []
    suppressed = 0
    excluded = 0
    for entry in deduped:
        key = _entry_key(entry)
        haystack = " ".join(
            filter(
                None,
                [
                    entry.title,
                    entry.prize,
                    entry.link,
                    entry.closing_text,
                    entry.raw_text,
                ],
            )
        ).lower()
        if any(keyword in haystack for keyword in EXCLUDE_KEYWORDS):
            excluded += 1
            continue
        if key in state.submitted:
            suppressed += 1
            continue
        if key not in state.seen:
            entry.is_new = True
            new_count += 1
        state.seen.add(key)
        filtered_entries.append(entry)

    if suppressed:
        logger.info("Suppressed %d competitions already submitted.", suppressed)
    if excluded:
        logger.info("Excluded %d competitions (Instagram/TikTok).", excluded)

    export_to_excel(filtered_entries, output_path)
    logger.info("Detected %d new competitions this run.", new_count)

    summary_text = build_summary(filtered_entries, new_count)
    logger.info("\n%s", summary_text)
    if summary_path:
        write_summary(summary_path, summary_text)

    if summary_webhook:
        send_webhook(summary_webhook, summary_text)
    if summary_email:
        send_email(summary_email, summary_text)

    save_state(state_path, state)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover UK competitions and export to Excel.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("competition_entries.xlsx"),
        help="Destination Excel file (default: competition_entries.xlsx)",
    )
    parser.add_argument(
        "-s",
        "--state",
        type=Path,
        default=Path("competition_state.json"),
        help="JSON file used to track previously seen competitions (default: competition_state.json)",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="Optional text file path to write a summary report.",
    )
    parser.add_argument(
        "--summary-webhook",
        help="POST the summary to this webhook URL (for Slack/Teams/etc).",
    )
    parser.add_argument(
        "--summary-email",
        nargs="+",
        help="Email the summary to these recipients (requires SMTP_* environment vars).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    run(
        args.output,
        args.state,
        args.summary,
        args.summary_webhook,
        args.summary_email,
    )


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# Roadmap notes
# ---------------------------------------------------------------------------
# * Latest Deals blocks non-browser requests behind CloudFront. You can capture
#   requests via the browser and supply the necessary cookies/headers, but
#   that requires per-account handling and may breach their terms.
# * MyOffers uses a JSON API that expects auth tokens generated in the SPA.
#   Investigate their network calls in DevTools and replicate with caution.
# * Loquax competition listings are now part of their XenForo forums and need
#   an authenticated session (plus CSRF token) to load consistently.
# * Woman Magazine competitions require login and frequently include CAPTCHAs,
#   so automation would need a human-in-the-loop solver.
# * Automatic form submission should be handled on a per-site basis and only
#   after ensuring the promotion terms permit automated entries.
