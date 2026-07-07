from __future__ import annotations

import calendar
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .yc_source import YCCompanySource, extract_public_emails, guess_contact_emails, score_yc_company


USER_AGENT = "approval-first-job-agent/0.2 tushar-pathak personal job search contact: no-live-email-by-default"


@dataclass(frozen=True)
class StartupCompany:
    source: str
    source_id: str
    name: str
    domain: str = ""
    country: str = ""
    region: str = ""
    stage: str = ""
    funding_signal: str = ""
    funding_date: str = ""
    funding_amount: str = ""
    evidence_url: str = ""
    description: str = ""
    tags: str = ""
    fit_score: int = 0
    fit_reasons: tuple[str, ...] = ()


def score_startup_company(company: StartupCompany, candidate_context: dict[str, Any] | None = None) -> tuple[int, list[str]]:
    yc_like = type(
        "ScoredCompany",
        (),
        {
            "name": company.name,
            "one_liner": f"{company.description} {company.funding_signal}",
            "tags": company.tags,
        },
    )()
    return score_yc_company(yc_like, candidate_context)


class YCStartupSource:
    name = "yc_directory"

    def fetch(self, min_batch_year: int, candidate_context: dict[str, Any]) -> list[StartupCompany]:
        companies: list[StartupCompany] = []
        for company in YCCompanySource().fetch(min_batch_year):
            startup = StartupCompany(
                source=self.name,
                source_id=company.slug,
                name=company.name,
                domain=company.domain,
                country="",
                region="",
                stage=company.batch,
                funding_signal=f"Y Combinator {company.batch}",
                evidence_url=f"https://www.ycombinator.com/companies/{company.slug}",
                description=company.one_liner,
                tags=company.tags,
            )
            score, reasons = score_startup_company(startup, candidate_context)
            companies.append(_with_score(startup, score, reasons))
        return companies


