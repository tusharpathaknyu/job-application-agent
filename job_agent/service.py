from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .adapters import UnmappedQuestionError as AdapterUnmappedQuestionError, find_adapter
from .artifacts import write_outreach_artifacts, write_package_artifacts
from .config import Settings
from .context import context_summary, load_candidate_context, load_resume_template
from .db import Database
from .outreach import OutreachBounceError, OutreachComposer
from .outreach import send_outreach as send_outreach_email
from .sources import (
    ArbeitnowSource, JobListing, OpenAIWebJobSource, RemotiveSource,
    YCJobSource, classify_job_region, classify_role_lane, group_queries_by_lane,
    normalize_dedupe_key, prescreen_score,
)
from .startup_sources import (
    PortfolioPageSource, SECFormDSource, StartupCompany, YCStartupSource,
    discover_contacts,
)
from .tailor import OpenAITailor, TailoredPackage
from .yc_source import (
    YCCompanySource, extract_public_emails, guess_contact_emails, has_mx_record,
    score_yc_company,
)


class ApprovalError(RuntimeError):
    pass


APPLICATION_ANSWER_FIELDS = (
    "legal_name", "preferred_name", "street_address", "city", "state_province",
    "postal_code", "country_of_residence", "github_url", "earliest_start_date",
    "employment_types", "willing_to_relocate", "willing_to_travel",
    "authorized_us", "sponsorship_us", "authorized_canada", "sponsorship_canada",
    "authorized_uk", "sponsorship_uk", "authorized_australia",
    "sponsorship_australia", "authorized_india", "demographic_response_policy",
    "additional_form_answers",
)


