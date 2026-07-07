from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from job_agent.config import Settings
from job_agent.db import Database
from job_agent.service import JobAgentService


SECRET_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "APP_PASSWORD",
    "APP_USERNAME",
    "DATABASE_PATH",
    "ARTIFACT_DIR",
    "AUTO_TAILOR",
    "AUTO_SUBMIT",
    "AUTO_OUTREACH",
    "ENABLE_LIVE_OUTREACH",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM_EMAIL",
)


def load_streamlit_secrets_into_env() -> None:
    for key in SECRET_KEYS:
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None
        if value is not None and str(value).strip():
            os.environ[key] = str(value)

    os.environ.setdefault("HOSTED_MODE", "true")
    os.environ.setdefault("DATABASE_PATH", "data/job_agent.db")
    os.environ.setdefault("ARTIFACT_DIR", "data/packages")
    os.environ.setdefault("AUTO_SUBMIT", "false")
    os.environ.setdefault("AUTO_OUTREACH", "false")
    os.environ.setdefault("ENABLE_LIVE_OUTREACH", "false")


def require_password() -> None:
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        st.error("APP_PASSWORD is required. Add it in Streamlit Cloud secrets before using this app.")
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.title("Personal job agent")
    password = st.text_input("App password", type="password")
    if st.button("Unlock"):
        if hmac.compare_digest(password, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Wrong password")
    st.stop()


@st.cache_resource
def get_service() -> JobAgentService:
    settings = Settings.from_env()
    Path(settings.artifact_dir).mkdir(parents=True, exist_ok=True)
    return JobAgentService(settings, Database(settings.database_path))


def refresh() -> None:
    st.cache_resource.clear()
    st.rerun()


def show_json_result(label: str, result: dict[str, Any]) -> None:
    st.success(label)
    st.json(result)


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def download_artifact(service: JobAgentService, package_id: int, kind: str, label: str) -> None:
    artifact = service.get_artifact(package_id, kind)
    if not artifact:
        return
    path, content_type = artifact
    st.download_button(label, path.read_bytes(), file_name=kind, mime=content_type, key=f"{kind}-{package_id}")


def profile_tab(service: JobAgentService) -> None:
    profile = service.get_profile()
    summary = service.get_context_summary()
    st.subheader("Profile context")
    cols = st.columns(4)
    cols[0].metric("Projects", summary.get("project_count", 0))
    cols[1].metric("Experience entries", summary.get("experience_count", 0))
    cols[2].metric("Target lanes", len(summary.get("target_lanes", [])))
    cols[3].metric("Search regions", len(summary.get("search_regions", [])))

    with st.expander("Target lanes and search regions", expanded=True):
        st.write(", ".join(summary.get("target_lanes", [])))
        st.caption("Regions: " + ", ".join(summary.get("search_regions", [])))

    with st.form("profile-form"):
        st.text_input("Full name", value=profile.get("full_name", ""), key="full_name")
        st.text_input("Email", value=profile.get("email", ""), key="email")
        st.text_input("Phone", value=profile.get("phone", ""), key="phone")
        st.text_input("Location", value=profile.get("location", ""), key="location")
        st.text_input("LinkedIn", value=profile.get("linkedin_url", ""), key="linkedin_url")
        st.text_input("Portfolio", value=profile.get("portfolio_url", ""), key="portfolio_url")
        st.text_area("Target roles", value=profile.get("target_roles", ""), key="target_roles", height=90)
        st.text_area("Preferences", value=profile.get("preferences", ""), key="preferences", height=90)
        st.text_area("Work authorization", value=profile.get("work_authorization", ""), key="work_authorization", height=80)
        st.text_area("Sponsorship required", value=profile.get("sponsorship_required", ""), key="sponsorship_required", height=70)
        st.text_area("Location preferences", value=profile.get("location_preferences", ""), key="location_preferences", height=70)
        st.text_area("Application notes", value=profile.get("application_notes", ""), key="application_notes", height=90)
        st.text_area("Base resume override", value=profile.get("base_resume", ""), key="base_resume", height=180)
        if st.form_submit_button("Save profile"):
            saved = service.save_profile({key: st.session_state.get(key, "") for key in (
                "full_name", "email", "phone", "location", "linkedin_url", "portfolio_url",
                "target_roles", "preferences", "work_authorization", "sponsorship_required",
                "location_preferences", "application_notes", "base_resume",
            )})
            st.success("Profile saved")
            st.json({"full_name": saved.get("full_name"), "target_roles": saved.get("target_roles")})


def jobs_tab(service: JobAgentService) -> None:
    st.subheader("Job discovery and tailoring")
    col1, col2 = st.columns([1, 2])
    if col1.button("Sync jobs"):
        with st.spinner("Syncing jobs across configured lanes..."):
            show_json_result("Job sync complete", service.sync_jobs())
    if col2.button("Refresh"):
        refresh()

    jobs = service.list_jobs()
    st.caption(f"{len(jobs)} jobs in database")
    for job in jobs[:150]:
        with st.expander(
            f"{job['title']} — {job['company']} · {job.get('location', '')} · {job.get('role_lane', '')}",
            expanded=False,
        ):
            st.write(job.get("url", ""))
            st.caption(f"Source: {job.get('source')} · Prescreen: {job.get('prescreen_score', 0)} · Status: {job.get('status')}")
            st.write((job.get("description") or "")[:2500])
            if job.get("package_id"):
                package = service.get_package(int(job["package_id"]))
                if package:
                    st.metric("Fit score", package.get("fit_score", 0))
                    st.write(package.get("fit_summary", ""))
                    missing = parse_json_list(package.get("missing_requirements"))
                    if missing:
                        st.warning("Missing/weak requirements: " + "; ".join(str(item) for item in missing))
                    st.text_area("Cover letter", value=package.get("cover_letter", ""), height=180, key=f"cover-{package['id']}")
                    download_artifact(service, int(package["id"]), "resume.pdf", "Download resume PDF")
                    download_artifact(service, int(package["id"]), "resume.tex", "Download resume LaTeX")
                    download_artifact(service, int(package["id"]), "cover-letter.txt", "Download cover letter")
                    decision_col1, decision_col2 = st.columns(2)
                    if decision_col1.button("Approve", key=f"approve-{package['id']}"):
                        decided = service.decide(int(package["id"]), "approved")
                        st.success("Approved. Keep the approval token private.")
                        st.code(decided.get("approval_token", ""))
                    if decision_col2.button("Reject", key=f"reject-{package['id']}"):
                        service.decide(int(package["id"]), "rejected")
                        st.warning("Rejected")
            else:
                if st.button("Tailor resume for this job", key=f"tailor-{job['id']}"):
                    with st.spinner("Tailoring with OpenAI..."):
                        try:
                            package = service.tailor_job(int(job["id"]))
                            st.success(f"Tailored package created with fit score {package.get('fit_score')}")
                            st.rerun()
                        except Exception as error:
                            st.error(str(error))


def outreach_tab(service: JobAgentService) -> None:
    st.subheader("Company-first outreach")
    yc_col, startup_col = st.columns(2)
    if yc_col.button("Sync YC companies"):
        with st.spinner("Syncing YC directory..."):
            show_json_result("YC sync complete", service.sync_yc_companies())
    if startup_col.button("Sync funded startups"):
        with st.spinner("Syncing funded startup sources..."):
            show_json_result("Startup sync complete", service.sync_startups())

    st.markdown("#### Funded startups")
    startups = service.list_startups()
    for company in startups[:100]:
        reasons = parse_json_list(company.get("fit_reasons"))
        with st.expander(f"{company['name']} · score {company.get('fit_score', 0)} · {company.get('domain', '')}"):
            st.write(company.get("description") or company.get("funding_signal") or "")
            st.caption("Reasons: " + "; ".join(str(reason) for reason in reasons))
            st.caption(f"Pending contacts: {company.get('pending_contacts', 0)} · Outreach: {company.get('outreach_decision') or 'not drafted'}")
            if st.button("Draft startup outreach", key=f"startup-outreach-{company['id']}"):
                with st.spinner("Drafting outreach..."):
                    try:
                        st.json(service.generate_startup_outreach(int(company["id"])))
                    except Exception as error:
                        st.error(str(error))

    st.markdown("#### YC companies")
    yc_companies = service.list_yc_companies()
    for company in yc_companies[:100]:
        reasons = parse_json_list(company.get("fit_reasons"))
        with st.expander(f"{company['name']} · score {company.get('fit_score', 0)} · {company.get('domain', '')}"):
            st.write(company.get("one_liner", ""))
            st.caption(company.get("tags", ""))
            st.caption("Reasons: " + "; ".join(str(reason) for reason in reasons))
            st.caption(f"Pending contacts: {company.get('pending_contacts', 0)} · Outreach: {company.get('outreach_decision') or 'not drafted'}")
            if st.button("Draft YC outreach", key=f"yc-outreach-{company['id']}"):
                with st.spinner("Drafting outreach..."):
                    try:
                        st.json(service.generate_outreach(int(company["id"])))
                    except Exception as error:
                        st.error(str(error))

    st.markdown("#### Drafted YC outreach")
    for outreach in service.list_outreach()[:50]:
        with st.expander(f"{outreach['company_name']} → {outreach['contact_email']} · {outreach['decision']}"):
            st.write(outreach.get("subject", ""))
            if st.button("Dry-run/send through configured SMTP gate", key=f"send-outreach-{outreach['id']}"):
                try:
                    st.json(service.send_outreach(int(outreach["id"])))
                except Exception as error:
                    st.error(str(error))


def automation_tab(service: JobAgentService) -> None:
    st.subheader("Automation log")
    for event in service.get_automation_log(100):
        st.code(json.dumps(event, indent=2), language="json")


def main() -> None:
    st.set_page_config(page_title="Personal Job Agent", layout="wide")
    load_streamlit_secrets_into_env()
    require_password()
    service = get_service()

    st.title("Personal job application agent")
    st.caption("Private single-user workflow for job discovery, resume tailoring, approval, and outreach drafts.")
    tab_profile, tab_jobs, tab_outreach, tab_log = st.tabs(["Profile", "Jobs", "Outreach", "Automation log"])
    with tab_profile:
        profile_tab(service)
    with tab_jobs:
        jobs_tab(service)
    with tab_outreach:
        outreach_tab(service)
    with tab_log:
        automation_tab(service)


if __name__ == "__main__":
    main()