class SECFormDSource:
    """Best-effort free source for U.S. private fundraising signals.

    Form D is noisy: many issuers are funds/LLCs and not startups. The local fit scorer
    filters aggressively before any outreach draft spends OpenAI tokens.
    """

    name = "sec_form_d"
    index_url = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/master.{yyyymmdd}.idx"
    archive_base = "https://www.sec.gov/Archives/"

    def fetch(self, days: int, limit: int, candidate_context: dict[str, Any]) -> list[StartupCompany]:
        companies: dict[str, StartupCompany] = {}
        for day in _recent_weekdays(days):
            for row in self._fetch_index_day(day):
                if row["form"] not in {"D", "D/A"}:
                    continue
                details = self._fetch_form_d_details(row["path"])
                startup = StartupCompany(
                    source=self.name,
                    source_id=f"{row['cik']}:{row['path']}",
                    name=details.get("name") or row["company"],
                    country=details.get("country", ""),
                    region=details.get("region", ""),
                    stage="private placement",
                    funding_signal="SEC Form D private securities offering",
                    funding_date=day.isoformat(),
                    funding_amount=details.get("amount", ""),
                    evidence_url=urllib.parse.urljoin(self.archive_base, row["path"]),
                    description=" ".join(
                        part for part in (
                            details.get("industry", ""),
                            details.get("offering_type", ""),
                            details.get("description", ""),
                        )
                        if part
                    ),
                    tags=", ".join(part for part in (details.get("industry", ""), details.get("offering_type", "")) if part),
                )
                score, reasons = score_startup_company(startup, candidate_context)
                companies[startup.source_id] = _with_score(startup, score, reasons)
                if len(companies) >= limit:
                    return list(companies.values())
        return list(companies.values())

    def _fetch_index_day(self, day: date) -> list[dict[str, str]]:
        yyyymmdd = day.strftime("%Y%m%d")
        quarter = (day.month - 1) // 3 + 1
        url = self.index_url.format(year=day.year, quarter=quarter, yyyymmdd=yyyymmdd)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                text = response.read().decode("latin-1", errors="replace")
        except urllib.error.URLError:
            return []
        return self.parse_master_index(text)

    @staticmethod
    def parse_master_index(text: str) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for line in text.splitlines():
            if "|" not in line or line.startswith("CIK|"):
                continue
            parts = line.split("|")
            if len(parts) != 5:
                continue
            cik, company, form, filed, path = [part.strip() for part in parts]
            rows.append({"cik": cik, "company": company, "form": form, "filed": filed, "path": path})
        return rows

    def _fetch_form_d_details(self, path: str) -> dict[str, str]:
        request = urllib.request.Request(urllib.parse.urljoin(self.archive_base, path), headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.URLError:
            return {}
        return self.parse_form_d_text(text)

    @staticmethod
    def parse_form_d_text(text: str) -> dict[str, str]:
        def first(pattern: str) -> str:
            match = re.search(pattern, text, flags=re.I | re.S)
            return html.unescape(re.sub(r"\s+", " ", match.group(1)).strip()) if match else ""

        amount = first(r"<totalOfferingAmount>(.*?)</totalOfferingAmount>")
        if amount and amount.isdigit():
            amount = f"${int(amount):,}"
        return {
            "name": first(r"<entityName>(.*?)</entityName>"),
            "country": first(r"<country>(.*?)</country>"),
            "region": first(r"<stateOrCountryDescription>(.*?)</stateOrCountryDescription>"),
            "industry": first(r"<industryGroupType>(.*?)</industryGroupType>"),
            "offering_type": first(r"<typeOfSecurityOffered>(.*?)</typeOfSecurityOffered>"),
            "amount": amount,
            "description": first(r"<descriptionOfOtherTypeOfSecurityOffered>(.*?)</descriptionOfOtherTypeOfSecurityOffered>"),
        }


class PortfolioPageSource:
    """Lightweight configurable public portfolio crawler.

    This is intentionally conservative: it records candidate company names/domains from
    provided URLs instead of scraping private databases or bypassing access controls.
    """

    name = "portfolio_page"

    def fetch(self, urls: tuple[str, ...], candidate_context: dict[str, Any]) -> list[StartupCompany]:
        companies: dict[str, StartupCompany] = {}
        for url in urls:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    text = response.read().decode("utf-8", errors="replace")
            except urllib.error.URLError:
                continue
            for domain in sorted(set(_extract_domains(text))):
                if _is_ignored_domain(domain):
                    continue
                name = domain.split(".")[0].replace("-", " ").title()
                startup = StartupCompany(
                    source=self.name,
                    source_id=f"{url}::{domain}",
                    name=name,
                    domain=domain,
                    funding_signal=f"Listed on public portfolio page: {urllib.parse.urlparse(url).netloc}",
                    evidence_url=url,
                    description=f"Company domain discovered on public portfolio page {url}",
                    tags="portfolio",
                )
                score, reasons = score_startup_company(startup, candidate_context)
                companies[startup.source_id] = _with_score(startup, score, reasons)
        return list(companies.values())


def discover_contacts(company: StartupCompany, enrich: bool = False) -> list[tuple[str, str]]:
    contacts: list[tuple[str, str]] = []
    if enrich and company.evidence_url.startswith("http"):
        request = urllib.request.Request(company.evidence_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                text = response.read().decode("utf-8", errors="replace")
            contacts.extend((email, "public_page") for email in extract_public_emails(text, company.domain))
        except urllib.error.URLError:
            pass
    contacts.extend((email, email.split("@")[0]) for email in guess_contact_emails(company.domain))
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for email, alias in contacts:
        email = email.lower()
        if email and email not in seen:
            seen.add(email)
            unique.append((email, alias))
    return unique


def _with_score(company: StartupCompany, score: int, reasons: list[str]) -> StartupCompany:
    return StartupCompany(**{**company.__dict__, "fit_score": score, "fit_reasons": tuple(reasons)})


def _recent_weekdays(days: int) -> list[date]:
    today = date.today()
    result: list[date] = []
    for offset in range(max(days, 1)):
        day = today - timedelta(days=offset)
        if calendar.weekday(day.year, day.month, day.day) < 5:
            result.append(day)
    return result


def _extract_domains(text: str) -> list[str]:
    domains: list[str] = []
    for match in re.finditer(r"https?://([^/\"'<> ]+)", html.unescape(text), flags=re.I):
        domain = match.group(1).lower().removeprefix("www.")
        if "." in domain:
            domains.append(domain)
    return domains


def _is_ignored_domain(domain: str) -> bool:
    ignored = (
        "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
        "youtube.com", "google.com", "schema.org", "w3.org", "github.com",
        "crunchbase.com", "pitchbook.com",
    )
    return any(domain == value or domain.endswith("." + value) for value in ignored)