class JobAgentService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._bootstrap_profile_from_context()
        self._backfill_job_metadata()

    def _backfill_job_metadata(self) -> None:
        for job in self.db.all("SELECT id, title, location, role_lane, search_region FROM jobs"):
            role_lane = classify_role_lane(job["title"])
            search_region = classify_job_region(job["location"])
            if job.get("role_lane") != role_lane or job.get("search_region") != search_region:
                self.db.execute(
                    "UPDATE jobs SET role_lane=?, search_region=? WHERE id=?",
                    (role_lane, search_region, job["id"]),
                )

    def _bootstrap_profile_from_context(self) -> None:
        context = load_candidate_context(self.settings.candidate_context_path)
        identity = context.get("identity", {})
        if not identity:
            return
        current = self.db.one("SELECT * FROM profile WHERE id=1") or {}
        defaults = {
            "full_name": identity.get("resume_name", ""),
            "email": identity.get("email", ""),
            "phone": identity.get("phone", ""),
            "linkedin_url": identity.get("linkedin", ""),
            "portfolio_url": identity.get("portfolio", ""),
            "target_roles": ", ".join(context.get("target_lanes", [])),
        }
        updates = {key: value for key, value in defaults.items() if value and not current.get(key)}
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        self.db.execute(
            f"UPDATE profile SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=1",
            tuple(updates.values()),
        )
        self.db.audit("profile_bootstrapped", "profile", 1, {"fields": sorted(updates)})

    def get_profile(self) -> dict[str, Any]:
        profile = self.db.one("SELECT * FROM profile WHERE id=1") or {}
        try:
            profile["application_answers"] = json.loads(profile.get("application_answers", "{}"))
        except (TypeError, json.JSONDecodeError):
            profile["application_answers"] = {}
        return profile

    def get_context_summary(self) -> dict[str, Any]:
        context = load_candidate_context(self.settings.candidate_context_path)
        template = load_resume_template(self.settings.resume_template_path)
        summary = context_summary(context, template)
        summary["search_regions"] = list(self.settings.job_search_regions)
        return summary

    def _has_tailoring_source(self, profile: dict[str, Any]) -> bool:
        return bool(profile.get("base_resume") or load_candidate_context(self.settings.candidate_context_path))

    def save_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "full_name", "email", "phone", "location", "linkedin_url",
            "portfolio_url", "target_roles", "preferences", "work_authorization",
            "sponsorship_required", "location_preferences", "salary_preferences",
            "application_notes", "base_resume",
        )
        values = tuple(str(data.get(field, "")).strip() for field in fields)
        application_answers = {
            field: str(data.get(field, "")).strip()
            for field in APPLICATION_ANSWER_FIELDS
            if str(data.get(field, "")).strip()
        }
        self.db.execute(
            f"UPDATE profile SET {', '.join(f'{field}=?' for field in fields)}, application_answers=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
            values + (json.dumps(application_answers),),
        )
        self.db.audit("profile_updated", "profile", 1)
        return self.get_profile()

    def sync_jobs(self) -> dict[str, Any]:
        queries = self.settings.job_search_queries or (self.settings.job_search_query,)
        unique: dict[tuple[str, str], JobListing] = {}
        errors: list[dict[str, str]] = []
        sources = (RemotiveSource(), ArbeitnowSource())
        source_count = len(sources)
        tasks: list[tuple[str, str, Any, tuple[Any, ...]]] = []
        for source in sources:
            source_queries = queries
            if isinstance(source, ArbeitnowSource):
                source_queries = tuple(
                    lane_queries[0]
                    for lane_queries in group_queries_by_lane(queries).values()
                )
            for query in source_queries:
                tasks.append((source.name, query, source.search, (query, self.settings.job_search_limit)))
        if self.settings.enable_openai_job_search and self.settings.openai_api_key:
            web_source = OpenAIWebJobSource(self.settings.openai_api_key, self.settings.openai_model)
            source_count += 1
            for lane, lane_queries in group_queries_by_lane(queries).items():
                tasks.append((
                    web_source.name, lane, web_source.search,
                    (
                        lane_queries, self.settings.web_search_per_lane_limit,
                    lane, self.settings.job_search_regions,
                ),
            ))
        if self.settings.enable_yc_job_search:
            yc_source = YCJobSource(min_company_batch_year=max(self.settings.yc_min_batch_year, 2024))
            source_count += 1
            tasks.append((
                yc_source.name, "YC curated jobs", yc_source.search,
                (
                    queries,
                    max(self.settings.job_search_limit, self.settings.web_search_job_limit, 120),
                    self.settings.job_search_regions,
                ),
            ))
        with ThreadPoolExecutor(max_workers=min(10, len(tasks) or 1)) as executor:
            futures = {
                executor.submit(function, *arguments): (source_name, query_label)
                for source_name, query_label, function, arguments in tasks
            }
            for future in as_completed(futures):
                source_name, query_label = futures[future]
                try:
                    for listing in future.result():
                        if not listing.role_lane or not listing.search_region:
                            listing = JobListing(**{
                                **listing.__dict__,
                                "role_lane": listing.role_lane or classify_role_lane(listing.title, query_label),
                                "search_region": listing.search_region or classify_job_region(listing.location),
                            })
                        unique[(listing.source, listing.source_id)] = listing
                except Exception as error:
                    errors.append({"source": source_name, "query": query_label, "error": str(error)})
        candidate_context = load_candidate_context(self.settings.candidate_context_path)
        deduped: dict[str, JobListing] = {}
        for listing in unique.values():
            key = normalize_dedupe_key(listing.title, listing.company)
            if key not in deduped:
                deduped[key] = listing
        inserted = 0
        for listing in deduped.values():
            inserted += self.add_job(listing, prescreen_score(listing, candidate_context))
        lane_counts: dict[str, int] = {}
        region_counts: dict[str, int] = {}
        for listing in deduped.values():
            lane_counts[listing.role_lane] = lane_counts.get(listing.role_lane, 0) + 1
            region_counts[listing.search_region] = region_counts.get(listing.search_region, 0) + 1
        return {
            "sources": source_count, "queries": len(queries), "fetched": len(unique),
            "duplicates_collapsed": len(unique) - len(deduped),
            "inserted": inserted, "lanes": lane_counts, "regions": region_counts,
            "errors": errors,
        }

    def run_automatic_cycle(self) -> dict[str, Any]:
        result: dict[str, Any] = {"discovery": self.sync_jobs(), "tailored": [], "errors": []}
        if not self.settings.auto_tailor:
            result["auto_tailor"] = "disabled"
        elif not self.settings.openai_api_key:
            result["auto_tailor"] = "skipped: OPENAI_API_KEY is missing"
        elif not self._has_tailoring_source(self.get_profile()):
            result["auto_tailor"] = "skipped: candidate context and base resume are missing"
        else:
            jobs = self.db.all(
                """SELECT jobs.id FROM jobs
                   LEFT JOIN packages ON packages.job_id=jobs.id
                   WHERE packages.id IS NULL AND jobs.status='discovered'
                   ORDER BY jobs.prescreen_score DESC, jobs.discovered_at DESC, jobs.id DESC LIMIT ?""",
                (self.settings.max_tailors_per_cycle,),
            )
            for job in jobs:
                try:
                    package = self.tailor_job(int(job["id"]))
                    result["tailored"].append(
                        {
                            "job_id": job["id"],
                            "package_id": package["id"],
                            "fit_score": package["fit_score"],
                            "recommended": package["fit_score"] >= self.settings.min_fit_score,
                        }
                    )
                except Exception as error:
                    result["errors"].append({"job_id": job["id"], "error": str(error)})
            result["auto_tailor"] = "complete"

        # Independent of auto-tailor: autonomous submission and cold outreach each have their
        # own master toggle (AUTO_SUBMIT / AUTO_OUTREACH) so they can run on their own schedule.
        if self.settings.auto_submit:
            result["auto_submit"] = self.run_auto_submit_cycle()
        if self.settings.auto_outreach:
            result["auto_outreach"] = self.run_outreach_cycle()
        return result

    def _adapter_is_live(self, adapter_name: str) -> bool:
        if not self.settings.enable_live_applications:
            return False
        return {
            "greenhouse": self.settings.enable_live_greenhouse,
            "lever": self.settings.enable_live_lever,
        }.get(adapter_name, False)

    def run_auto_submit_cycle(self) -> dict[str, Any]:
        """Autonomously decide -> prepare -> submit for high-fit packages with a known adapter.

        No human click: this is the AUTO_SUBMIT-gated replacement for a person using the
        dashboard's Approve button and "Open approved application". Each adapter stays in
        dry-run (fills the form, screenshots it, never clicks Submit) until both
        ENABLE_LIVE_APPLICATIONS and that adapter's own ENABLE_LIVE_* flag are set.
        """
        result: dict[str, Any] = {"submitted": [], "dry_run": [], "needs_review": [], "errors": []}
        profile = self.get_profile()
        candidates = self.db.all(
            """SELECT packages.id AS package_id, jobs.url, jobs.id AS job_id FROM packages
               JOIN jobs ON jobs.id=packages.job_id
               WHERE packages.decision='pending' AND packages.fit_score >= ?
               ORDER BY packages.fit_score DESC, packages.created_at DESC LIMIT ?""",
            (self.settings.auto_submit_min_fit_score, self.settings.max_applications_per_cycle),
        )
        for row in candidates:
            package_id = int(row["package_id"])
            adapter = find_adapter(row["url"])
            if not adapter:
                continue
            try:
                approved = self.decide(package_id, "approved")
                prepared = self.prepare_application(package_id, approved["approval_token"])
                submission_dir = (
                    Path(self.settings.artifact_dir).resolve() / f"package-{package_id}" / "submission"
                )
                live = self._adapter_is_live(adapter.name)
                outcome = adapter.submit(prepared["url"], approved, profile, submission_dir, dry_run=not live)
                if outcome.status == "submitted":
                    self.mark_submitted(
                        package_id, approved["approval_token"],
                        outcome.confirmation_reference or outcome.log_path,
                    )
                    result["submitted"].append({"package_id": package_id})
                else:
                    self.db.execute("UPDATE jobs SET status='dry_run_submitted' WHERE id=?", (row["job_id"],))
                    self.db.audit("application_dry_run", "package", package_id, {"log_path": outcome.log_path})
                    result["dry_run"].append({"package_id": package_id, "log_path": outcome.log_path})
            except AdapterUnmappedQuestionError as error:
                self.db.execute("UPDATE jobs SET status='needs_manual_review' WHERE id=?", (row["job_id"],))
                self.db.audit("application_needs_review", "package", package_id, {"reason": str(error)})
                result["needs_review"].append({"package_id": package_id, "reason": str(error)})
            except Exception as error:
                result["errors"].append({"package_id": package_id, "error": str(error)})
        return result

    def sync_yc_companies(self) -> dict[str, Any]:
        candidate_context = load_candidate_context(self.settings.candidate_context_path)
        companies = YCCompanySource().fetch(self.settings.yc_min_batch_year)
        inserted = 0
        eligible = 0
        scored: list[tuple[int, Any]] = []
        for company in companies:
            fit_score, fit_reasons = score_yc_company(company, candidate_context)
            if fit_score >= self.settings.yc_outreach_min_fit_score:
                eligible += 1
            inserted += self._add_yc_company(company, fit_score, fit_reasons)
            scored.append((fit_score, company))
        enriched = 0
        public_emails = 0
        for fit_score, company in sorted(scored, key=lambda item: item[0], reverse=True):
            if fit_score < self.settings.yc_outreach_min_fit_score:
                continue
            if enriched >= self.settings.yc_enrich_max_companies:
                break
            emails = self._discover_yc_page_emails(company.slug, company.domain)
            if emails:
                public_emails += self._add_yc_contacts(company.slug, emails, "public_page")
            enriched += 1
        return {
            "fetched": len(companies), "inserted": inserted, "eligible": eligible,
            "enriched": enriched, "public_emails": public_emails,
        }

    def _add_yc_company(self, company: Any, fit_score: int = 0, fit_reasons: list[str] | None = None) -> int:
        with self.db.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM yc_companies WHERE slug=?", (company.slug,)
            ).fetchone()
            cursor = connection.execute(
                """INSERT INTO yc_companies
                   (slug, name, domain, batch, one_liner, tags, status, fit_score, fit_reasons)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(slug) DO UPDATE SET
                       name=excluded.name, domain=excluded.domain, batch=excluded.batch,
                       one_liner=excluded.one_liner, tags=excluded.tags, status=excluded.status,
                       fit_score=excluded.fit_score, fit_reasons=excluded.fit_reasons""",
                (
                    company.slug, company.name, company.domain, company.batch,
                    company.one_liner, company.tags, company.status, fit_score,
                    json.dumps(fit_reasons or []),
                ),
            )
            company_id = (existing["id"] if existing else cursor.lastrowid)
            for email in guess_contact_emails(company.domain):
                connection.execute(
                    "INSERT OR IGNORE INTO yc_contacts (company_id, email, alias_type) VALUES (?, ?, ?)",
                    (company_id, email.lower(), email.split("@")[0]),
                )
        return 0 if existing else 1

    def _add_yc_contacts(self, slug: str, emails: list[str], alias_type: str) -> int:
        if not emails:
            return 0
        company = self.db.one("SELECT id FROM yc_companies WHERE slug=?", (slug,))
        if not company:
            return 0
        added = 0
        with self.db.connect() as connection:
            for email in emails:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO yc_contacts (company_id, email, alias_type) VALUES (?, ?, ?)",
                    (company["id"], email.lower(), alias_type),
                )
                added += int(cursor.rowcount)
                if alias_type == "public_page" and cursor.rowcount == 0:
                    connection.execute(
                        "UPDATE yc_contacts SET alias_type='public_page' WHERE company_id=? AND email=?",
                        (company["id"], email.lower()),
                    )
        return added

    def _discover_yc_page_emails(self, slug: str, domain: str) -> list[str]:
        url = f"https://www.ycombinator.com/companies/{slug}"
        request = urllib.request.Request(url, headers={"User-Agent": "approval-first-job-agent/0.2"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                html_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.URLError:
            return []
        return extract_public_emails(html_text, domain)

    def list_yc_companies(self) -> list[dict[str, Any]]:
        return self.db.all(
            """SELECT yc_companies.*, outreach_packages.id AS outreach_id,
                      outreach_packages.decision AS outreach_decision, outreach_packages.sent_at,
                      outreach_packages.contact_email,
                      (SELECT COUNT(*) FROM yc_contacts
                       WHERE yc_contacts.company_id=yc_companies.id AND yc_contacts.status='pending') AS pending_contacts,
                      (SELECT COUNT(*) FROM yc_contacts
                       WHERE yc_contacts.company_id=yc_companies.id AND yc_contacts.status='bounced') AS bounced_contacts
               FROM yc_companies LEFT JOIN outreach_packages ON outreach_packages.company_id=yc_companies.id
               ORDER BY yc_companies.fit_score DESC, yc_companies.added_at DESC"""
        )

    def generate_outreach(self, company_id: int) -> dict[str, Any]:
        profile = self.get_profile()
        candidate_context = load_candidate_context(self.settings.candidate_context_path)
        company = self.db.one("SELECT * FROM yc_companies WHERE id=?", (company_id,))
        if not company:
            raise ValueError("YC company not found")
        if not has_mx_record(company["domain"]):
            self.db.execute(
                "UPDATE yc_contacts SET status='bounced', last_error='domain has no MX record' "
                "WHERE company_id=? AND status='pending'",
                (company_id,),
            )
            self.db.audit(
                "outreach_skipped_no_mx", "yc_company", company_id, {"domain": company["domain"]}
            )
            raise ValueError(f"{company['name']}: domain has no mail server (MX record); skipping outreach")
        # Contacts are inserted in CONTACT_ALIASES priority order (careers, jobs, talent, hi,
        # hello), so the lowest-id 'pending' row is always the next-best untried guess.
        contact = self.db.one(
            """SELECT * FROM yc_contacts WHERE company_id=? AND status='pending'
               ORDER BY CASE alias_type
                   WHEN 'public_page' THEN 0 WHEN 'founders' THEN 1 WHEN 'careers' THEN 2
                   WHEN 'jobs' THEN 3 WHEN 'talent' THEN 4 WHEN 'hi' THEN 5
                   WHEN 'hello' THEN 6 WHEN 'contact' THEN 7 WHEN 'team' THEN 8
                   ELSE 9 END, id LIMIT 1""",
            (company_id,),
        )
        if not contact:
            raise ValueError("No usable contact email left for this company (all guesses bounced)")
        composer = OutreachComposer(self.settings.openai_api_key, self.settings.openai_model)
        package = composer.create(profile, company, candidate_context)
        outreach_id = self._upsert_outreach(company_id, contact["email"], package)
        artifacts = write_outreach_artifacts(
            self.settings.artifact_dir, outreach_id, package.resume_data, candidate_context,
            folder_prefix="startup-outreach",
        )
        self.db.execute(
            "UPDATE outreach_packages SET resume_pdf_path=? WHERE id=?",
            (artifacts.resume_pdf_path, outreach_id),
        )
        self.db.audit("outreach_drafted", "outreach_package", outreach_id, {"company_id": company_id})
        return self.get_outreach(outreach_id) or {}

    def _upsert_outreach(self, company_id: int, contact_email: str, package: Any) -> int:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO outreach_packages (company_id, contact_email, subject, body, resume_data)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(company_id) DO UPDATE SET
                       contact_email=excluded.contact_email, subject=excluded.subject,
                       body=excluded.body, resume_data=excluded.resume_data,
                       created_at=CURRENT_TIMESTAMP, decision='drafted', sent_at=NULL""",
                (company_id, contact_email, package.subject, package.body, json.dumps(package.resume_data)),
            )
            row = connection.execute(
                "SELECT id FROM outreach_packages WHERE company_id=?", (company_id,)
            ).fetchone()
            return int(row["id"])

    def get_outreach(self, outreach_id: int) -> dict[str, Any] | None:
        row = self.db.one(
            """SELECT outreach_packages.*, yc_companies.name AS company_name, yc_companies.domain
               FROM outreach_packages JOIN yc_companies ON yc_companies.id=outreach_packages.company_id
               WHERE outreach_packages.id=?""",
            (outreach_id,),
        )
        if row:
            row["resume_data"] = json.loads(row["resume_data"])
        return row

    def list_outreach(self) -> list[dict[str, Any]]:
        return self.db.all(
            """SELECT outreach_packages.id, outreach_packages.company_id, outreach_packages.contact_email,
                      outreach_packages.subject, outreach_packages.decision, outreach_packages.dry_run,
                      outreach_packages.sent_at, outreach_packages.created_at,
                      yc_companies.name AS company_name, yc_companies.domain
               FROM outreach_packages JOIN yc_companies ON yc_companies.id=outreach_packages.company_id
               ORDER BY outreach_packages.created_at DESC"""
        )

    def send_outreach(self, outreach_id: int) -> dict[str, Any]:
        outreach = self.get_outreach(outreach_id)
        if not outreach:
            raise ValueError("Outreach package not found")
        dry_run = not self.settings.enable_live_outreach
        try:
            outcome = send_outreach_email(
                outreach_id, outreach["contact_email"], outreach["subject"], outreach["body"],
                outreach.get("resume_pdf_path", ""), self.settings, dry_run=dry_run,
            )
        except OutreachBounceError as error:
            return self._handle_outreach_bounce(outreach, error)
        decision = "sent" if outcome.status == "sent" else "dry_run"
        self.db.execute(
            """UPDATE outreach_packages SET decision=?, dry_run=?,
               sent_at=CASE WHEN ?='sent' THEN CURRENT_TIMESTAMP ELSE sent_at END WHERE id=?""",
            (decision, 0 if outcome.status == "sent" else 1, decision, outreach_id),
        )
        self.db.audit(
            "outreach_sent" if outcome.status == "sent" else "outreach_dry_run",
            "outreach_package", outreach_id, {"contact_email": outreach["contact_email"]},
        )
        return self.get_outreach(outreach_id) or {}

    def _handle_outreach_bounce(self, outreach: dict[str, Any], error: Exception) -> dict[str, Any]:
        """A live send was rejected outright: mark that guess bad and requeue the next one.

        This is what turns "one guessed alias, no fallback" into an actual fallback chain —
        the next run_outreach_cycle (or a manual "Send now") picks up the retry automatically
        because decision is reset to 'drafted'.
        """
        outreach_id = outreach["id"]
        self.db.execute(
            "UPDATE yc_contacts SET status='bounced', last_error=? WHERE company_id=? AND email=?",
            (str(error), outreach["company_id"], outreach["contact_email"]),
        )
        self.db.audit(
            "outreach_bounced", "outreach_package", outreach_id,
            {"contact_email": outreach["contact_email"], "error": str(error)},
        )
        next_contact = self.db.one(
            """SELECT * FROM yc_contacts WHERE company_id=? AND status='pending'
               ORDER BY CASE alias_type
                   WHEN 'public_page' THEN 0 WHEN 'founders' THEN 1 WHEN 'careers' THEN 2
                   WHEN 'jobs' THEN 3 WHEN 'talent' THEN 4 WHEN 'hi' THEN 5
                   WHEN 'hello' THEN 6 WHEN 'contact' THEN 7 WHEN 'team' THEN 8
                   ELSE 9 END, id LIMIT 1""",
            (outreach["company_id"],),
        )
        if next_contact:
            self.db.execute(
                "UPDATE outreach_packages SET contact_email=?, decision='drafted', sent_at=NULL WHERE id=?",
                (next_contact["email"], outreach_id),
            )
            self.db.audit(
                "outreach_retry_queued", "outreach_package", outreach_id,
                {"contact_email": next_contact["email"]},
            )
        else:
            self.db.execute("UPDATE outreach_packages SET decision='exhausted' WHERE id=?", (outreach_id,))
            self.db.audit("outreach_exhausted", "outreach_package", outreach_id, {})
        return self.get_outreach(outreach_id) or {}

    def run_outreach_cycle(self) -> dict[str, Any]:
        result: dict[str, Any] = {"sync": self.sync_yc_companies(), "drafted": [], "sent": [], "errors": []}
        if not self.settings.openai_api_key:
            result["outreach"] = "skipped: OPENAI_API_KEY is missing"
            return result
        pending_companies = self.db.all(
            """SELECT yc_companies.id FROM yc_companies
               LEFT JOIN outreach_packages ON outreach_packages.company_id=yc_companies.id
               WHERE outreach_packages.id IS NULL
                 AND yc_companies.fit_score >= ?
                 AND EXISTS (
                     SELECT 1 FROM yc_contacts
                     WHERE yc_contacts.company_id=yc_companies.id AND yc_contacts.status='pending'
                 )
               ORDER BY yc_companies.fit_score DESC, yc_companies.added_at DESC LIMIT ?""",
            (self.settings.yc_outreach_min_fit_score, self.settings.max_outreach_per_cycle),
        )
        for row in pending_companies:
            try:
                outreach = self.generate_outreach(int(row["id"]))
                result["drafted"].append({"company_id": row["id"], "outreach_id": outreach["id"]})
            except Exception as error:
                result["errors"].append({"company_id": row["id"], "error": str(error)})
        to_send = self.db.all(
            "SELECT id FROM outreach_packages WHERE decision='drafted' AND sent_at IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (self.settings.max_outreach_per_cycle,),
        )
        for row in to_send:
            try:
                sent = self.send_outreach(int(row["id"]))
                result["sent"].append({"outreach_id": row["id"], "status": sent["decision"]})
            except Exception as error:
                result["errors"].append({"outreach_id": row["id"], "error": str(error)})
        result["outreach"] = "complete"
        return result

    def sync_startups(self) -> dict[str, Any]:
        candidate_context = load_candidate_context(self.settings.candidate_context_path)
        result: dict[str, Any] = {
            "sources": {}, "fetched": 0, "inserted": 0, "eligible": 0,
            "contacts": 0, "errors": [],
        }
        source_batches: list[tuple[str, list[StartupCompany]]] = []
        source_calls = [
            ("yc_directory", lambda: YCStartupSource().fetch(self.settings.yc_min_batch_year, candidate_context)),
            (
                "sec_form_d",
                lambda: SECFormDSource().fetch(
                    self.settings.startup_sec_form_d_days,
                    self.settings.startup_sec_form_d_limit,
                    candidate_context,
                ),
            ),
        ]
        if self.settings.startup_portfolio_urls:
            source_calls.append((
                "portfolio_page",
                lambda: PortfolioPageSource().fetch(self.settings.startup_portfolio_urls, candidate_context),
            ))
        for source_name, source_call in source_calls:
            try:
                companies = source_call()
                source_batches.append((source_name, companies))
                result["sources"][source_name] = len(companies)
                result["fetched"] += len(companies)
            except Exception as error:
                result["errors"].append({"source": source_name, "error": str(error)})

        all_companies: list[StartupCompany] = []
        for _, companies in source_batches:
            for company in companies:
                result["inserted"] += self._add_startup_company(company)
                all_companies.append(company)
        eligible = [company for company in all_companies if company.fit_score >= self.settings.startup_min_fit_score]
        result["eligible"] = len(eligible)
        enriched = 0
        for company in sorted(eligible, key=lambda item: item.fit_score, reverse=True):
            enrich = enriched < self.settings.startup_enrich_max_companies
            contacts = discover_contacts(company, enrich=enrich)
            if enrich:
                enriched += 1
            result["contacts"] += self._add_startup_contacts(company.source, company.source_id, contacts)
        result["enriched"] = enriched
        return result

    def _add_startup_company(self, company: StartupCompany) -> int:
        with self.db.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM startup_companies WHERE source=? AND source_id=?",
                (company.source, company.source_id),
            ).fetchone()
            connection.execute(
                """INSERT INTO startup_companies
                   (source, source_id, name, domain, country, region, stage, funding_signal,
                    funding_date, funding_amount, evidence_url, description, tags, fit_score, fit_reasons)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, source_id) DO UPDATE SET
                       name=excluded.name, domain=excluded.domain, country=excluded.country,
                       region=excluded.region, stage=excluded.stage,
                       funding_signal=excluded.funding_signal, funding_date=excluded.funding_date,
                       funding_amount=excluded.funding_amount, evidence_url=excluded.evidence_url,
                       description=excluded.description, tags=excluded.tags,
                       fit_score=excluded.fit_score, fit_reasons=excluded.fit_reasons""",
                (
                    company.source, company.source_id, company.name, company.domain,
                    company.country, company.region, company.stage, company.funding_signal,
                    company.funding_date, company.funding_amount, company.evidence_url,
                    company.description, company.tags, company.fit_score,
                    json.dumps(list(company.fit_reasons)),
                ),
            )
        return 0 if existing else 1

    def _add_startup_contacts(self, source: str, source_id: str, contacts: list[tuple[str, str]]) -> int:
        company = self.db.one(
            "SELECT id FROM startup_companies WHERE source=? AND source_id=?", (source, source_id)
        )
        if not company:
            return 0
        added = 0
        with self.db.connect() as connection:
            for email, alias_type in contacts:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO startup_contacts (company_id, email, alias_type) VALUES (?, ?, ?)",
                    (company["id"], email.lower(), alias_type),
                )
                added += int(cursor.rowcount)
                if alias_type == "public_page" and cursor.rowcount == 0:
                    connection.execute(
                        "UPDATE startup_contacts SET alias_type='public_page' WHERE company_id=? AND email=?",
                        (company["id"], email.lower()),
                    )
        return added

    def list_startups(self, limit: int = 250) -> list[dict[str, Any]]:
        return self.db.all(
            """SELECT startup_companies.*, startup_outreach_packages.id AS outreach_id,
                      startup_outreach_packages.decision AS outreach_decision,
                      startup_outreach_packages.contact_email,
                      (SELECT COUNT(*) FROM startup_contacts
                       WHERE startup_contacts.company_id=startup_companies.id AND startup_contacts.status='pending') AS pending_contacts
               FROM startup_companies
               LEFT JOIN startup_outreach_packages ON startup_outreach_packages.company_id=startup_companies.id
               ORDER BY startup_companies.fit_score DESC, startup_companies.added_at DESC
               LIMIT ?""",
            (limit,),
        )

    def generate_startup_outreach(self, company_id: int) -> dict[str, Any]:
        profile = self.get_profile()
        candidate_context = load_candidate_context(self.settings.candidate_context_path)
        company = self.db.one("SELECT * FROM startup_companies WHERE id=?", (company_id,))
        if not company:
            raise ValueError("Startup company not found")
        if not company.get("domain"):
            raise ValueError(f"{company['name']}: no domain available for outreach")
        if not has_mx_record(company["domain"]):
            self.db.execute(
                "UPDATE startup_contacts SET status='bounced', last_error='domain has no MX record' "
                "WHERE company_id=? AND status='pending'",
                (company_id,),
            )
            raise ValueError(f"{company['name']}: domain has no mail server (MX record); skipping outreach")
        contact = self.db.one(
            """SELECT * FROM startup_contacts WHERE company_id=? AND status='pending'
               ORDER BY CASE alias_type
                   WHEN 'public_page' THEN 0 WHEN 'founders' THEN 1 WHEN 'careers' THEN 2
                   WHEN 'jobs' THEN 3 WHEN 'talent' THEN 4 WHEN 'hi' THEN 5
                   WHEN 'hello' THEN 6 WHEN 'contact' THEN 7 WHEN 'team' THEN 8
                   ELSE 9 END, id LIMIT 1""",
            (company_id,),
        )
        if not contact:
            raise ValueError("No usable contact email left for this startup")
        composer = OutreachComposer(self.settings.openai_api_key, self.settings.openai_model)
        package = composer.create(
            profile,
            {
                "name": company["name"],
                "domain": company["domain"],
                "batch": company["stage"],
                "one_liner": company["description"] or company["funding_signal"],
                "tags": company["tags"],
            },
            candidate_context,
        )
        outreach_id = self._upsert_startup_outreach(company_id, contact["email"], package)
        artifacts = write_outreach_artifacts(
            self.settings.artifact_dir, outreach_id, package.resume_data, candidate_context
        )
        self.db.execute(
            "UPDATE startup_outreach_packages SET resume_pdf_path=? WHERE id=?",
            (artifacts.resume_pdf_path, outreach_id),
        )
        self.db.audit("startup_outreach_drafted", "startup_outreach_package", outreach_id, {"company_id": company_id})
        return self.get_startup_outreach(outreach_id) or {}

    def _upsert_startup_outreach(self, company_id: int, contact_email: str, package: Any) -> int:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO startup_outreach_packages (company_id, contact_email, subject, body, resume_data)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(company_id) DO UPDATE SET
                       contact_email=excluded.contact_email, subject=excluded.subject,
                       body=excluded.body, resume_data=excluded.resume_data,
                       created_at=CURRENT_TIMESTAMP, decision='drafted'""",
                (company_id, contact_email, package.subject, package.body, json.dumps(package.resume_data)),
            )
            row = connection.execute(
                "SELECT id FROM startup_outreach_packages WHERE company_id=?", (company_id,)
            ).fetchone()
            return int(row["id"])

    def get_startup_outreach(self, outreach_id: int) -> dict[str, Any] | None:
        row = self.db.one(
            """SELECT startup_outreach_packages.*, startup_companies.name AS company_name,
                      startup_companies.domain
               FROM startup_outreach_packages
               JOIN startup_companies ON startup_companies.id=startup_outreach_packages.company_id
               WHERE startup_outreach_packages.id=?""",
            (outreach_id,),
        )
        if row:
            row["resume_data"] = json.loads(row["resume_data"])
        return row

    def run_startup_outreach_cycle(self) -> dict[str, Any]:
        result: dict[str, Any] = {"sync": self.sync_startups(), "drafted": [], "errors": []}
        if not self.settings.openai_api_key:
            result["outreach"] = "skipped: OPENAI_API_KEY is missing"
            return result
        companies = self.db.all(
            """SELECT startup_companies.id FROM startup_companies
               LEFT JOIN startup_outreach_packages ON startup_outreach_packages.company_id=startup_companies.id
               WHERE startup_outreach_packages.id IS NULL
                 AND startup_companies.fit_score >= ?
                 AND EXISTS (
                     SELECT 1 FROM startup_contacts
                     WHERE startup_contacts.company_id=startup_companies.id AND startup_contacts.status='pending'
                 )
               ORDER BY startup_companies.fit_score DESC, startup_companies.added_at DESC
               LIMIT ?""",
            (self.settings.startup_min_fit_score, self.settings.max_outreach_per_cycle),
        )
        for row in companies:
            try:
                outreach = self.generate_startup_outreach(int(row["id"]))
                result["drafted"].append({"company_id": row["id"], "outreach_id": outreach["id"]})
            except Exception as error:
                result["errors"].append({"company_id": row["id"], "error": str(error)})
        result["outreach"] = "complete"
        return result

    def add_job(self, listing: JobListing, prescreen: int = 0) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO jobs
                (source, source_id, title, company, location, url, description, salary,
                 published_at, role_lane, search_region, prescreen_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    listing.source, listing.source_id, listing.title, listing.company,
                    listing.location, listing.url, listing.description, listing.salary,
                    listing.published_at, listing.role_lane or classify_role_lane(listing.title),
                    listing.search_region or classify_job_region(listing.location), prescreen,
                ),
            )
            inserted = cursor.rowcount
        return int(inserted)

    def add_manual_job(self, data: dict[str, Any]) -> dict[str, Any]:
        required = ("title", "company", "url", "description")
        missing = [field for field in required if not str(data.get(field, "")).strip()]
        if missing:
            raise ValueError("Missing fields: " + ", ".join(missing))
        source_id = secrets.token_hex(12)
        listing = JobListing(
            source="manual",
            source_id=source_id,
            title=str(data["title"]).strip(),
            company=str(data["company"]).strip(),
            location=str(data.get("location", "")).strip(),
            url=str(data["url"]).strip(),
            description=str(data["description"]).strip(),
            salary=str(data.get("salary", "")).strip(),
            role_lane=classify_role_lane(str(data["title"])),
            search_region=classify_job_region(str(data.get("location", ""))),
        )
        self.add_job(listing)
        return self.db.one("SELECT * FROM jobs WHERE source=? AND source_id=?", ("manual", source_id)) or {}

    def list_jobs(self) -> list[dict[str, Any]]:
        return self.db.all(
            """SELECT jobs.*, packages.id AS package_id, packages.fit_score, packages.decision,
                      packages.submitted_at
               FROM jobs LEFT JOIN packages ON packages.job_id=jobs.id
               ORDER BY jobs.discovered_at DESC, jobs.id DESC"""
        )

    def get_package(self, package_id: int) -> dict[str, Any] | None:
        row = self.db.one(
            """SELECT packages.*, jobs.title, jobs.company, jobs.location, jobs.url,
                      jobs.description, jobs.salary
               FROM packages JOIN jobs ON jobs.id=packages.job_id WHERE packages.id=?""",
            (package_id,),
        )
        if row:
            for field in ("missing_requirements", "screening_notes", "resume_data"):
                row[field] = json.loads(row[field])
        return row

    def tailor_job(self, job_id: int) -> dict[str, Any]:
        profile = self.get_profile()
        candidate_context = load_candidate_context(self.settings.candidate_context_path)
        resume_template = load_resume_template(self.settings.resume_template_path)
        if not profile.get("base_resume") and not candidate_context:
            raise ValueError("Save a base resume or provide candidate context before tailoring")
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise ValueError("Job not found")
        tailor = OpenAITailor(self.settings.openai_api_key, self.settings.openai_model)
        package = tailor.create(profile, job, candidate_context, resume_template)
        package_id = self._upsert_package(job_id, package)
        artifacts = write_package_artifacts(
            self.settings.artifact_dir, package_id, package, candidate_context
        )
        self.db.execute(
            "UPDATE packages SET resume_path=?, resume_pdf_path=?, cover_letter_path=? WHERE id=?",
            (
                artifacts.resume_path, artifacts.resume_pdf_path,
                artifacts.cover_letter_path, package_id,
            ),
        )
        self.db.execute("UPDATE jobs SET status='review' WHERE id=?", (job_id,))
        self.db.audit("package_created", "package", package_id, {"job_id": job_id})
        return self.get_package(package_id) or {}

    def _upsert_package(self, job_id: int, package: TailoredPackage) -> int:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO packages
                (job_id, fit_score, fit_summary, missing_requirements, tailored_resume,
                 resume_data, cover_letter, screening_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    fit_score=excluded.fit_score,
                    fit_summary=excluded.fit_summary,
                    missing_requirements=excluded.missing_requirements,
                    tailored_resume=excluded.tailored_resume,
                    resume_data=excluded.resume_data,
                    cover_letter=excluded.cover_letter,
                    screening_notes=excluded.screening_notes,
                    created_at=CURRENT_TIMESTAMP,
                    decision='pending', decision_at=NULL, approval_token=NULL""",
                (
                    job_id, package.fit_score, package.fit_summary,
                    json.dumps(package.missing_requirements), package.tailored_resume,
                    json.dumps(package.resume_data), package.cover_letter,
                    json.dumps(package.screening_notes),
                ),
            )
            row = connection.execute("SELECT id FROM packages WHERE job_id=?", (job_id,)).fetchone()
            return int(row["id"])

    def get_automation_log(self, limit: int = 50) -> list[dict[str, Any]]:
        events = (
            "application_handoff", "application_submitted", "application_dry_run",
            "application_needs_review", "outreach_drafted", "outreach_sent", "outreach_dry_run",
        )
        placeholders = ", ".join("?" for _ in events)
        rows = self.db.all(
            f"""SELECT * FROM audit_log WHERE event IN ({placeholders})
                ORDER BY created_at DESC, id DESC LIMIT ?""",
            (*events, limit),
        )
        for row in rows:
            row["details"] = json.loads(row["details"])
        return rows

    def get_artifact(self, package_id: int, kind: str) -> tuple[Path, str] | None:
        fields = {
            "resume.tex": ("resume_path", "text/x-tex"),
            "resume.pdf": ("resume_pdf_path", "application/pdf"),
            "cover-letter.txt": ("cover_letter_path", "text/plain; charset=utf-8"),
        }
        if kind not in fields:
            return None
        package = self.db.one("SELECT * FROM packages WHERE id=?", (package_id,))
        if not package:
            return None
        field, content_type = fields[kind]
        raw_path = package.get(field, "")
        if not raw_path:
            return None
        path = Path(raw_path).resolve()
        root = self.settings.artifact_dir.resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path, content_type

    def decide(self, package_id: int, decision: str) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be approved or rejected")
        package = self.get_package(package_id)
        if not package:
            raise ValueError("Package not found")
        token = secrets.token_urlsafe(24) if decision == "approved" else None
        self.db.execute(
            "UPDATE packages SET decision=?, decision_at=CURRENT_TIMESTAMP, approval_token=? WHERE id=?",
            (decision, token, package_id),
        )
        self.db.execute(
            "UPDATE jobs SET status=? WHERE id=?",
            ("approved" if decision == "approved" else "rejected", package["job_id"]),
        )
        self.db.audit("package_decided", "package", package_id, {"decision": decision})
        return self.get_package(package_id) or {}

    def prepare_application(self, package_id: int, approval_token: str) -> dict[str, Any]:
        package = self.get_package(package_id)
        if not package:
            raise ValueError("Package not found")
        if package["decision"] != "approved" or not package.get("approval_token"):
            raise ApprovalError("Application has not been approved")
        if not secrets.compare_digest(package["approval_token"], approval_token):
            raise ApprovalError("Approval token is invalid")
        # Generic job sites have incompatible forms and anti-bot requirements. This handoff is
        # deliberate: a site adapter may call mark_submitted only after it confirms submission.
        self.db.audit("application_handoff", "package", package_id, {"url": package["url"]})
        return {
            "status": "ready_for_site_adapter",
            "url": package["url"],
            "package_id": package_id,
            "message": "Approval verified. Open the job URL or connect a site-specific submitter.",
        }

    def mark_submitted(self, package_id: int, approval_token: str, reference: str) -> dict[str, Any]:
        if not self.settings.enable_live_applications:
            raise ApprovalError("Live applications are disabled")
        self.prepare_application(package_id, approval_token)
        if not reference.strip():
            raise ValueError("A submission confirmation reference is required")
        self.db.execute(
            "UPDATE packages SET submitted_at=CURRENT_TIMESTAMP, submission_reference=? WHERE id=?",
            (reference.strip(), package_id),
        )
        package = self.get_package(package_id) or {}
        self.db.execute("UPDATE jobs SET status='submitted' WHERE id=?", (package["job_id"],))
        self.db.audit("application_submitted", "package", package_id, {"reference": reference.strip()})
        return self.get_package(package_id) or {}
