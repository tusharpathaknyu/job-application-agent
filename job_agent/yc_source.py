from __future__ import annotations

import json
import random
import re
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


CONTACT_ALIASES = ("founders", "careers", "jobs", "talent", "hi", "hello", "contact", "team")
PUBLIC_DNS_RESOLVERS = ("1.1.1.1", "8.8.8.8")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


@dataclass(frozen=True)
class YCCompany:
    slug: str
    name: str
    domain: str
    batch: str
    one_liner: str
    tags: str
    status: str


def _extract_domain(website: str) -> str:
    if not website:
        return ""
    parsed = urllib.parse.urlparse(website if "://" in website else f"https://{website}")
    return parsed.netloc.removeprefix("www.").lower()


def _batch_year(batch: str) -> int | None:
    match = re.search(r"(\d{4})", batch)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d{2})$", batch.strip())
    if match:
        return 2000 + int(match.group(1))
    return None


def guess_contact_emails(domain: str) -> list[str]:
    if not domain:
        return []
    return [f"{alias}@{domain}" for alias in CONTACT_ALIASES]


def extract_public_emails(text: str, preferred_domain: str = "") -> list[str]:
    """Extract plain emails from a public company page, preferring same-domain contacts."""
    emails: list[str] = []
    seen: set[str] = set()
    preferred_domain = preferred_domain.lower().removeprefix("www.")
    for match in EMAIL_PATTERN.finditer(text):
        email = match.group(0).strip(".,;:()[]{}<>").lower()
        if email in seen:
            continue
        if any(email.endswith(suffix) for suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
            continue
        if preferred_domain and not email.endswith("@" + preferred_domain):
            continue
        seen.add(email)
        emails.append(email)
    return emails


FIT_KEYWORDS: tuple[tuple[str, int, str], ...] = (
    ("power electronics", 18, "power electronics"),
    ("electric vehicles", 12, "EV / electric vehicles"),
    ("energy", 7, "energy"),
    ("circuit board", 18, "circuit boards / PCB"),
    ("pcb", 18, "circuit boards / PCB"),
    ("electronics", 12, "electronics"),
    ("electrical", 10, "electrical engineering"),
    ("hardware", 10, "hardware"),
    ("firmware", 12, "firmware"),
    ("embedded", 10, "embedded systems"),
    ("microcontroller", 10, "microcontrollers"),
    ("microcontrollers", 10, "microcontrollers"),
    ("semiconductor", 14, "semiconductors"),
    ("semiconductors", 14, "semiconductors"),
    ("chip", 12, "chip design"),
    ("silicon", 12, "silicon"),
    ("fpga", 14, "FPGA"),
    ("asic", 14, "ASIC"),
    ("rtl", 14, "RTL"),
    ("verification", 8, "verification"),
    ("simulation", 9, "simulation"),
    ("physics", 8, "physics simulation"),
    ("robotics", 9, "robotics"),
    ("manufacturing", 8, "manufacturing"),
    ("industrial", 7, "industrial systems"),
    ("developer tools", 5, "developer tools"),
    ("gaming", 12, "gaming"),
    ("game", 8, "game development"),
    ("games", 8, "game development"),
    ("gameplay", 12, "gameplay systems"),
    ("unity", 12, "Unity"),
    ("godot", 12, "Godot"),
    ("virtual reality", 9, "XR / VR"),
    ("augmented reality", 9, "XR / AR"),
    ("xr", 7, "XR"),
    ("vr", 7, "VR"),
    ("ar", 5, "AR"),
    ("sports tech", 6, "sports tech"),
    ("fitness", 8, "fitness technology"),
    ("ai", 4, "AI"),
    ("machine learning", 5, "machine learning"),
)


def score_yc_company(company: YCCompany, candidate_context: dict | None = None) -> tuple[int, list[str]]:
    """Local, deterministic company-fit score used before spending an OpenAI call."""
    text = f"{company.name} {company.one_liner} {company.tags}".lower()
    score = 0
    reasons: list[str] = []
    for keyword, weight, reason in FIT_KEYWORDS:
        if _keyword_in_text(keyword, text):
            score += weight
            if reason not in reasons:
                reasons.append(reason)
    for lane in (candidate_context or {}).get("target_lanes", []):
        words = re.findall(r"[a-z]{4,}", str(lane).lower())
        if any(word in text for word in words):
            score += 3
    for group in (candidate_context or {}).get("skill_groups", {}).values():
        for skill in group:
            skill_text = str(skill).lower()
            if len(skill_text) >= 4 and skill_text in text:
                score += 1
    return min(score, 100), reasons[:8]


def _keyword_in_text(keyword: str, text: str) -> bool:
    if len(keyword) <= 3 and keyword.isalnum():
        return bool(re.search(rf"\b{re.escape(keyword)}\b", text))
    return keyword in text


def build_mx_query(domain: str, transaction_id: int | None = None) -> bytes:
    """Build a raw DNS query packet asking for the domain's MX records.

    Pure/testable: no network. Uses the stdlib only (no dnspython dependency) since the
    rest of this project deliberately avoids third-party HTTP/network libraries.
    """
    transaction_id = transaction_id if transaction_id is not None else random.randint(0, 65535)
    header = struct.pack(">HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    question = b"".join(
        bytes([len(label)]) + label.encode("ascii") for label in domain.strip(".").split(".")
    ) + b"\x00" + struct.pack(">HH", 15, 1)  # QTYPE=MX(15), QCLASS=IN(1)
    return header + question


def parse_answer_count(response: bytes) -> int:
    """Read ANCOUNT (answer count) from a DNS response header. Pure/testable."""
    if len(response) < 8:
        return 0
    return struct.unpack(">H", response[6:8])[0]


def has_mx_record(domain: str, timeout: float = 3.0, resolvers: tuple[str, ...] = PUBLIC_DNS_RESOLVERS) -> bool:
    """Best-effort check that a domain has at least one mail exchanger.

    Used to skip cold outreach entirely for domains that can't receive mail at all
    (dead sites, parked domains) before spending an OpenAI call or a send attempt. This
    does not verify that any specific guessed alias exists — only that the domain accepts
    mail for *some* address.
    """
    if not domain:
        return False
    query = build_mx_query(domain)
    for resolver in resolvers:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(query, (resolver, 53))
                response, _ = sock.recvfrom(1024)
            return parse_answer_count(response) > 0
        except OSError:
            continue
    return False


class YCCompanySource:
    """Reads the public yc-oss mirror of YC's company directory (no auth required)."""

    name = "yc_companies"
    endpoint = "https://yc-oss.github.io/api/companies/all.json"

    def fetch(self, min_batch_year: int = 2020) -> list[YCCompany]:
        request = urllib.request.Request(
            self.endpoint, headers={"User-Agent": "approval-first-job-agent/0.1"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.URLError as error:
            raise RuntimeError(f"YC company directory fetch failed: {error}") from error
        return self.parse(payload, min_batch_year)

    @staticmethod
    def parse(payload: list[dict], min_batch_year: int = 2020) -> list[YCCompany]:
        companies: list[YCCompany] = []
        for item in payload:
            batch = str(item.get("batch", "")).strip()
            year = _batch_year(batch)
            if year is None or year < min_batch_year:
                continue
            if str(item.get("status", "")).strip().lower() not in ("active", ""):
                continue
            slug = str(item.get("slug", "")).strip()
            name = str(item.get("name", "")).strip()
            if not slug or not name:
                continue
            tags = item.get("tags") or item.get("industries") or []
            companies.append(
                YCCompany(
                    slug=slug,
                    name=name,
                    domain=_extract_domain(str(item.get("website", ""))),
                    batch=batch,
                    one_liner=str(item.get("one_liner", "")).strip(),
                    tags=", ".join(str(tag) for tag in tags) if isinstance(tags, list) else str(tags),
                    status=str(item.get("status", "")).strip(),
                )
            )
        return companies
