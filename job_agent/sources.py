from __future__ import annotations

import html
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .yc_source import YCCompanySource


@dataclass(frozen=True)
class JobListing:
    source: str
    source_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    salary: str = ""
    published_at: str = ""
    role_lane: str = ""
    search_region: str = ""


def strip_html(value: str) -> str:
    value = re.sub(r"<(br|p|li)(\s[^>]*)?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n+", "\n", value)).strip()


ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "software engineer": ("software", "developer", "platform engineer"),
    "backend engineer": ("backend", "back-end", "api engineer", "platform engineer"),
    "full stack engineer": ("full stack", "full-stack"),
    "ai engineer": ("ai engineer", "ai architect", "artificial intelligence"),
    "machine learning engineer": ("machine learning", "ml engineer"),
    "solutions engineer": ("solutions engineer", "solution engineer", "sales engineer", "forward deployed"),
    "applications engineer": ("applications engineer", "application engineer", "product applications"),
    "field applications engineer": ("field applications engineer", "field application engineer", "fae"),
    "platform engineer": ("platform engineer", "infrastructure engineer"),
    "fpga engineer": ("fpga engineer", "fpga design", "fpga developer"),
    "fpga verification engineer": ("fpga verification", "fpga validation", "fpga test"),
    "soc design engineer": ("soc", "system-on-chip", "silicon design"),
    "asic rtl engineer": ("asic", "rtl", "digital design"),
    "design verification engineer": ("verification engineer", "design verification", "uvm"),
    "serdes validation engineer": ("serdes", "signal integrity"),
    "mixed signal validation engineer": ("mixed-signal", "mixed signal", "ams validation"),
    "hardware validation engineer": ("hardware", "validation engineer", "test engineer", "embedded engineer"),
    "firmware engineer": ("firmware", "embedded systems", "microcontroller", "microcontrollers"),
    "power electronics engineer": ("power electronics", "power engineer", "power conversion"),
    "analog hardware engineer": ("analog", "hardware engineer", "electrical engineer"),
    "pcb design engineer": ("pcb", "circuit board", "board design", "hardware design"),
    "game developer": ("game developer", "game engineer", "gameplay engineer", "unity", "godot"),
    "unity developer": ("unity", "unity developer", "unity engineer", "c#"),
    "gameplay engineer": ("gameplay", "gameplay engineer", "game systems"),
    "technical game designer": ("technical game designer", "game designer", "game systems"),
    "xr engineer": ("xr", "vr", "ar", "mixed reality", "unity"),
}

ROLE_LANE_ORDER = (
    "Software Engineering",
    "AI / ML Engineering",
    "Applications Engineering",
    "Solutions Engineering",
    "Power, Board & Hardware",
    "FPGA Engineering",
    "Chip Design & Verification",
    "Game Development",
)

EXCLUDED_NON_ENGINEERING_TITLE_TERMS = (
    "account manager", "customer success manager", "technical account manager",
    "marketer", "marketing", "recruiter", "talent acquisition", "product manager",
    "program manager", "project manager", "sales representative", "business development",
)


def role_lane_for_query(query: str) -> str:
    value = query.lower()
    if "application" in value or value.strip() == "fae":
        return "Applications Engineering"
    if "fpga" in value:
        return "FPGA Engineering"
    if "solution" in value or "forward deployed" in value:
        return "Solutions Engineering"
    if _is_game_or_xr_text(value):
        return "Game Development"
    if any(term in value for term in ("ai ", "machine learning", "ml engineer")):
        return "AI / ML Engineering"
    if any(term in value for term in ("soc", "asic", "rtl", "verification", "serdes", "mixed signal", "mixed-signal", "fpga")):
        return "Chip Design & Verification"
    if any(term in value for term in ("power", "analog", "hardware", "pcb", "board", "electrical")):
        return "Power, Board & Hardware"
    return "Software Engineering"


