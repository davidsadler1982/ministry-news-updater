from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

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

HEADERS = {"User-Agent": "Mozilla/5.0 Ministry-News-Updater/1.1"}
SITE_TITLE_GARBAGE = {
    "BC Gov News",
    "B.C. Gov News",
    "News",
}


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


def clean_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def title_case_preserving_apostrophes(text: str) -> str:
    if text.isupper():
        return text.title()
    return text


def get_soup(url: str) -> BeautifulSoup:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")


def parse_listing_page() -> list[str]:
    soup = get_soup(MINISTRY_URL)
    links: list[str] = []
    seen: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if "/releases/" not in href:
            continue

        full_url = clean_url(urljoin(BASE_URL, href))
        if full_url in seen:
            continue

        seen.add(full_url)
        links.append(full_url)

    return links


def extract_title(soup: BeautifulSoup) -> str:
    meta_og = soup.find("meta", attrs={"property": "og:title"})
    if meta_og and meta_og.get("content"):
        title = clean_text(meta_og["content"])
        if title and title not in SITE_TITLE_GARBAGE:
            return title

    for selector in ["h1", "main h1", ".news-title", ".page-title"]:
        node = soup.select_one(selector)
        if node:
            title = clean_text(node.get_text(" ", strip=True))
            if title and title not in SITE_TITLE_GARBAGE:
                return title

    if soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))
        title = re.sub(r"\s*[\-|–|—]\s*BC Gov News.*$", "", title).strip()
        if title and title not in SITE_TITLE_GARBAGE:
            return title

    return "Untitled release"


def extract_date(soup: BeautifulSoup) -> str | None:
    meta_candidates = [
        ("meta", {"property": "article:published_time"}, "content"),
        ("meta", {"name": "publish-date"}, "content"),
        ("meta", {"name": "date"}, "content"),
    ]
    for tag, attrs, field in meta_candidates:
        node = soup.find(tag, attrs=attrs)
        if node and node.get(field):
            try:
                return date_parser.parse(str(node.get(field))).date().isoformat()
            except Exception:
                pass

    for node in soup.find_all(["time", "span", "p", "div"]):
        text = clean_text(node.get_text(" ", strip=True))
        if not text or len(text) > 80:
            continue
        try:
            parsed = date_parser.parse(text, fuzzy=True)
            if 2020 <= parsed.year <= 2100:
                return parsed.date().isoformat()
        except Exception:
            continue

    return None


def extract_summary(soup: BeautifulSoup, title: str) -> str:
    paragraphs: list[str] = []
    for para in soup.find_all("p"):
        text = clean_text(para.get_text(" ", strip=True))
        if len(text) < 50:
            continue
        lower = text.lower()
        if lower.startswith("for more information"):
            continue
        if "backgrounders" in lower and len(text) < 120:
            continue
        if text == title:
            continue
        paragraphs.append(text)

    if not paragraphs:
        return title

    first = paragraphs[0]
    first = re.sub(r"^[A-Z][a-z]+\s*,\s*B\.C\.\s*[–-]\s*", "", first)
    first = re.sub(r"^[A-Z][a-z]+\s*[–-]\s*", "", first)
    return clean_text(first)


