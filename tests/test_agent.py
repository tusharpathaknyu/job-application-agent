import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from job_agent.adapters import SubmissionResult, UnmappedQuestionError, find_adapter
from job_agent.adapters.base import is_sensitive_question, mapped_or_raise, map_known_field
from job_agent.artifacts import write_package_artifacts
from job_agent.config import Settings
from job_agent.context import context_summary, load_candidate_context
from job_agent.db import Database
from job_agent.outreach import OutreachBounceError, send_outreach
from job_agent.service import ApprovalError, JobAgentService
from job_agent.sources import (
    ATSJobSource, ArbeitnowSource, JobListing, OpenAIWebJobSource, RemotiveSource,
    YCJobSource, classify_job_region, classify_role_lane, extract_ats_board_targets,
    group_queries_by_lane, matches_role_query, normalize_dedupe_key, parse_ats_board_target,
    prescreen_score, strip_html,
)
from job_agent.startup_sources import SECFormDSource, StartupCompany, discover_contacts, score_startup_company
from job_agent.server import require_local_host
from job_agent.tailor import TailoredPackage
from job_agent.yc_source import (
    YCCompany, YCCompanySource, build_mx_query, extract_public_emails, guess_contact_emails,
    has_mx_record, parse_answer_count, score_yc_company,
)