def classify_role_lane(title: str, query: str = "") -> str:
    if query:
        return role_lane_for_query(query)
    value = title.lower()
    if "applications engineer" in value or "application engineer" in value or re.search(r"\bfae\b", value):
        return "Applications Engineering"
    if "fpga" in value:
        return "FPGA Engineering"
    if any(term in value for term in ("solution", "sales engineer", "forward deployed")):
        return "Solutions Engineering"
    if _is_game_or_xr_text(value):
        return "Game Development"
    if any(term in value for term in ("machine learning", "ml engineer", "ai engineer", "artificial intelligence")):
        return "AI / ML Engineering"
    if re.search(
        r"\b(soc|asic|rtl|verification|serdes|fpga|silicon|uvm)\b|"
        r"mixed[- ]signal|digital (?:ic )?design|post-silicon",
        value,
    ):
        return "Chip Design & Verification"
    if any(term in value for term in ("power", "analog", "hardware", "pcb", "board", "electrical", "validation", "embedded")):
        return "Power, Board & Hardware"
    return "Software Engineering"


def _is_game_or_xr_text(value: str) -> bool:
    return bool(
        re.search(r"\b(game|games|gaming|gameplay|unity|godot|xr|vr|ar)\b|mixed reality", value)
    )


def group_queries_by_lane(queries: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {lane: [] for lane in ROLE_LANE_ORDER}
    for query in queries:
        grouped[role_lane_for_query(query)].append(query)
    return {lane: tuple(values) for lane, values in grouped.items() if values}


REGION_ORDER = (
    "India", "Canada", "United Kingdom", "Australia", "United States",
    "Europe", "Remote / Worldwide", "Other International",
)


def classify_job_region(location: str, declared_region: str = "") -> str:
    value = f"{location} {declared_region}".lower()
    if "india" in value:
        return "India"
    if "canada" in value:
        return "Canada"
    if any(term in value for term in ("united kingdom", " uk", "england", "scotland", "wales", "london")):
        return "United Kingdom"
    if "australia" in value:
        return "Australia"
    if any(term in value for term in ("united states", " usa", "u.s.", "remote us")):
        return "United States"
    if any(term in value for term in ("europe", "germany", "france", "netherlands", "ireland", "spain", "italy", "berlin", "munich")):
        return "Europe"
    if any(term in value for term in ("worldwide", "anywhere", "global", "multiple locations", "remote")):
        return "Remote / Worldwide"
    return "Other International"


def normalize_dedupe_key(title: str, company: str) -> str:
    def clean(value: str) -> str:
        value = re.sub(r"[^a-z0-9]+", " ", value.lower())
        return re.sub(r"\s+", " ", value).strip()

    return f"{clean(company)}::{clean(title)}"


def prescreen_score(listing: "JobListing", candidate_context: dict[str, Any]) -> int:
    """Cheap local keyword-overlap score against candidate skills/target lanes.

    No API call: used to order the tailoring queue so scarce OpenAI calls are spent
    on the best-looking matches first instead of just the newest listings.
    """
    text = f"{listing.title} {listing.description}".lower()
    keywords: set[str] = set()
    for group in candidate_context.get("skill_groups", {}).values():
        keywords.update(str(item).lower() for item in group)
    for lane in candidate_context.get("target_lanes", []):
        keywords.update(re.findall(r"[a-z]{3,}", str(lane).lower()))
    score = 0
    for keyword in keywords:
        if not keyword:
            continue
        if len(keyword) <= 4 and keyword.isalnum():
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                score += 1
        elif keyword in text:
            score += 1
    return score


def matches_role_query(title: str, query: str) -> bool:
    normalized_title = re.sub(r"\s+", " ", title.lower().replace("/", " ")).strip()
    keywords = ROLE_KEYWORDS.get(query.lower(), (query.lower(),))
    return any(_contains_keyword(normalized_title, keyword) for keyword in keywords)


def _contains_keyword(value: str, keyword: str) -> bool:
    if len(keyword) <= 4 and keyword.isalnum():
        return bool(re.search(rf"\b{re.escape(keyword)}\b", value))
    return keyword in value


class RemotiveSource:
    name = "remotive"
    endpoint = "https://remotive.com/api/remote-jobs"

    def search(self, query: str, limit: int = 25) -> list[JobListing]:
        url = f"{self.endpoint}?{urllib.parse.urlencode({'search': query, 'limit': limit})}"
        request = urllib.request.Request(url, headers={"User-Agent": "approval-first-job-agent/0.1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        return [
            replace(
                listing,
                role_lane=classify_role_lane(listing.title, query),
                search_region=classify_job_region(listing.location),
            )
            for listing in self.parse(payload, limit)
            if matches_role_query(listing.title, query)
        ]

    @staticmethod
    def parse(payload: dict[str, Any], limit: int = 25) -> list[JobListing]:
        listings: list[JobListing] = []
        for item in payload.get("jobs", [])[:limit]:
            listings.append(
                JobListing(
                    source="remotive",
                    source_id=str(item["id"]),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("candidate_required_location", "Remote"),
                    url=item.get("url", ""),
                    description=strip_html(item.get("description", "")),
                    salary=item.get("salary", ""),
                    published_at=item.get("publication_date", ""),
                )
            )
        return listings


class ArbeitnowSource:
    name = "arbeitnow"
    endpoint = "https://www.arbeitnow.com/api/job-board-api"

    def search(self, query: str, limit: int = 25) -> list[JobListing]:
        url = f"{self.endpoint}?{urllib.parse.urlencode({'search': query, 'remote': 'true'})}"
        request = urllib.request.Request(url, headers={"User-Agent": "approval-first-job-agent/0.2"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        return [
            replace(
                listing,
                role_lane=classify_role_lane(listing.title, query),
                search_region=classify_job_region(listing.location),
            )
            for listing in self.parse(payload, limit)
            if matches_role_query(listing.title, query)
        ]

    @staticmethod
    def parse(payload: dict[str, Any], limit: int = 25) -> list[JobListing]:
        listings: list[JobListing] = []
        for item in payload.get("data", []):
            if not item.get("remote"):
                continue
            created_at = item.get("created_at", "")
            if isinstance(created_at, (int, float)):
                created_at = datetime.fromtimestamp(created_at, timezone.utc).isoformat()
            listings.append(
                JobListing(
                    source="arbeitnow",
                    source_id=str(item.get("slug", "")),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("location", "Remote") or "Remote",
                    url=item.get("url", ""),
                    description=strip_html(item.get("description", "")),
                    published_at=str(created_at),
                )
            )
            if len(listings) >= limit:
                break
        return listings


@dataclass(frozen=True)
class ATSBoardTarget:
    ats: str
    slug: str
    company_hint: str = ""


def parse_ats_board_target(value: str) -> ATSBoardTarget | None:
    raw = value.strip()
    if not raw:
        return None
    if ":" not in raw:
        return None
    ats, slug = raw.split(":", 1)
    ats = ats.strip().lower()
    slug = slug.strip().strip("/")
    if ats not in {"greenhouse", "lever", "ashby"} or not slug:
        return None
    return ATSBoardTarget(ats=ats, slug=slug)


def extract_ats_board_targets(html_text: str, company_hint: str = "") -> list[ATSBoardTarget]:
    decoded = html.unescape(html_text)
    targets: dict[tuple[str, str], ATSBoardTarget] = {}
    patterns = (
        ("greenhouse", r"https?://boards\.greenhouse\.io/([A-Za-z0-9_-]+)"),
        ("greenhouse", r"https?://job-boards\.greenhouse\.io/([A-Za-z0-9_-]+)"),
        ("lever", r"https?://jobs\.lever\.co/([A-Za-z0-9_-]+)"),
        ("ashby", r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)"),
    )
    for ats, pattern in patterns:
        for match in re.finditer(pattern, decoded):
            slug = match.group(1).strip()
            if slug and slug.lower() not in {"embed", "jobs"}:
                targets[(ats, slug)] = ATSBoardTarget(ats, slug, company_hint)
    return list(targets.values())


class ATSJobSource:
    name = "direct_ats"

    def __init__(self, targets: tuple[ATSBoardTarget, ...]):
        deduped: dict[tuple[str, str], ATSBoardTarget] = {}
        for target in targets:
            deduped[(target.ats, target.slug)] = target
        self.targets = tuple(deduped.values())

    def search(self, queries: tuple[str, ...], limit: int = 200) -> list[JobListing]:
        listings: list[JobListing] = []
        for target in self.targets:
            try:
                if target.ats == "greenhouse":
                    found = self._greenhouse(target)
                elif target.ats == "lever":
                    found = self._lever(target)
                elif target.ats == "ashby":
                    found = self._ashby(target)
                else:
                    found = []
            except Exception:
                continue
            for listing in found:
                if self._excluded_title(listing.title):
                    continue
                matched_query = self._matched_query(listing, queries)
                if queries and not matched_query:
                    continue
                listings.append(
                    replace(
                        listing,
                        role_lane=listing.role_lane or classify_role_lane(listing.title, matched_query),
                        search_region=listing.search_region or classify_job_region(listing.location),
                    )
                )
                if len(listings) >= limit:
                    return listings
        return listings[:limit]

    @staticmethod
    def _excluded_title(title: str) -> bool:
        value = title.lower()
        if "sales engineer" in value or "solutions engineer" in value or "solution engineer" in value:
            return False
        return any(term in value for term in EXCLUDED_NON_ENGINEERING_TITLE_TERMS)

    @staticmethod
    def _matched_query(listing: JobListing, queries: tuple[str, ...]) -> str:
        normalized_title = re.sub(r"\s+", " ", listing.title.lower())
        title_is_engineering_like = bool(
            re.search(
                r"\b(engineer|developer|architect|designer|verification|validation|fpga|rtl|"
                r"firmware|hardware|electrical|electronics|unity|godot|gameplay|xr|"
                r"applications?|solutions?)\b",
                normalized_title,
            )
        )
        searchable = re.sub(r"\s+", " ", f"{listing.title} {listing.description}".lower())
        for query in queries:
            if matches_role_query(listing.title, query):
                return query
            if not title_is_engineering_like:
                continue
            if any(_contains_keyword(searchable, keyword) for keyword in ROLE_KEYWORDS.get(query.lower(), (query.lower(),))):
                return query
        return ""

    @staticmethod
    def _request_json(url: str) -> Any:
        request = urllib.request.Request(url, headers={"User-Agent": "approval-first-job-agent/0.3"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def _greenhouse(self, target: ATSBoardTarget) -> list[JobListing]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(target.slug)}/jobs?content=true"
        payload = self._request_json(url)
        return self.parse_greenhouse(payload, target.slug, target.company_hint)

    def _lever(self, target: ATSBoardTarget) -> list[JobListing]:
        url = f"https://api.lever.co/v0/postings/{urllib.parse.quote(target.slug)}?mode=json"
        payload = self._request_json(url)
        return self.parse_lever(payload, target.slug, target.company_hint)

    def _ashby(self, target: ATSBoardTarget) -> list[JobListing]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(target.slug)}?includeCompensation=true"
        payload = self._request_json(url)
        return self.parse_ashby(payload, target.slug, target.company_hint)

    @staticmethod
    def parse_greenhouse(payload: dict[str, Any], board_slug: str, company_hint: str = "") -> list[JobListing]:
        listings: list[JobListing] = []
        for item in payload.get("jobs", []):
            title = str(item.get("title", "")).strip()
            absolute_url = str(item.get("absolute_url", "")).strip()
            if not title or not absolute_url:
                continue
            offices = item.get("offices") or []
            location = ", ".join(str(office.get("name", "")).strip() for office in offices if office.get("name"))
            if not location:
                location = str((item.get("location") or {}).get("name", "")).strip()
            departments = ", ".join(str(dept.get("name", "")).strip() for dept in item.get("departments", []) if dept.get("name"))
            content = strip_html(str(item.get("content", "")))
            description = "\n".join(part for part in (departments, content) if part)
            listings.append(JobListing(
                source="greenhouse",
                source_id=f"{board_slug}:{item.get('id', absolute_url)}",
                title=title,
                company=company_hint or board_slug,
                location=location or "Not listed",
                url=absolute_url,
                description=description,
                published_at=str(item.get("updated_at", "") or item.get("created_at", "")),
            ))
        return listings

    @staticmethod
    def parse_lever(payload: list[dict[str, Any]], board_slug: str, company_hint: str = "") -> list[JobListing]:
        listings: list[JobListing] = []
        for item in payload:
            title = str(item.get("text", "")).strip()
            hosted_url = str(item.get("hostedUrl", "") or item.get("applyUrl", "")).strip()
            if not title or not hosted_url:
                continue
            categories = item.get("categories") or {}
            location = str(categories.get("location", "") or item.get("workplaceType", "")).strip()
            commitment = str(categories.get("commitment", "")).strip()
            team = str(categories.get("team", "")).strip()
            lists = []
            for block in item.get("lists", []) or []:
                heading = str(block.get("text", "")).strip()
                body = "\n".join(str(content.get("text", "")).strip() for content in block.get("content", []) if content.get("text"))
                if heading or body:
                    lists.append("\n".join(part for part in (heading, body) if part))
            description = "\n".join(part for part in (team, commitment, strip_html(str(item.get("descriptionPlain", ""))), "\n\n".join(lists)) if part)
            listings.append(JobListing(
                source="lever",
                source_id=f"{board_slug}:{item.get('id', hosted_url)}",
                title=title,
                company=company_hint or board_slug,
                location=location or "Not listed",
                url=hosted_url,
                description=description,
                published_at=str(item.get("createdAt", "")),
            ))
        return listings

    @staticmethod
    def parse_ashby(payload: dict[str, Any], board_slug: str, company_hint: str = "") -> list[JobListing]:
        jobs = payload.get("jobs") or payload.get("jobPostings") or []
        listings: list[JobListing] = []
        for item in jobs:
            title = str(item.get("title", "")).strip()
            job_id = str(item.get("id", "") or item.get("jobPostingId", "")).strip()
            hosted_url = str(item.get("jobUrl", "") or item.get("hostedUrl", "") or item.get("externalLink", "")).strip()
            if not hosted_url and job_id:
                hosted_url = f"https://jobs.ashbyhq.com/{urllib.parse.quote(board_slug)}/{urllib.parse.quote(job_id)}"
            if not title or not hosted_url:
                continue
            location = ATSJobSource._ashby_location(item)
            compensation = ATSJobSource._ashby_compensation(item)
            description = strip_html(str(item.get("descriptionHtml", "") or item.get("descriptionPlain", "") or item.get("description", "")))
            listings.append(JobListing(
                source="ashby",
                source_id=f"{board_slug}:{job_id or hosted_url}",
                title=title,
                company=company_hint or board_slug,
                location=location or "Not listed",
                url=hosted_url,
                description=description,
                salary=compensation,
                published_at=str(item.get("publishedAt", "") or item.get("createdAt", "")),
            ))
        return listings

    @staticmethod
    def _ashby_location(item: dict[str, Any]) -> str:
        location = item.get("location")
        if isinstance(location, dict):
            return str(location.get("name", "") or location.get("displayName", "")).strip()
        if isinstance(location, str):
            return location.strip()
        locations = item.get("locations")
        if isinstance(locations, list):
            values = [
                str(location.get("name", "") if isinstance(location, dict) else location).strip()
                for location in locations
            ]
            return ", ".join(value for value in values if value)
        return ""

    @staticmethod
    def _ashby_compensation(item: dict[str, Any]) -> str:
        compensation = item.get("compensation")
        if isinstance(compensation, dict):
            parts = [
                str(compensation.get("compensationTierSummary", "")).strip(),
                str(compensation.get("summary", "")).strip(),
            ]
            return " ".join(part for part in parts if part)
        return str(compensation or "").strip()


YC_JOB_ROUTES = (
    "/jobs",
    "/jobs/role/engineering",
    "/jobs/role/software-engineer",
    "/jobs/location/india",
    "/jobs/location/canada",
    "/jobs/location/united-kingdom",
    "/jobs/location/australia",
    "/jobs/location/san-francisco",
    "/jobs/location/new-york",
    "/jobs/location/los-angeles",
    "/jobs/location/austin",
)


class YCJobSource:
    name = "ycombinator"
    endpoint = "https://www.ycombinator.com"

    def __init__(self, min_company_batch_year: int = 2024, company_scan_limit: int = 120):
        self.min_company_batch_year = min_company_batch_year
        self.company_scan_limit = company_scan_limit

    def search(
        self, queries: tuple[str, ...], limit: int = 50,
        regions: tuple[str, ...] = (),
    ) -> list[JobListing]:
        del regions  # YC routes encode the useful location slices; keep raw locations in listings.
        merged: dict[str, JobListing] = {}
        for listing in self._search_relevant_company_pages(queries, limit=limit):
            merged[listing.source_id] = listing
        for route in YC_JOB_ROUTES:
            url = f"{self.endpoint}{route}"
            request = urllib.request.Request(url, headers={"User-Agent": "approval-first-job-agent/0.2"})
            with urllib.request.urlopen(request, timeout=30) as response:
                html_text = response.read().decode("utf-8", errors="replace")
            for listing in self.parse(self._extract_job_postings(html_text), queries, limit=limit):
                merged[listing.source_id] = listing
        return list(merged.values())[:limit]

    def _search_relevant_company_pages(
        self, queries: tuple[str, ...], limit: int = 50,
    ) -> list[JobListing]:
        """Crawl recent relevant YC company pages because some strong jobs only appear there."""
        listings: list[JobListing] = []
        try:
            companies = YCCompanySource().fetch(self.min_company_batch_year)
        except RuntimeError:
            return []
        companies = sorted(
            companies,
            key=lambda company: self._company_relevance_score(
                company.name, company.one_liner, company.tags, queries
            ),
            reverse=True,
        )
        scanned = 0
        for company in companies:
            if not self._company_matches_queries(company.name, company.one_liner, company.tags, queries):
                continue
            scanned += 1
            if scanned > self.company_scan_limit:
                break
            url = f"{self.endpoint}/companies/{urllib.parse.quote(company.slug)}"
            request = urllib.request.Request(url, headers={"User-Agent": "approval-first-job-agent/0.2"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    html_text = response.read().decode("utf-8", errors="replace")
            except urllib.error.URLError:
                continue
            listings.extend(self.parse(self._extract_job_postings(html_text), queries, limit=limit))
            if len(listings) >= limit:
                break
        return listings[:limit]

    @staticmethod
    def _company_matches_queries(
        name: str, one_liner: str, tags: str, queries: tuple[str, ...],
    ) -> bool:
        if not queries:
            return True
        value = re.sub(r"\s+", " ", f"{name} {one_liner} {tags}".lower())
        for query in queries:
            keywords = ROLE_KEYWORDS.get(query.lower(), (query.lower(),))
            if any(_contains_keyword(value, keyword) for keyword in keywords):
                return True
        return False

    @staticmethod
    def _company_relevance_score(
        name: str, one_liner: str, tags: str, queries: tuple[str, ...],
    ) -> int:
        value = re.sub(r"\s+", " ", f"{name} {one_liner} {tags}".lower())
        score = 0
        priority_terms = (
            "power electronics", "circuit board", "pcb", "electronics", "electrical",
            "hardware", "firmware", "embedded", "semiconductor", "semiconductors",
            "chip", "silicon", "fpga", "asic", "rtl", "verification", "robotics",
            "manufacturing", "industrial", "electric vehicles",
        )
        for term in priority_terms:
            if term in value:
                score += 8
        for query in queries:
            for keyword in ROLE_KEYWORDS.get(query.lower(), (query.lower(),)):
                if _contains_keyword(value, keyword):
                    score += 4
        if "ai" in value or "artificial intelligence" in value:
            score += 1
        return score

    @staticmethod
    def _extract_job_postings(html_text: str) -> list[dict[str, Any]]:
        """Extract the embedded Rails/React jobPostings payload from public YC pages."""
        decoded = html.unescape(html_text)
        key = '"jobPostings":'
        key_index = decoded.find(key)
        if key_index < 0:
            return []
        array_start = decoded.find("[", key_index)
        if array_start < 0:
            return []
        depth = 0
        in_string = False
        escaped = False
        for index in range(array_start, len(decoded)):
            char = decoded[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    try:
                        payload = json.loads(decoded[array_start:index + 1])
                    except json.JSONDecodeError:
                        return []
                    return payload if isinstance(payload, list) else []
        return []

    @staticmethod
    def parse(
        payload: list[dict[str, Any]], queries: tuple[str, ...] = (), limit: int = 50,
    ) -> list[JobListing]:
        listings: list[JobListing] = []
        for item in payload:
            title = str(item.get("title", "")).strip()
            company = str(item.get("companyName", "")).strip()
            relative_url = str(item.get("url", "")).strip()
            if not title or not company or not relative_url:
                continue
            matched_query = YCJobSource._matched_query(item, queries)
            if queries and not matched_query:
                continue
            role_lane = classify_role_lane(title, matched_query or "")
            location = str(item.get("location", "")).strip() or "Not listed"
            salary = " / ".join(
                value for value in (
                    str(item.get("salaryRange", "")).strip(),
                    str(item.get("equityRange", "")).strip(),
                )
                if value
            )
            description_parts = [
                str(item.get("companyOneLiner", "")).strip(),
                f"YC batch: {item.get('companyBatchName', '')}".strip(),
                f"Role type: {item.get('roleSpecificType', '')}".strip(),
                f"Experience: {item.get('minExperience', '')}".strip(),
                f"Visa/location note from YC: {item.get('visa', '')}".strip(),
                f"Skills: {', '.join(str(skill) for skill in item.get('skills', []) if skill)}",
                f"Last active: {item.get('lastActive', '')}".strip(),
            ]
            listings.append(
                JobListing(
                    source=YCJobSource.name,
                    source_id=str(item.get("id", "")).strip(),
                    title=title,
                    company=company,
                    location=location,
                    url=urllib.parse.urljoin(YCJobSource.endpoint, relative_url),
                    description="\n".join(part for part in description_parts if part and not part.endswith(":")),
                    salary=salary,
                    published_at=str(item.get("createdAt", "")).strip(),
                    role_lane=role_lane,
                    search_region=classify_job_region(location),
                )
            )
            if len(listings) >= limit:
                break
        return listings

    @staticmethod
    def _matched_query(item: dict[str, Any], queries: tuple[str, ...]) -> str:
        if not queries:
            return ""
        title = str(item.get("title", ""))
        searchable = " ".join(
            [
                title,
                str(item.get("roleSpecificType", "")),
                str(item.get("companyOneLiner", "")),
                " ".join(str(skill) for skill in item.get("skills", [])),
            ]
        )
        normalized = re.sub(r"\s+", " ", searchable.lower().replace("/", " ")).strip()
        for query in queries:
            if matches_role_query(title, query):
                return query
            keywords = ROLE_KEYWORDS.get(query.lower(), (query.lower(),))
            if any(_contains_keyword(normalized, keyword) for keyword in keywords):
                return query
        return ""


WEB_JOB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "url": {"type": "string"},
                    "description": {"type": "string"},
                    "salary": {"type": "string"},
                    "published_at": {"type": "string"},
                    "search_region": {"type": "string"},
                },
                "required": ["title", "company", "location", "url", "description", "salary", "published_at", "search_region"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["jobs"],
    "additionalProperties": False,
}


class OpenAIWebJobSource:
    name = "openai_web_search"
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, model: str = "gpt-5.5"):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for web job search")
        self.api_key = api_key
        self.model = model

    def search(
        self, queries: tuple[str, ...], limit: int = 10, role_lane: str = "",
        regions: tuple[str, ...] = (),
    ) -> list[JobListing]:
        geographic_scope = ", ".join(regions) if regions else "worldwide"
        prompt = (
            "Search the live web for currently open jobs matching these role families: "
            + ", ".join(queries)
            + f". These are all within the {role_lane or 'requested'} lane; distribute results across the "
            "listed role families instead of concentrating on the first one. Search worldwide, with these "
            f"priority geographies: {geographic_scope}. Include India-based roles whenever relevant and "
            "distribute results across countries instead of defaulting mainly to the United States. Prioritize "
            "official employer career pages and direct ATS pages. Prefer employers that explicitly support "
            "international applicants, visa sponsorship, relocation, or cross-border remote work, but never "
            "claim sponsorship unless the source page supports it. Prefer new-grad, junior, associate, or roles that "
            "do not explicitly require more than three years of experience. Exclude senior, staff, principal, "
            "lead, manager, director, contract-sales, and clearly closed roles. Return no more than "
            f"{limit} distinct jobs. Each URL must be the direct job or application page, and description must "
            "summarize concrete responsibilities and qualifications from that page. If a date or salary is not "
            "shown, return an empty string."
        )
        payload = {
            "model": self.model,
            "reasoning": {"effort": "low"},
            "tools": [{
                "type": "web_search",
                "search_context_size": "medium",
                "user_location": {
                    "type": "approximate",
                    "country": "US",
                    "timezone": "America/Chicago",
                },
            }],
            "tool_choice": "required",
            "input": prompt,
            "max_output_tokens": 8000,
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "current_job_listings",
                    "strict": True,
                    "schema": WEB_JOB_SCHEMA,
                },
            },
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "approval-first-job-agent/0.2",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI web-search error ({error.code}): {detail[:800]}") from error
        output_text = body.get("output_text") or self._extract_output_text(body)
        if not output_text:
            raise RuntimeError("OpenAI web search returned no structured job list")
        data = json.loads(output_text)
        listings: list[JobListing] = []
        for item in data.get("jobs", [])[:limit]:
            url = str(item.get("url", "")).strip()
            if not url.startswith(("https://", "http://")):
                continue
            source_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
            listings.append(
                JobListing(
                    source=self.name,
                    source_id=source_id,
                    title=str(item.get("title", "")).strip(),
                    company=str(item.get("company", "")).strip(),
                    location=str(item.get("location", "")).strip(),
                    url=url,
                    description=str(item.get("description", "")).strip(),
                    salary=str(item.get("salary", "")).strip(),
                    published_at=str(item.get("published_at", "")).strip(),
                    role_lane=role_lane or classify_role_lane(str(item.get("title", ""))),
                    search_region=classify_job_region(
                        str(item.get("location", "")), str(item.get("search_region", ""))
                    ),
                )
            )
        return listings

    @staticmethod
    def _extract_output_text(body: dict[str, Any]) -> str:
        for item in body.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return content.get("text", "")
        return ""
