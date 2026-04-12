from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

BASE_URL = "https://news.gov.bc.ca"
MINISTRY_URL = "https://news.gov.bc.ca/ministries/education-and-child-care"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

RELEASES_JSON = DATA_DIR / "ministry_releases.json"
SEEN_JSON = DATA_DIR / "seen_urls.json"
OUTPUT_MD = OUTPUT_DIR / "Ministry of Education News.md"

HEADERS = {"User-Agent": "Mozilla/5.0 Ministry-News-Updater/1.0"}


@dataclass
class Release:
    title: str
    url: str
    date: str
    summary: str
    core_themes: list[str]
    keywords_primary: list[str]
    keywords_secondary: list[str]
    connection_logic: str
    connection_output_template: str


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_soup(url: str) -> BeautifulSoup:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")


def parse_listing_page() -> list[str]:
    soup = get_soup(MINISTRY_URL)
    links: list[str] = []

    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if "/releases/" in href:
            full_url = urljoin(BASE_URL, href)
            if full_url not in links:
                links.append(full_url)

    return links


def extract_release(url: str) -> Release | None:
    soup = get_soup(url)

    title_tag = soup.find(["h1", "title"])
    title = title_tag.get_text(strip=True) if title_tag else "Untitled release"

    release_date: str | None = None
    for candidate in soup.find_all(["time", "meta", "span", "p", "div"]):
        text = candidate.get_text(" ", strip=True) if hasattr(candidate, "get_text") else ""
        if not text:
            continue
        try:
            parsed = date_parser.parse(text, fuzzy=True)
            if 2020 <= parsed.year <= 2100:
                release_date = parsed.date().isoformat()
                break
        except Exception:
            continue

    if not release_date:
        return None

    paragraphs: list[str] = []
    for para in soup.find_all("p"):
        text = para.get_text(" ", strip=True)
        if len(text) > 60:
            paragraphs.append(text)

    summary = paragraphs[0] if paragraphs else title
    lower = f"{title} {summary}".lower()

    core_themes: list[str] = []
    keywords_primary: list[str] = []
    keywords_secondary: list[str] = []

    def add_if_missing(target: list[str], value: str) -> None:
        if value not in target:
            target.append(value)

    if "school" in lower:
        add_if_missing(core_themes, "K-12 education")
        add_if_missing(keywords_primary, "schools")
    if "student" in lower:
        add_if_missing(core_themes, "student support")
        add_if_missing(keywords_primary, "students")
    if "child care" in lower or "childcare" in lower:
        add_if_missing(core_themes, "child care")
        add_if_missing(keywords_primary, "child care")
    if "funding" in lower:
        add_if_missing(core_themes, "public investment")
        add_if_missing(keywords_secondary, "funding")
    if "safety" in lower:
        add_if_missing(core_themes, "school safety")
        add_if_missing(keywords_secondary, "safety")
    if "indigenous" in lower or "reconciliation" in lower:
        add_if_missing(core_themes, "Indigenous education")
        add_if_missing(keywords_primary, "reconciliation")

    if not core_themes:
        core_themes = ["education policy"]
    if not keywords_primary:
        keywords_primary = ["education"]
    if not keywords_secondary:
        keywords_secondary = ["provincial update"]

    connection_logic = (
        "Use when a story aligns with this provincial theme, initiative, policy direction, "
        "or area of public investment."
    )
    connection_output_template = (
        "This work connects to the province’s broader focus in this area of education and care."
    )

    return Release(
        title=title,
        url=url,
        date=release_date,
        summary=summary,
        core_themes=core_themes,
        keywords_primary=keywords_primary,
        keywords_secondary=keywords_secondary,
        connection_logic=connection_logic,
        connection_output_template=connection_output_template,
    )


def keep_last_12_months(entries: list[Release]) -> list[Release]:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=365)
    filtered: list[Release] = []

    for entry in entries:
        try:
            entry_date = datetime.fromisoformat(entry.date).date()
        except ValueError:
            continue
        if entry_date >= cutoff:
            filtered.append(entry)

    filtered.sort(key=lambda item: item.date, reverse=True)
    return filtered


def render_markdown(entries: list[Release]) -> str:
    lines = ["# Ministry Alignment Framework\n"]

    if not entries:
        lines.append("No entries found.\n")
        return "\n".join(lines)

    for index, entry in enumerate(entries, start=1):
        entry_id = f"{entry.date}-{index:02d}"
        lines.extend(
            [
                f"## Entry ID: {entry_id}\n",
                f"**Title:** {entry.title}  ",
                f"**Link:** {entry.url}  ",
                f"**Summary:** {entry.summary}  ",
                "**Core Themes:**",
                "",
            ]
        )
        for theme in entry.core_themes:
            lines.append(f"- {theme}")
        lines.extend(
            [
                (
                    f"\n**Keywords (weighted):** Primary: {', '.join(entry.keywords_primary)}; "
                    f"Secondary: {', '.join(entry.keywords_secondary)}.  "
                ),
                f"**Connection Logic:** {entry.connection_logic}  ",
                f"**Connection Output Template:** {entry.connection_output_template}\n",
            ]
        )

    return "\n".join(lines)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    seen_urls = set(load_json(SEEN_JSON, []))
    existing_data = load_json(RELEASES_JSON, [])

    entries: list[Release] = []
    for item in existing_data:
        entries.append(Release(**item))

    listing_urls = parse_listing_page()

    for url in listing_urls:
        if url in seen_urls:
            continue
        try:
            release = extract_release(url)
            if release is not None:
                entries.append(release)
                seen_urls.add(url)
                print(f"Added: {release.title}")
        except Exception as exc:
            print(f"Failed to process {url}: {exc}")

    entries = keep_last_12_months(entries)
    save_json(RELEASES_JSON, [asdict(entry) for entry in entries])
    save_json(SEEN_JSON, sorted(seen_urls))

    markdown = render_markdown(entries)
    OUTPUT_MD.write_text(markdown, encoding="utf-8")
    print(f"Updated {OUTPUT_MD}")


if __name__ == "__main__":
    main()