def settings(path: Path, live: bool = False) -> Settings:
    return Settings(
        openai_api_key="",
        openai_model="gpt-5.5",
        host="127.0.0.1",
        port=8787,
        hosted_mode=False,
        app_username="tushar",
        app_password="",
        database_path=path,
        job_search_query="engineer",
        job_search_limit=10,
        sync_interval_minutes=60,
        auto_tailor=True,
        max_tailors_per_cycle=5,
        min_fit_score=55,
        enable_live_applications=live,
    )


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        self.service = JobAgentService(settings(self.path), Database(self.path))
        self.service.save_profile({"full_name": "A Candidate", "base_resume": "Built reliable APIs."})
        self.service.add_job(JobListing("manual", "1", "Engineer", "Acme", "Remote", "https://example.com/job", "Build APIs"))
        self.package_id = self.service._upsert_package(1, TailoredPackage(85, "Strong fit", [], "Resume", "Letter", []))

    def tearDown(self):
        self.temp.cleanup()

    def test_submission_handoff_requires_approval(self):
        with self.assertRaises(ApprovalError):
            self.service.prepare_application(self.package_id, "wrong")

    def test_approval_token_is_scoped_and_required(self):
        approved = self.service.decide(self.package_id, "approved")
        with self.assertRaises(ApprovalError):
            self.service.prepare_application(self.package_id, "wrong")
        result = self.service.prepare_application(self.package_id, approved["approval_token"])
        self.assertEqual(result["status"], "ready_for_site_adapter")

    def test_live_submission_is_disabled_by_default(self):
        approved = self.service.decide(self.package_id, "approved")
        with self.assertRaisesRegex(ApprovalError, "disabled"):
            self.service.mark_submitted(self.package_id, approved["approval_token"], "confirmation")

    def test_automatic_cycle_skips_tailoring_without_api_key(self):
        self.service.sync_jobs = lambda: {"fetched": 0, "inserted": 0}
        result = self.service.run_automatic_cycle()
        self.assertIn("OPENAI_API_KEY", result["auto_tailor"])

    def test_remotive_parser_and_html_cleanup(self):
        payload = {"jobs": [{"id": 7, "title": "Dev", "company_name": "Co", "url": "https://e.test", "description": "<p>Hello <b>world</b></p>"}]}
        jobs = RemotiveSource.parse(payload)
        self.assertEqual(jobs[0].source_id, "7")
        self.assertEqual(jobs[0].description, "Hello world")
        self.assertEqual(strip_html("A<br>B"), "A\nB")
        self.assertTrue(matches_role_query("Senior Backend Engineer", "backend engineer"))
        self.assertTrue(matches_role_query("Tech Lead Full-Stack Rails Engineer", "full stack engineer"))
        self.assertFalse(matches_role_query("Inside Sales Contractor", "software engineer"))

    def test_arbeitnow_parser_keeps_remote_jobs(self):
        payload = {"data": [
            {"slug": "remote-role", "title": "Backend Engineer", "company_name": "Co", "remote": True, "url": "https://e.test/remote", "description": "<p>APIs</p>", "location": "Remote", "created_at": 0},
            {"slug": "onsite-role", "title": "Backend Engineer", "company_name": "Co", "remote": False, "url": "https://e.test/onsite", "description": "APIs", "location": "Berlin", "created_at": 0},
        ]}
        jobs = ArbeitnowSource.parse(payload)
        self.assertEqual([job.source_id for job in jobs], ["remote-role"])

    def test_ats_board_target_parsing_and_link_extraction(self):
        target = parse_ats_board_target("greenhouse:openai")
        self.assertEqual(target.slug, "openai")
        self.assertIsNone(parse_ats_board_target("unknown:openai"))
        html = """
        <a href="https://boards.greenhouse.io/diodecomputers">Jobs</a>
        <a href="https://jobs.lever.co/anthropic/abc">Role</a>
        <a href="https://jobs.ashbyhq.com/posthog">Careers</a>
        """
        targets = extract_ats_board_targets(html, "Acme")
        self.assertEqual(
            {(item.ats, item.slug) for item in targets},
            {("greenhouse", "diodecomputers"), ("lever", "anthropic"), ("ashby", "posthog")},
        )

    def test_ats_source_parses_greenhouse_lever_and_ashby(self):
        greenhouse = ATSJobSource.parse_greenhouse({
            "jobs": [{
                "id": 1,
                "title": "FPGA Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "location": {"name": "Toronto, Canada"},
                "content": "<p>Build FPGA systems.</p>",
                "departments": [{"name": "Hardware"}],
            }]
        }, "acme", "Acme")
        lever = ATSJobSource.parse_lever([{
            "id": "abc",
            "text": "Unity Gameplay Engineer",
            "hostedUrl": "https://jobs.lever.co/gameco/abc",
            "categories": {"location": "Remote", "team": "Games", "commitment": "Full-time"},
            "descriptionPlain": "Unity and gameplay systems.",
        }], "gameco", "GameCo")
        ashby = ATSJobSource.parse_ashby({
            "jobs": [{
                "id": "j1",
                "title": "Applications Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/acme/j1",
                "location": {"name": "India"},
                "descriptionHtml": "<p>Customer-facing engineering.</p>",
            }]
        }, "acme", "Acme")
        self.assertEqual(greenhouse[0].company, "Acme")
        self.assertIn("FPGA", greenhouse[0].description)
        self.assertEqual(lever[0].title, "Unity Gameplay Engineer")
        self.assertEqual(ashby[0].location, "India")

    def test_candidate_context_loader_and_summary(self):
        path = Path(self.temp.name) / "context.json"
        path.write_text('{"identity":{"resume_name":"Tushar"},"experience":[{}],"projects":[{},{}],"target_lanes":["hardware"]}')
        context = load_candidate_context(path)
        summary = context_summary(context, "template")
        self.assertEqual(summary["project_count"], 2)
        self.assertTrue(summary["resume_template_loaded"])

    def test_candidate_context_filters_excluded_projects(self):
        path = Path(self.temp.name) / "context.json"
        path.write_text(
            """{
                "excluded_projects": [
                    {"name": "WaveformGPT", "repository": "WaveformGPT"},
                    {"name": "UART UVM Verification", "repository": "uart-verification"}
                ],
                "projects": [
                    {"name": "UVMForge", "repository": "UVMForge"},
                    {"name": "WaveformGPT", "repository": "WaveformGPT"},
                    {"name": "UART UVM Verification", "repository": "uart-verification"}
                ]
            }"""
        )
        context = load_candidate_context(path)
        self.assertEqual([project["name"] for project in context["projects"]], ["UVMForge"])

    def test_multi_query_discovery_deduplicates_jobs(self):
        self.service.settings = replace(
            self.service.settings, job_search_queries=("software engineer", "AI engineer")
        )
        duplicate = JobListing("remotive", "7", "Software Engineer", "Co", "Remote", "https://e.test/7", "Build APIs")
        unique = JobListing("remotive", "8", "AI Engineer", "ML Co", "Remote", "https://e.test/8", "Build models")
        def remotive_results(query, _limit):
            return [duplicate, unique] if query == "AI engineer" else [duplicate]
        with patch("job_agent.service.RemotiveSource.search", side_effect=remotive_results), patch(
            "job_agent.service.ArbeitnowSource.search", side_effect=[[], []]
        ):
            result = self.service.sync_jobs()
        self.assertEqual(result["sources"], 2)
        self.assertEqual(result["queries"], 2)
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["inserted"], 2)

    def test_web_discovery_is_used_when_api_key_is_configured(self):
        self.service.settings = replace(
            self.service.settings,
            openai_api_key="test-key",
            job_search_queries=("software engineer",),
        )
        web_job = JobListing(
            "openai_web_search", "web-1", "Junior Software Engineer", "Co",
            "United States", "https://e.test/web-1", "Build software",
        )
        with patch("job_agent.service.RemotiveSource.search", return_value=[]), patch(
            "job_agent.service.ArbeitnowSource.search", return_value=[]
        ), patch("job_agent.service.OpenAIWebJobSource.search", return_value=[web_job]):
            result = self.service.sync_jobs()
        self.assertEqual(result["sources"], 3)
        self.assertEqual(result["inserted"], 1)

    def test_direct_ats_discovery_is_used_when_targets_exist(self):
        self.service.settings = replace(
            self.service.settings,
            enable_openai_job_search=False,
            enable_yc_job_search=False,
            enable_ats_job_search=True,
            ats_board_targets=("greenhouse:acme",),
            job_search_queries=("FPGA engineer",),
        )
        ats_job = JobListing(
            "greenhouse", "acme:1", "FPGA Engineer", "Acme", "Canada",
            "https://boards.greenhouse.io/acme/jobs/1", "Build FPGA systems.",
        )
        with patch("job_agent.service.RemotiveSource.search", return_value=[]), patch(
            "job_agent.service.ArbeitnowSource.search", return_value=[]
        ), patch("job_agent.service.ATSJobSource.search", return_value=[ats_job]):
            result = self.service.sync_jobs()
        self.assertEqual(result["sources"], 3)
        self.assertEqual(result["inserted"], 1)
        row = self.service.db.one("SELECT * FROM jobs WHERE source='greenhouse'")
        self.assertEqual(row["role_lane"], "FPGA Engineering")

    def test_yc_job_source_extracts_embedded_jobs_and_preserves_visa_note(self):
        embedded = (
            '&quot;jobPostings&quot;:[{&quot;id&quot;:70576,&quot;title&quot;:&quot;Founding Product Engineer&quot;,'
            '&quot;url&quot;:&quot;/companies/seal-2/jobs/n4byaPm-founding-product-engineer&quot;,'
            '&quot;location&quot;:&quot;England, GB&quot;,&quot;type&quot;:&quot;Full-time&quot;,'
            '&quot;roleSpecificType&quot;:&quot;Full stack&quot;,&quot;salaryRange&quot;:&quot;£50K - £120K GBP&quot;,'
            '&quot;equityRange&quot;:&quot;0.10% - 2.00%&quot;,&quot;minExperience&quot;:&quot;Any (new grads ok)&quot;,'
            '&quot;visa&quot;:&quot;US citizenship/visa not required&quot;,&quot;skills&quot;:[&quot;Node.js&quot;],'
            '&quot;companyName&quot;:&quot;Seal&quot;,&quot;companyBatchName&quot;:&quot;S20&quot;,'
            '&quot;companyOneLiner&quot;:&quot;Workspace for regulated products&quot;,'
            '&quot;createdAt&quot;:&quot;over 1 year&quot;,&quot;lastActive&quot;:&quot;about 13 hours&quot;}],'
        )
        payload = YCJobSource._extract_job_postings(embedded)
        jobs = YCJobSource.parse(payload, ("full stack engineer",))
        self.assertEqual(jobs[0].source, "ycombinator")
        self.assertEqual(jobs[0].company, "Seal")
        self.assertIn("US citizenship/visa not required", jobs[0].description)
        self.assertEqual(jobs[0].search_region, "United Kingdom")

    def test_yc_discovery_is_optional_source(self):
        self.service.settings = replace(
            self.service.settings,
            enable_yc_job_search=True,
            job_search_queries=("FPGA engineer",),
        )
        yc_job = JobListing(
            "ycombinator", "yc-1", "Founding Engineer - FPGA Engineer", "Zettascale",
            "San Francisco, CA, US", "https://www.ycombinator.com/companies/z/jobs/1",
            "Verilog and FPGA work. Visa/location note from YC: Will sponsor.",
        )
        with patch("job_agent.service.RemotiveSource.search", return_value=[]), patch(
            "job_agent.service.ArbeitnowSource.search", return_value=[]
        ), patch("job_agent.service.YCJobSource.search", return_value=[yc_job]):
            result = self.service.sync_jobs()
        self.assertEqual(result["sources"], 3)
        self.assertEqual(result["inserted"], 1)

    def test_yc_source_scans_relevant_company_pages_for_embedded_jobs(self):
        company = YCCompany(
            slug="steinmetz", name="Steinmetz", domain="steinmetzmotors.com",
            batch="Winter 2025", one_liner="Next-Generation Power Electronics",
            tags="Hardware, Electric Vehicles, Electronics", status="Active",
        )
        embedded = (
            '&quot;jobPostings&quot;:[{&quot;id&quot;:95359,&quot;title&quot;:&quot;Electrical Engineer&quot;,'
            '&quot;url&quot;:&quot;/companies/steinmetz/jobs/h4Qjisc-electrical-engineer&quot;,'
            '&quot;location&quot;:&quot;San Francisco, CA, US&quot;,&quot;roleSpecificType&quot;:&quot;Electrical&quot;,'
            '&quot;salaryRange&quot;:&quot;$100K - $175K&quot;,&quot;minExperience&quot;:&quot;Any (new grads ok)&quot;,'
            '&quot;visa&quot;:&quot;Will sponsor&quot;,&quot;skills&quot;:[],'
            '&quot;companyName&quot;:&quot;Steinmetz&quot;,&quot;companyBatchName&quot;:&quot;W25&quot;,'
            '&quot;companyOneLiner&quot;:&quot;Next-Generation Power Electronics&quot;}],'
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = embedded.encode()
        with patch("job_agent.sources.YCCompanySource.fetch", return_value=[company]), patch(
            "job_agent.sources.urllib.request.urlopen", return_value=response,
        ):
            jobs = YCJobSource(company_scan_limit=1).search(("power electronics engineer",), limit=10)
        self.assertEqual(jobs[0].company, "Steinmetz")
        self.assertEqual(jobs[0].title, "Electrical Engineer")
        self.assertIn("Will sponsor", jobs[0].description)

    def test_web_search_output_text_extraction(self):
        body = {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": '{"jobs":[]}'},
        ]}]}
        self.assertEqual(OpenAIWebJobSource._extract_output_text(body), '{"jobs":[]}')

    def test_role_queries_are_grouped_across_all_target_lanes(self):
        queries = (
            "software engineer", "AI engineer", "applications engineer",
            "solutions engineer", "power electronics engineer", "FPGA engineer",
            "design verification engineer", "game developer",
        )
        grouped = group_queries_by_lane(queries)
        self.assertEqual(len(grouped), 8)
        self.assertEqual(classify_role_lane("ASIC RTL Design Verification Engineer"), "Chip Design & Verification")
        self.assertEqual(classify_role_lane("Analog Hardware Validation Engineer"), "Power, Board & Hardware")
        self.assertEqual(classify_role_lane("Field Applications Engineer"), "Applications Engineering")
        self.assertEqual(classify_role_lane("FPGA Design Engineer"), "FPGA Engineering")
        self.assertEqual(classify_role_lane("Unity Gameplay Engineer"), "Game Development")
        self.assertEqual(classify_role_lane("Associate Software Engineer"), "Software Engineering")
        self.assertFalse(matches_role_query("Online Marketing Manager - Paid Social", "SoC design engineer"))
        self.assertEqual(classify_job_region("Bengaluru, India"), "India")
        self.assertEqual(classify_job_region("Remote - Canada"), "Canada")

    def test_server_rejects_public_bind_addresses(self):
        require_local_host("127.0.0.1")
        with self.assertRaisesRegex(ValueError, "local-only"):
            require_local_host("0.0.0.0")

    def test_profile_bootstraps_from_candidate_context(self):
        context_path = Path(self.temp.name) / "candidate.json"
        context_path.write_text(
            '{"identity":{"resume_name":"Tushar","email":"t@example.com"},'
            '"target_lanes":["Software engineering","AI engineering"]}'
        )
        database_path = Path(self.temp.name) / "bootstrap.db"
        service = JobAgentService(
            replace(settings(database_path), candidate_context_path=context_path),
            Database(database_path),
        )
        profile = service.get_profile()
        self.assertEqual(profile["full_name"], "Tushar")
        self.assertIn("Software engineering", profile["target_roles"])
        self.assertIn("work_authorization", profile)

    def test_reusable_application_answers_are_stored_as_structured_data(self):
        profile = self.service.save_profile({
            "full_name": "A Candidate",
            "base_resume": "Built reliable APIs.",
            "legal_name": "A Legal Candidate",
            "authorized_india": "Yes",
            "demographic_response_policy": "Prefer not to answer",
        })
        self.assertEqual(profile["application_answers"]["legal_name"], "A Legal Candidate")
        self.assertEqual(profile["application_answers"]["authorized_india"], "Yes")
        raw = self.service.db.one("SELECT application_answers FROM profile WHERE id=1")
        self.assertIn("demographic_response_policy", raw["application_answers"])

    def test_application_artifacts_are_written(self):
        resume_data = {
            "headline": "Software and AI Engineer",
            "education": [{"institution": "NYU", "location": "New York", "degree": "M.S. Computer Engineering", "dates": "2024-2025"}],
            "experience": [{"organization": "Dreamline AI", "location": "Remote", "role": "Software Engineer", "dates": "2025-2026", "bullets": ["Built REST APIs."]}],
            "projects": [{"name": "UVMForge", "url": "https://example.com", "technologies": "Python", "bullets": ["Generated verification environments."]}],
            "skills": [{"category": "Programming", "items": "Python, C++"}],
        }
        package = TailoredPackage(80, "Fit", [], "\\documentclass{article}", "Letter", [], resume_data)
        artifacts = write_package_artifacts(
            Path(self.temp.name) / "artifacts", 42, package,
            {"identity": {"resume_name": "Tushar", "email": "t@example.com"}},
        )
        self.assertTrue(Path(artifacts.resume_path).is_file())
        self.assertTrue(Path(artifacts.cover_letter_path).is_file())
        if artifacts.resume_pdf_path:
            self.assertTrue(Path(artifacts.resume_pdf_path).read_bytes().startswith(b"%PDF"))
            from pypdf import PdfReader

            reader = PdfReader(artifacts.resume_pdf_path)
            extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertNotIn("|", extracted)


    def test_normalize_dedupe_key_ignores_punctuation_and_case(self):
        self.assertEqual(
            normalize_dedupe_key("Backend Engineer!", "Acme, Inc."),
            normalize_dedupe_key("backend engineer", "acme inc"),
        )

    def test_prescreen_score_favors_keyword_overlap(self):
        context = {
            "skill_groups": {"programming_and_scripting": ["Python", "FastAPI"]},
            "target_lanes": ["Backend engineering"],
        }
        strong = JobListing(
            "remotive", "1", "Backend Engineer", "Acme", "Remote",
            "https://e.test/1", "Build APIs with Python and FastAPI for backend engineering roles.",
        )
        weak = JobListing(
            "remotive", "2", "Marketing Manager", "Acme", "Remote",
            "https://e.test/2", "Run paid social campaigns.",
        )
        self.assertGreater(prescreen_score(strong, context), prescreen_score(weak, context))

    def test_sync_jobs_collapses_cross_source_duplicates(self):
        remotive_job = JobListing("remotive", "10", "Backend Engineer", "Acme Inc", "Remote", "https://e.test/r", "Build APIs")
        arbeitnow_job = JobListing("arbeitnow", "acme-backend", "Backend Engineer", "Acme Inc.", "Remote", "https://e.test/a", "Build APIs")
        with patch("job_agent.service.RemotiveSource.search", return_value=[remotive_job]), patch(
            "job_agent.service.ArbeitnowSource.search", return_value=[arbeitnow_job]
        ):
            result = self.service.sync_jobs()
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["duplicates_collapsed"], 1)
        self.assertEqual(result["inserted"], 1)

    def test_sync_jobs_ranks_queue_by_prescreen_score(self):
        weak = JobListing("remotive", "20", "Marketing Manager", "Acme", "Remote", "https://e.test/w", "Run paid social campaigns.")
        strong = JobListing("remotive", "21", "Backend Engineer", "Acme", "Remote", "https://e.test/s", "Build backend APIs.")
        with patch("job_agent.service.RemotiveSource.search", return_value=[weak, strong]), patch(
            "job_agent.service.ArbeitnowSource.search", return_value=[]
        ):
            self.service.sync_jobs()
        rows = self.service.db.all("SELECT title, prescreen_score FROM jobs WHERE source_id IN ('20','21') ORDER BY prescreen_score DESC")
        self.assertEqual(rows[0]["title"], "Backend Engineer")

    def test_yc_company_source_parses_and_filters_by_batch_and_status(self):
        payload = [
            {"slug": "acme", "name": "Acme", "website": "https://acme.com", "batch": "W20", "status": "Active", "one_liner": "Widgets", "tags": ["B2B"]},
            {"slug": "snapmagic", "name": "SnapMagic", "website": "https://snapmagic.com", "batch": "S15", "status": "Active", "one_liner": "AI-assisted electronics design", "tags": ["Hardware"]},
            {"slug": "old-co", "name": "OldCo", "website": "https://old.co", "batch": "W15", "status": "Active", "one_liner": "Old", "tags": []},
            {"slug": "dead-co", "name": "DeadCo", "website": "https://dead.co", "batch": "S21", "status": "Dead", "one_liner": "Dead", "tags": []},
        ]
        companies = YCCompanySource.parse(payload, min_batch_year=2020)
        self.assertEqual([company.slug for company in companies], ["acme"])
        self.assertEqual(companies[0].domain, "acme.com")
        older = YCCompanySource.parse(payload, min_batch_year=2005)
        self.assertIn("snapmagic", [company.slug for company in older])

    def test_guess_contact_emails_from_domain(self):
        self.assertEqual(
            guess_contact_emails("acme.com"),
            [f"{alias}@acme.com" for alias in ("founders", "careers", "jobs", "talent", "hi", "hello", "contact", "team")],
        )
        self.assertEqual(guess_contact_emails(""), [])

    def test_extract_public_emails_prefers_company_domain(self):
        emails = extract_public_emails(
            "Contact founders@deepsim.io or someone@gmail.com", "deepsim.io"
        )
        self.assertEqual(emails, ["founders@deepsim.io"])

    def test_yc_company_scoring_favors_profile_aligned_hardware_companies(self):
        diode = YCCompany(
            "diode-computers-inc", "Diode Computers, Inc.", "diode.computer", "S24",
            "Automate circuit board design using AI", "Hardware, Manufacturing, Electronics", "Active",
        )
        generic = YCCompany("generic", "Generic", "generic.test", "S24", "Social media analytics", "Marketing", "Active")
        diode_score, reasons = score_yc_company(diode, {"target_lanes": ["PCB design engineering"]})
        generic_score, _ = score_yc_company(generic, {"target_lanes": ["PCB design engineering"]})
        self.assertGreater(diode_score, generic_score)
        self.assertIn("circuit boards / PCB", reasons)

    def test_startup_scoring_includes_game_development(self):
        game_company = StartupCompany(
            "portfolio", "game-1", "GameCo", "gameco.test",
            description="Unity gameplay tools for camera-driven fitness games",
            tags="Gaming, Sports Tech, Unity",
        )
        score, reasons = score_startup_company(game_company, {"target_lanes": ["Game development"]})
        self.assertGreaterEqual(score, 15)
        self.assertIn("gaming", reasons)

    def test_sec_form_d_parsers(self):
        master = "CIK|Company Name|Form Type|Date Filed|Filename\n123|Acme Robotics Inc.|D|2026-07-01|edgar/data/123/abc.txt\n"
        rows = SECFormDSource.parse_master_index(master)
        self.assertEqual(rows[0]["form"], "D")
        xml = """
        <primaryIssuer><entityName>Acme Robotics Inc.</entityName>
        <issuerAddress><stateOrCountryDescription>CA</stateOrCountryDescription><country>US</country></issuerAddress></primaryIssuer>
        <industryGroup><industryGroupType>Technology</industryGroupType></industryGroup>
        <typeOfSecurityOffered>Equity</typeOfSecurityOffered>
        <totalOfferingAmount>2500000</totalOfferingAmount>
        """
        details = SECFormDSource.parse_form_d_text(xml)
        self.assertEqual(details["name"], "Acme Robotics Inc.")
        self.assertEqual(details["amount"], "$2,500,000")

    def test_startup_sync_imports_companies_and_contacts(self):
        company = StartupCompany(
            "test_source", "1", "Unity Fitness Games", "unityfitness.test",
            country="US", funding_signal="Seed funded", description="Unity game studio for fitness",
            tags="Gaming, Fitness, Unity", fit_score=40, fit_reasons=("gaming", "fitness technology"),
        )
        self.service.sync_startups = lambda: None
        inserted = self.service._add_startup_company(company)
        contacts = self.service._add_startup_contacts(
            company.source, company.source_id, discover_contacts(company, enrich=False)
        )
        self.assertEqual(inserted, 1)
        self.assertGreater(contacts, 0)
        row = self.service.db.one("SELECT * FROM startup_companies WHERE source='test_source'")
        self.assertEqual(row["fit_score"], 40)

    def test_outreach_dry_run_writes_preview_without_smtp(self):
        dry_run_settings = replace(settings(Path(self.temp.name) / "outreach.db"), artifact_dir=Path(self.temp.name) / "artifacts")
        with patch("job_agent.outreach.smtplib.SMTP") as smtp_mock:
            result = send_outreach(1, "careers@acme.com", "Subject", "Body", "", dry_run_settings, dry_run=True)
        smtp_mock.assert_not_called()
        self.assertEqual(result.status, "dry_run")
        self.assertTrue(Path(result.preview_path).is_file())

    def test_adapter_registry_detects_known_ats_urls(self):
        self.assertEqual(find_adapter("https://boards.greenhouse.io/acme/jobs/123").name, "greenhouse")
        self.assertEqual(find_adapter("https://jobs.lever.co/acme/abc").name, "lever")
        self.assertIsNone(find_adapter("https://example.com/careers/123"))

    def test_map_known_field_matches_known_labels_only(self):
        profile = {"application_answers": {"authorized_us": "Yes, citizen"}}
        self.assertEqual(map_known_field("Are you authorized to work in the United States?", profile), "Yes, citizen")
        self.assertIsNone(map_known_field("What is your favorite color?", profile))
        self.assertTrue(is_sensitive_question("Please enter your Social Security Number"))
        with self.assertRaises(UnmappedQuestionError):
            mapped_or_raise("Date of birth", profile, "Greenhouse", required=False)
        with self.assertRaises(UnmappedQuestionError):
            mapped_or_raise("What is your favorite color?", profile, "Lever", required=True)

    def test_new_automation_flags_default_off(self):
        default_settings = settings(Path(self.temp.name) / "flags.db")
        self.assertFalse(default_settings.auto_submit)
        self.assertFalse(default_settings.auto_outreach)
        self.assertFalse(default_settings.enable_live_outreach)
        self.assertFalse(default_settings.enable_live_greenhouse)
        self.assertFalse(default_settings.enable_live_lever)

    def test_run_automatic_cycle_skips_new_steps_when_disabled(self):
        self.service.sync_jobs = lambda: {"fetched": 0, "inserted": 0}
        self.service.run_auto_submit_cycle = lambda: (_ for _ in ()).throw(AssertionError("should not run"))
        self.service.run_outreach_cycle = lambda: (_ for _ in ()).throw(AssertionError("should not run"))
        result = self.service.run_automatic_cycle()
        self.assertNotIn("auto_submit", result)
        self.assertNotIn("auto_outreach", result)

    def test_run_automatic_cycle_runs_new_steps_when_enabled(self):
        self.service.sync_jobs = lambda: {"fetched": 0, "inserted": 0}
        self.service.settings = replace(self.service.settings, auto_submit=True, auto_outreach=True, auto_tailor=False)
        self.service.run_auto_submit_cycle = lambda: {"submitted": []}
        self.service.run_outreach_cycle = lambda: {"sent": []}
        result = self.service.run_automatic_cycle()
        self.assertEqual(result["auto_submit"], {"submitted": []})
        self.assertEqual(result["auto_outreach"], {"sent": []})

    def test_auto_submit_cycle_flags_unmapped_question_as_needs_review(self):
        self.service.settings = replace(self.service.settings, auto_submit_min_fit_score=50)
        self.service.db.execute("UPDATE jobs SET url=? WHERE id=1", ("https://boards.greenhouse.io/acme/jobs/1",))
        self.service.db.execute("UPDATE packages SET fit_score=90 WHERE id=?", (self.package_id,))

        class FailingAdapter:
            name = "greenhouse"

            def submit(self, *args, **kwargs):
                raise UnmappedQuestionError("Unmapped required question: Do you have a driver's license?")

        with patch("job_agent.service.find_adapter", return_value=FailingAdapter()):
            result = self.service.run_auto_submit_cycle()
        self.assertEqual(len(result["needs_review"]), 1)
        job = self.service.db.one("SELECT status FROM jobs WHERE id=1")
        self.assertEqual(job["status"], "needs_manual_review")

    def test_auto_submit_cycle_dry_run_does_not_mark_submitted(self):
        self.service.settings = replace(self.service.settings, auto_submit_min_fit_score=50)
        self.service.db.execute("UPDATE jobs SET url=? WHERE id=1", ("https://boards.greenhouse.io/acme/jobs/1",))
        self.service.db.execute("UPDATE packages SET fit_score=90 WHERE id=?", (self.package_id,))

        class DryAdapter:
            name = "greenhouse"

            def submit(self, *args, **kwargs):
                return SubmissionResult(status="dry_run", log_path="/tmp/fields.json")

        with patch("job_agent.service.find_adapter", return_value=DryAdapter()):
            result = self.service.run_auto_submit_cycle()
        self.assertEqual(len(result["dry_run"]), 1)
        package = self.service.get_package(self.package_id)
        self.assertEqual(package["decision"], "approved")
        self.assertIsNone(package["submitted_at"])

    def test_build_mx_query_encodes_domain_labels(self):
        query = build_mx_query("acme.com", transaction_id=1234)
        self.assertEqual(query[:2], (1234).to_bytes(2, "big"))
        self.assertIn(b"\x04acme\x03com\x00", query)
        self.assertTrue(query.endswith(b"\x00\x0f\x00\x01"))  # QTYPE=MX(15), QCLASS=IN(1)

    def test_parse_answer_count_reads_header_ancount(self):
        header = bytes(6) + (3).to_bytes(2, "big")
        self.assertEqual(parse_answer_count(header), 3)
        self.assertEqual(parse_answer_count(b""), 0)

    def test_has_mx_record_returns_false_for_empty_domain(self):
        self.assertFalse(has_mx_record(""))

    def test_has_mx_record_parses_socket_response(self):
        fake_response = bytes(6) + (1).to_bytes(2, "big")
        with patch("job_agent.yc_source.socket.socket") as socket_cls:
            sock = MagicMock()
            sock.recvfrom.return_value = (fake_response, ("1.1.1.1", 53))
            socket_cls.return_value.__enter__.return_value = sock
            self.assertTrue(has_mx_record("acme.com"))

    def test_sync_yc_companies_scores_and_prefers_public_page_contacts(self):
        diode = YCCompany(
            "diode-computers-inc", "Diode Computers, Inc.", "diode.computer", "S24",
            "Automate circuit board design using AI", "Hardware, Manufacturing, Electronics", "Active",
        )
        with patch("job_agent.service.YCCompanySource.fetch", return_value=[diode]), patch(
            "job_agent.service.JobAgentService._discover_yc_page_emails",
            return_value=["founders@diode.computer"],
        ):
            result = self.service.sync_yc_companies()
        self.assertEqual(result["fetched"], 1)
        company = self.service.db.one("SELECT * FROM yc_companies WHERE slug='diode-computers-inc'")
        self.assertGreaterEqual(company["fit_score"], self.service.settings.yc_outreach_min_fit_score)
        contact = self.service.db.one(
            """SELECT * FROM yc_contacts WHERE company_id=? AND status='pending'
               ORDER BY CASE WHEN alias_type='public_page' THEN 0 ELSE 1 END, id LIMIT 1""",
            (company["id"],),
        )
        self.assertEqual(contact["email"], "founders@diode.computer")
        self.assertEqual(contact["alias_type"], "public_page")

    def test_outreach_cycle_drafts_highest_fit_companies_first(self):
        low = YCCompany("low", "Low", "low.test", "S24", "Marketing analytics", "Marketing", "Active")
        high = YCCompany(
            "high", "High", "high.test", "S24",
            "Automate circuit board design using AI", "Hardware, Electronics", "Active",
        )
        self.service.settings = replace(
            self.service.settings,
            openai_api_key="test-key",
            max_outreach_per_cycle=1,
            yc_outreach_min_fit_score=10,
        )
        with patch("job_agent.service.YCCompanySource.fetch", return_value=[low, high]), patch(
            "job_agent.service.JobAgentService._discover_yc_page_emails", return_value=[],
        ), patch("job_agent.service.has_mx_record", return_value=True), patch(
            "job_agent.service.OutreachComposer.create",
            return_value=MagicMock(subject="Subject", body="Body", resume_data={}),
        ):
            result = self.service.run_outreach_cycle()
        self.assertEqual(len(result["drafted"]), 1)
        outreach = self.service.get_outreach(result["drafted"][0]["outreach_id"])
        self.assertEqual(outreach["company_name"], "High")

    def test_generate_outreach_skips_company_with_no_mx_record(self):
        self.service.db.execute(
            "INSERT INTO yc_companies (slug, name, domain, batch, status) VALUES (?, ?, ?, ?, ?)",
            ("acme", "Acme", "dead-domain.test", "W20", "Active"),
        )
        company = self.service.db.one("SELECT * FROM yc_companies WHERE slug='acme'")
        self.service.db.execute(
            "INSERT INTO yc_contacts (company_id, email, alias_type) VALUES (?, ?, ?)",
            (company["id"], "careers@dead-domain.test", "careers"),
        )
        with patch("job_agent.service.has_mx_record", return_value=False):
            with self.assertRaises(ValueError):
                self.service.generate_outreach(company["id"])
        contact = self.service.db.one("SELECT * FROM yc_contacts WHERE company_id=?", (company["id"],))
        self.assertEqual(contact["status"], "bounced")

    def test_send_outreach_bounce_requeues_next_pending_contact(self):
        self.service.db.execute(
            "INSERT INTO yc_companies (slug, name, domain, batch, status) VALUES (?, ?, ?, ?, ?)",
            ("acme2", "Acme2", "acme2.test", "W20", "Active"),
        )
        company = self.service.db.one("SELECT * FROM yc_companies WHERE slug='acme2'")
        self.service.db.execute(
            "INSERT INTO yc_contacts (company_id, email, alias_type) VALUES (?, ?, ?)",
            (company["id"], "careers@acme2.test", "careers"),
        )
        self.service.db.execute(
            "INSERT INTO yc_contacts (company_id, email, alias_type) VALUES (?, ?, ?)",
            (company["id"], "jobs@acme2.test", "jobs"),
        )
        self.service.db.execute(
            """INSERT INTO outreach_packages (company_id, contact_email, subject, body, resume_data)
               VALUES (?, ?, ?, ?, ?)""",
            (company["id"], "careers@acme2.test", "Subject", "Body", "{}"),
        )
        outreach = self.service.db.one("SELECT * FROM outreach_packages WHERE company_id=?", (company["id"],))
        with patch(
            "job_agent.service.send_outreach_email",
            side_effect=OutreachBounceError("careers@acme2.test rejected"),
        ):
            result = self.service.send_outreach(outreach["id"])
        self.assertEqual(result["decision"], "drafted")
        self.assertEqual(result["contact_email"], "jobs@acme2.test")
        careers_contact = self.service.db.one(
            "SELECT * FROM yc_contacts WHERE company_id=? AND email='careers@acme2.test'", (company["id"],)
        )
        self.assertEqual(careers_contact["status"], "bounced")

    def test_send_outreach_bounce_exhausts_when_no_pending_contacts_left(self):
        self.service.db.execute(
            "INSERT INTO yc_companies (slug, name, domain, batch, status) VALUES (?, ?, ?, ?, ?)",
            ("acme3", "Acme3", "acme3.test", "W20", "Active"),
        )
        company = self.service.db.one("SELECT * FROM yc_companies WHERE slug='acme3'")
        self.service.db.execute(
            "INSERT INTO yc_contacts (company_id, email, alias_type) VALUES (?, ?, ?)",
            (company["id"], "careers@acme3.test", "careers"),
        )
        self.service.db.execute(
            """INSERT INTO outreach_packages (company_id, contact_email, subject, body, resume_data)
               VALUES (?, ?, ?, ?, ?)""",
            (company["id"], "careers@acme3.test", "Subject", "Body", "{}"),
        )
        outreach = self.service.db.one("SELECT * FROM outreach_packages WHERE company_id=?", (company["id"],))
        with patch(
            "job_agent.service.send_outreach_email",
            side_effect=OutreachBounceError("careers@acme3.test rejected"),
        ):
            result = self.service.send_outreach(outreach["id"])
        self.assertEqual(result["decision"], "exhausted")


if __name__ == "__main__":
    unittest.main()