def classify_release(title: str, summary: str) -> tuple[list[str], list[str], list[str], str, str]:
    text = f"{title} {summary}".lower()

    core_themes: list[str] = []
    primary: list[str] = []
    secondary: list[str] = []

    def add_unique(target: list[str], value: str) -> None:
        if value not in target:
            target.append(value)

    if any(term in text for term in ["pink shirt", "bullying", "kindness", "inclusive"]):
        add_unique(core_themes, "inclusive culture")
        add_unique(primary, "anti-bullying")
        add_unique(secondary, "respect")
    if any(term in text for term in ["black excellence", "anti-racist", "anti-racism", "barriers"]):
        add_unique(core_themes, "equity and inclusion")
        add_unique(primary, "equity")
        add_unique(secondary, "anti-racism")
    if any(term in text for term in ["reconciliation", "orange shirt", "indigenous", "first nations", "métis"]):
        add_unique(core_themes, "Indigenous education")
        add_unique(primary, "reconciliation")
        add_unique(secondary, "Indigenous learning")
    if any(term in text for term in ["seismic", "safety", "safe-access", "safe access", "protected"]):
        add_unique(core_themes, "school safety")
        add_unique(primary, "school safety")
        add_unique(secondary, "student protection")
    if any(term in text for term in ["classroom", "school spaces", "capacity", "temporary school", "new school"]):
        add_unique(core_themes, "learning spaces")
        add_unique(primary, "school capacity")
        add_unique(secondary, "capital projects")
    if any(term in text for term in ["mental health", "erase", "well-being", "wellbeing"]):
        add_unique(core_themes, "student well-being")
        add_unique(primary, "mental health")
        add_unique(secondary, "student support")
    if any(term in text for term in ["feed", "feeding futures", "meal", "food security"]):
        add_unique(core_themes, "student well-being")
        add_unique(primary, "food security")
        add_unique(secondary, "student health")
    if any(term in text for term in ["substance-use", "substance use", "prevention", "healthy decisions"]):
        add_unique(core_themes, "health education")
        add_unique(primary, "prevention")
        add_unique(secondary, "student health")
    if any(term in text for term in ["child care", "childcare", "licensed spaces", "$10-a-day", "10-a-day"]):
        add_unique(core_themes, "child care access")
        add_unique(primary, "child care")
        add_unique(secondary, "affordability")
    if any(term in text for term in ["education week", "student success", "school year", "achievement", "educators"]):
        add_unique(core_themes, "student achievement")
        add_unique(primary, "student success")
        add_unique(secondary, "educator recognition")

    if not core_themes:
        add_unique(core_themes, "education policy")
    if not primary:
        add_unique(primary, "education")
    if not secondary:
        add_unique(secondary, "provincial update")

    if "school safety" in core_themes:
        logic = "Use when a story involves school safety, student protection, or efforts to maintain secure learning environments."
        template = "This connects to the province’s focus on keeping schools safe, respectful, and protected for students and staff."
    elif "child care access" in core_themes:
        logic = "Use when a story involves child care access, affordability, school-based child care, or support for families."
        template = "This reflects the province’s broader effort to expand affordable, high-quality child care for families."
    elif "Indigenous education" in core_themes:
        logic = "Use when a story includes Indigenous learning, reconciliation, language revitalization, or culturally responsive education."
        template = "This aligns with the province’s commitment to advancing Indigenous education and reconciliation in B.C. schools."
    elif "equity and inclusion" in core_themes or "inclusive culture" in core_themes:
        logic = "Use when a story highlights inclusion, belonging, anti-racism, or student identity and dignity."
        template = "This supports the province’s work to strengthen inclusive and equitable learning environments for students."
    elif "learning spaces" in core_themes:
        logic = "Use when a story involves new spaces, temporary facilities, school construction, or capacity planning."
        template = "This reflects the province’s focus on ensuring students have safe, functional learning spaces as communities grow and change."
    elif "student well-being" in core_themes:
        logic = "Use when a story includes mental health, nutrition, or other supports that help students feel ready to learn."
        template = "This connects to the province’s emphasis on student well-being as a foundation for learning and success."
    elif "health education" in core_themes:
        logic = "Use when a story involves health education, prevention, or helping students make safe and informed choices."
        template = "This aligns with the province’s focus on giving students the knowledge they need to make healthy and safe decisions."
    elif "student achievement" in core_themes:
        logic = "Use when a story highlights learning progress, academic milestones, year-end achievements, or appreciation for educators."
        template = "This reflects the province’s commitment to student success and recognition of the educators who support it."
    else:
        logic = "Use when a story aligns with this provincial theme, initiative, policy direction, or area of public investment."
        template = "This work connects to the province’s broader focus in this area of education and care."

    return core_themes[:3], primary[:3], secondary[:3], logic, template


def build_release(url: str) -> Release | None:
    soup = get_soup(url)
    title = extract_title(soup)
    date_value = extract_date(soup)
    if not date_value:
        return None

    summary = extract_summary(soup, title)
    core_themes, primary, secondary, logic, template = classify_release(title, summary)

    return Release(
        title=title,
        url=url,
        date=date_value,
        summary=summary,
        core_themes=core_themes,
        keywords_primary=primary,
        keywords_secondary=secondary,
        connection_logic=logic,
        connection_output_template=template,
    )


def keep_last_12_months(entries: list[Release]) -> list[Release]:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=365)
    filtered: list[Release] = []
    seen_urls: set[str] = set()

    for entry in entries:
        if entry.url in seen_urls:
            continue
        seen_urls.add(entry.url)
        try:
            entry_date = datetime.fromisoformat(entry.date).date()
        except ValueError:
            continue
        if entry_date >= cutoff:
            filtered.append(entry)

    filtered.sort(key=lambda item: (item.date, item.title), reverse=True)
    return filtered


def render_markdown(entries: list[Release]) -> str:
    if entries:
        start_date = entries[-1].date[:7]
        end_date = entries[0].date[:7]
        header = f"# Ministry Alignment Framework: {start_date} – {end_date}\n"
    else:
        header = "# Ministry Alignment Framework\n"

    lines = [header]
    if not entries:
        lines.append("No entries found.\n")
        return "\n".join(lines)

    counts_by_date: dict[str, int] = {}

    for entry in entries:
        counts_by_date.setdefault(entry.date, 0)
        counts_by_date[entry.date] += 1
        entry_id = f"{entry.date}-{counts_by_date[entry.date]:02d}"
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

    seen_urls = {clean_url(url) for url in load_json(SEEN_JSON, [])}
    existing_data = load_json(RELEASES_JSON, [])

    entries: list[Release] = []
    for item in existing_data:
        try:
            item["url"] = clean_url(item["url"])
            entries.append(Release(**item))
        except Exception:
            continue

    listing_urls = parse_listing_page()

    for url in listing_urls:
        if url in seen_urls:
            continue
        try:
            release = build_release(url)
            if release is not None:
                entries.append(release)
                seen_urls.add(url)
                print(f"Added: {release.title}")
        except Exception as exc:
            print(f"Failed to process {url}: {exc}")

    entries = keep_last_12_months(entries)
    save_json(RELEASES_JSON, [asdict(entry) for entry in entries])
    save_json(SEEN_JSON, sorted(seen_urls))
    OUTPUT_MD.write_text(render_markdown(entries), encoding="utf-8")
    print(f"Updated {OUTPUT_MD}")


if __name__ == "__main__":
    main()
