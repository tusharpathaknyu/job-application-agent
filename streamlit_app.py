from __future__ import annotations

import hmac
import html
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


def inject_design() -> None:
    st.markdown(
        """
        <style>
        :root {
          --bg:#090d13;
          --panel:#111923;
          --panel-soft:#162231;
          --ink:#eef5ff;
          --muted:#9baabd;
          --line:rgba(255,255,255,.10);
          --accent:#7c5cff;
          --accent-2:#00d4a6;
          --warn:#ffb86b;
          --danger:#ff6b7a;
          --good:#42d392;
          --shadow:0 22px 70px rgba(0,0,0,.35);
        }
        .stApp {
          background:
            radial-gradient(circle at 12% 10%, rgba(124,92,255,.26), transparent 30%),
            radial-gradient(circle at 84% 4%, rgba(0,212,166,.18), transparent 28%),
            linear-gradient(135deg, #080b10 0%, #0b111a 45%, #111827 100%);
          color:var(--ink);
        }
        header[data-testid="stHeader"] { background:transparent; }
        [data-testid="stSidebar"] {
          background:rgba(10,15,23,.74);
          border-right:1px solid var(--line);
          backdrop-filter: blur(18px);
        }
        [data-testid="stSidebar"] * { color:var(--ink); }
        .block-container { max-width:1280px; padding-top:1.6rem; padding-bottom:4rem; }
        h1,h2,h3 { letter-spacing:-.035em; }
        p, label, span, div { color:inherit; }
        .hero {
          position:relative;
          overflow:hidden;
          border:1px solid var(--line);
          border-radius:28px;
          padding:34px 34px 28px;
          background:
            linear-gradient(135deg, rgba(255,255,255,.12), rgba(255,255,255,.04)),
            radial-gradient(circle at 78% 22%, rgba(0,212,166,.18), transparent 34%);
          box-shadow:var(--shadow);
        }
        .hero:after {
          content:"";
          position:absolute;
          inset:auto -120px -160px auto;
          width:360px;
          height:360px;
          background:radial-gradient(circle, rgba(124,92,255,.35), transparent 65%);
          pointer-events:none;
        }
        .eyebrow {
          display:inline-flex;
          align-items:center;
          gap:8px;
          color:#b7a8ff;
          font-size:12px;
          font-weight:800;
          letter-spacing:.16em;
          text-transform:uppercase;
        }
        .hero h1 {
          max-width:820px;
          margin:12px 0 10px;
          font-size:clamp(42px, 6vw, 76px);
          line-height:.93;
          color:#fff;
        }
        .hero p {
          max-width:760px;
          margin:0;
          color:var(--muted);
          font-size:18px;
          line-height:1.58;
        }
        .hero-grid {
          display:grid;
          grid-template-columns:repeat(4,minmax(0,1fr));
          gap:12px;
          margin-top:26px;
        }
        .metric-card, .glass-card {
          border:1px solid var(--line);
          border-radius:20px;
          background:rgba(17,25,35,.72);
          box-shadow:0 16px 42px rgba(0,0,0,.20);
        }
        .metric-card { padding:18px; }
        .metric-card .label { color:var(--muted); font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.08em; }
        .metric-card .value { margin-top:8px; color:#fff; font-size:30px; font-weight:850; letter-spacing:-.04em; }
        .metric-card .hint { margin-top:4px; color:#6f8197; font-size:12px; }
        .section-card {
          padding:22px;
          border:1px solid var(--line);
          border-radius:24px;
          background:rgba(14,21,31,.76);
          box-shadow:0 20px 54px rgba(0,0,0,.20);
          margin:16px 0;
        }
        .section-card h3 { margin:0 0 6px; color:#fff; }
        .section-card p { margin:0; color:var(--muted); }
        .pill-row { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
        .pill {
          display:inline-flex;
          align-items:center;
          border:1px solid var(--line);
          border-radius:999px;
          padding:5px 10px;
          background:rgba(255,255,255,.055);
          color:#cfd8e6;
          font-size:12px;
          font-weight:700;
        }
        .pill.good { color:#7df0b2; border-color:rgba(66,211,146,.35); background:rgba(66,211,146,.10); }
        .pill.warn { color:#ffd19c; border-color:rgba(255,184,107,.38); background:rgba(255,184,107,.10); }
        .pill.bad { color:#ff9aa5; border-color:rgba(255,107,122,.38); background:rgba(255,107,122,.10); }
        .pill.purple { color:#cabfff; border-color:rgba(124,92,255,.45); background:rgba(124,92,255,.13); }
        .job-card {
          border:1px solid var(--line);
          border-radius:22px;
          padding:18px 20px;
          background:linear-gradient(135deg, rgba(255,255,255,.075), rgba(255,255,255,.035));
          box-shadow:0 16px 38px rgba(0,0,0,.18);
          margin:12px 0 10px;
        }
        .job-title { color:#fff; font-size:20px; font-weight:850; letter-spacing:-.03em; margin-bottom:4px; }
        .job-meta { color:var(--muted); font-size:13px; line-height:1.5; }
        .company-name { color:#dce6f5; font-weight:750; }
        .score-badge {
          display:inline-grid;
          place-items:center;
          min-width:54px;
          height:42px;
          border-radius:15px;
          font-size:18px;
          font-weight:900;
          color:#07110d;
          background:linear-gradient(135deg, #42d392, #00d4a6);
        }
        .score-badge.mid { background:linear-gradient(135deg, #ffdd8a, #ffb86b); }
        .score-badge.low { background:linear-gradient(135deg, #ff8fa0, #ff6b7a); color:#24070b; }
        .mini-label { color:#71839a; font-size:11px; text-transform:uppercase; letter-spacing:.12em; font-weight:850; }
        .result-box {
          border:1px solid rgba(66,211,146,.26);
          background:rgba(66,211,146,.08);
          border-radius:18px;
          padding:14px 16px;
          margin:14px 0;
        }
        .result-box strong { color:#8af4bd; }
        .empty {
          border:1px dashed rgba(255,255,255,.18);
          border-radius:22px;
          padding:28px;
          text-align:center;
          color:var(--muted);
          background:rgba(255,255,255,.035);
        }
        .stTabs [data-baseweb="tab-list"] { gap:10px; }
        .stTabs [data-baseweb="tab"] {
          height:44px;
          border:1px solid var(--line);
          border-radius:999px;
          background:rgba(255,255,255,.045);
          color:var(--muted);
          padding:0 18px;
        }
        .stTabs [aria-selected="true"] {
          color:#fff !important;
          background:linear-gradient(135deg, rgba(124,92,255,.45), rgba(0,212,166,.22)) !important;
          border-color:rgba(255,255,255,.22) !important;
        }
        div[data-testid="stExpander"] {
          border:1px solid var(--line);
          border-radius:18px;
          background:rgba(9,14,22,.42);
          overflow:hidden;
        }
        div[data-testid="stExpander"] summary { font-weight:800; color:#eaf1fb; }
        div[data-testid="stMetric"] {
          border:1px solid var(--line);
          border-radius:18px;
          padding:14px 16px;
          background:rgba(255,255,255,.045);
        }
        .stButton > button, .stDownloadButton > button, .stLinkButton > a {
          border-radius:999px !important;
          border:1px solid rgba(255,255,255,.14) !important;
          background:linear-gradient(135deg, var(--accent), #5e7cff) !important;
          color:white !important;
          font-weight:850 !important;
          box-shadow:0 12px 26px rgba(94,124,255,.18);
        }
        .stButton > button:hover, .stDownloadButton > button:hover, .stLinkButton > a:hover {
          border-color:rgba(255,255,255,.32) !important;
          transform:translateY(-1px);
        }
        input, textarea, select {
          border-radius:14px !important;
          border-color:rgba(255,255,255,.12) !important;
          background:rgba(255,255,255,.06) !important;
          color:#eef5ff !important;
        }
        textarea { min-height:88px; }
        code, pre {
          border-radius:14px !important;
          background:rgba(0,0,0,.34) !important;
          color:#dce6f5 !important;
        }
        @media (max-width: 900px) {
          .hero { padding:24px; border-radius:22px; }
          .hero-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def pill(label: Any, tone: str = "") -> str:
    return f'<span class="pill {tone}">{esc(label)}</span>'


def score_tone(score: int) -> str:
    if score >= 75:
        return ""
    if score >= 55:
        return "mid"
    return "low"


def decision_tone(decision: str) -> str:
    if decision in {"approved", "sent", "submitted", "dry_run"}:
        return "good"
    if decision in {"pending", "drafted", "review"}:
        return "warn"
    if decision in {"rejected", "exhausted", "needs_manual_review"}:
        return "bad"
    return "purple"


def metric_cards_html(items: list[tuple[str, Any, str]]) -> str:
    cards = "\n".join(
        f"""
        <div class="metric-card">
          <div class="label">{esc(label)}</div>
          <div class="value">{esc(value)}</div>
          <div class="hint">{esc(hint)}</div>
        </div>
        """
        for label, value, hint in items
    )
    return f'<div class="hero-grid">{cards}</div>'


def require_password() -> None:
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        st.markdown(
            """
            <div class="hero">
              <div class="eyebrow">Setup required</div>
              <h1>Add your app password.</h1>
              <p>Set <code>APP_PASSWORD</code> in Streamlit Cloud secrets before using the dashboard.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.markdown(
        """
        <div class="hero">
          <div class="eyebrow">Private access</div>
          <h1>Your job hunt command center.</h1>
          <p>Unlock the dashboard to search roles, tailor resumes, approve applications, and draft founder outreach.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    left, center, right = st.columns([1, 1.15, 1])
    with center:
        st.markdown(
            '<div class="section-card"><h3>Sign in</h3><p>Single-user password gate.</p></div>',
            unsafe_allow_html=True,
        )
        password = st.text_input("App password", type="password", label_visibility="collapsed", placeholder="Enter app password")
        if st.button("Unlock dashboard", use_container_width=True):
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
    st.markdown(f'<div class="result-box"><strong>{esc(label)}</strong></div>', unsafe_allow_html=True)
    with st.expander("View details", expanded=False):
        st.json(result)


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def short_text(value: str, limit: int = 360) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def download_artifact(service: JobAgentService, package_id: int, kind: str, label: str) -> None:
    artifact = service.get_artifact(package_id, kind)
    if not artifact:
        return
    path, content_type = artifact
    st.download_button(label, path.read_bytes(), file_name=kind, mime=content_type, key=f"{kind}-{package_id}")


def sidebar(service: JobAgentService) -> None:
    profile = service.get_profile()
    jobs = service.list_jobs()
    packages = [job for job in jobs if job.get("package_id")]
    approved = [job for job in jobs if job.get("decision") == "approved"]
    st.sidebar.markdown("### Application OS")
    st.sidebar.caption(profile.get("full_name") or "Private candidate")
    st.sidebar.markdown(
        f"""
        {pill(f"{len(jobs)} jobs", "purple")}
        {pill(f"{len(packages)} tailored", "good")}
        {pill(f"{len(approved)} approved", "warn")}
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.divider()
    if st.sidebar.button("Refresh data", use_container_width=True):
        refresh()
    st.sidebar.caption("Live submit/outreach stay gated by env flags. The UI can draft and dry-run safely.")


def hero(service: JobAgentService) -> None:
    summary = service.get_context_summary()
    jobs = service.list_jobs()
    outreach = service.list_outreach()
    startups = service.list_startups()
    tailored = sum(1 for job in jobs if job.get("package_id"))
    metric_grid = metric_cards_html([
        ("Jobs tracked", len(jobs), "direct ATS, YC, web and remote boards"),
        ("Tailored packages", tailored, "resume + cover letter bundles"),
        ("Startup matches", len(startups), "company-first outreach targets"),
        ("Target lanes", len(summary.get("target_lanes", [])), "profile-aware search modes"),
    ])
    st.markdown(
        f"""
        <div class="hero">
          <div class="eyebrow">Personal job agent</div>
          <h1>Find sharper roles. Tailor faster. Apply with control.</h1>
          <p>A private workspace for your hardware, FPGA, applications, software, AI, and game-development job search — with approval gates before anything goes out.</p>
          {metric_grid}
        </div>
        """,
        unsafe_allow_html=True,
    )
    if outreach:
        st.caption(f"{len(outreach)} YC outreach draft(s) are currently tracked.")


def profile_tab(service: JobAgentService) -> None:
    profile = service.get_profile()
    summary = service.get_context_summary()
    st.markdown(
        """
        <div class="section-card">
          <h3>Profile cockpit</h3>
          <p>This is the source of truth used for tailoring, application answers, and outreach. Keep it accurate; the agent should not invent anything.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns(4)
    cols[0].metric("Projects", summary.get("project_count", 0))
    cols[1].metric("Experience", summary.get("experience_count", 0))
    cols[2].metric("Target lanes", len(summary.get("target_lanes", [])))
    cols[3].metric("Regions", len(summary.get("search_regions", [])))

    st.markdown(
        '<div class="pill-row">'
        + "".join(pill(lane, "purple") for lane in summary.get("target_lanes", [])[:18])
        + "</div>",
        unsafe_allow_html=True,
    )

    with st.form("profile-form"):
        st.markdown("### Identity")
        left, right = st.columns(2)
        with left:
            st.text_input("Full name", value=profile.get("full_name", ""), key="full_name")
            st.text_input("Email", value=profile.get("email", ""), key="email")
            st.text_input("Phone", value=profile.get("phone", ""), key="phone")
        with right:
            st.text_input("Location", value=profile.get("location", ""), key="location")
            st.text_input("LinkedIn", value=profile.get("linkedin_url", ""), key="linkedin_url")
            st.text_input("Portfolio", value=profile.get("portfolio_url", ""), key="portfolio_url")

        st.markdown("### Targeting")
        st.text_area("Target roles", value=profile.get("target_roles", ""), key="target_roles", height=95)
        st.text_area("Preferences", value=profile.get("preferences", ""), key="preferences", height=95)
        auth_col, sponsor_col = st.columns(2)
        with auth_col:
            st.text_area("Work authorization", value=profile.get("work_authorization", ""), key="work_authorization", height=95)
        with sponsor_col:
            st.text_area("Sponsorship required", value=profile.get("sponsorship_required", ""), key="sponsorship_required", height=95)
        st.text_area("Location preferences", value=profile.get("location_preferences", ""), key="location_preferences", height=80)
        st.text_area("Application notes", value=profile.get("application_notes", ""), key="application_notes", height=95)

        with st.expander("Base resume override", expanded=False):
            st.text_area("Paste only if you want to override the canonical profile context", value=profile.get("base_resume", ""), key="base_resume", height=220)

        if st.form_submit_button("Save profile", use_container_width=True):
            saved = service.save_profile({key: st.session_state.get(key, "") for key in (
                "full_name", "email", "phone", "location", "linkedin_url", "portfolio_url",
                "target_roles", "preferences", "work_authorization", "sponsorship_required",
                "location_preferences", "application_notes", "base_resume",
            )})
            st.success(f"Saved profile for {saved.get('full_name') or 'candidate'}")


def jobs_tab(service: JobAgentService) -> None:
    st.markdown(
        """
        <div class="section-card">
          <h3>Job pipeline</h3>
          <p>Search direct ATS boards, YC roles, remote boards, and web results. Tailor only the roles worth spending effort on.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    action_col, refresh_col = st.columns([1, 1])
    if action_col.button("Sync jobs", use_container_width=True):
        with st.spinner("Syncing jobs across configured lanes..."):
            show_json_result("Job sync complete", service.sync_jobs())
    if refresh_col.button("Refresh", use_container_width=True):
        refresh()

    jobs = service.list_jobs()
    lanes = sorted({job.get("role_lane") for job in jobs if job.get("role_lane")})
    statuses = sorted({job.get("status") for job in jobs if job.get("status")})
    filter_col1, filter_col2, filter_col3 = st.columns([1.2, 1, 1.4])
    lane_filter = filter_col1.selectbox("Lane", ["All"] + lanes)
    status_filter = filter_col2.selectbox("Status", ["All"] + statuses)
    query = filter_col3.text_input("Search company/title", placeholder="diode, fpga, unity, applications...")

    filtered = jobs
    if lane_filter != "All":
        filtered = [job for job in filtered if job.get("role_lane") == lane_filter]
    if status_filter != "All":
        filtered = [job for job in filtered if job.get("status") == status_filter]
    if query.strip():
        needle = query.lower().strip()
        filtered = [
            job for job in filtered
            if needle in f"{job.get('title','')} {job.get('company','')} {job.get('description','')}".lower()
        ]
    filtered = sorted(filtered, key=lambda job: (int(job.get("prescreen_score") or 0), job.get("discovered_at") or ""), reverse=True)

    st.caption(f"Showing {len(filtered)} of {len(jobs)} jobs")
    if not filtered:
        st.markdown('<div class="empty">No matching jobs yet. Sync jobs or loosen the filters.</div>', unsafe_allow_html=True)
        return

    for job in filtered[:150]:
        package = service.get_package(int(job["package_id"])) if job.get("package_id") else None
        score = int(package.get("fit_score") or 0) if package else int(job.get("prescreen_score") or 0)
        score_label = "fit" if package else "pre"
        status = str(job.get("decision") or job.get("status") or "discovered")
        st.markdown(
            f"""
            <div class="job-card">
              <div style="display:flex; justify-content:space-between; gap:16px; align-items:flex-start;">
                <div>
                  <div class="job-title">{esc(job.get('title'))}</div>
                  <div class="job-meta"><span class="company-name">{esc(job.get('company'))}</span> · {esc(job.get('location'))}</div>
                  <div class="pill-row">
                    {pill(job.get('role_lane') or 'Unclassified', 'purple')}
                    {pill(job.get('search_region') or 'Region unknown')}
                    {pill(job.get('source') or 'source')}
                    {pill(status, decision_tone(status))}
                  </div>
                </div>
                <div style="text-align:center;">
                  <div class="score-badge {score_tone(score)}">{score}</div>
                  <div class="mini-label">{score_label}</div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.expander("Open role workspace", expanded=False):
            link_col, action_col = st.columns([1, 1])
            if job.get("url"):
                link_col.link_button("Open job page", job["url"], use_container_width=True)
            action_col.caption(f"Discovered: {job.get('discovered_at', '')}")
            st.write(short_text(job.get("description", ""), 2500))
            if package:
                st.divider()
                fit_col, summary_col = st.columns([.35, 1])
                fit_col.metric("Fit score", package.get("fit_score", 0))
                summary_col.write(package.get("fit_summary", ""))
                missing = parse_json_list(package.get("missing_requirements"))
                if missing:
                    st.warning("Missing/weak requirements: " + "; ".join(str(item) for item in missing))
                st.text_area("Cover letter", value=package.get("cover_letter", ""), height=180, key=f"cover-{package['id']}")
                d1, d2, d3 = st.columns(3)
                with d1:
                    download_artifact(service, int(package["id"]), "resume.pdf", "Resume PDF")
                with d2:
                    download_artifact(service, int(package["id"]), "resume.tex", "Resume LaTeX")
                with d3:
                    download_artifact(service, int(package["id"]), "cover-letter.txt", "Cover letter")
                decision_col1, decision_col2 = st.columns(2)
                if decision_col1.button("Approve package", key=f"approve-{package['id']}", use_container_width=True):
                    decided = service.decide(int(package["id"]), "approved")
                    st.success("Approved. Keep the approval token private.")
                    st.code(decided.get("approval_token", ""))
                if decision_col2.button("Reject package", key=f"reject-{package['id']}", use_container_width=True):
                    service.decide(int(package["id"]), "rejected")
                    st.warning("Rejected")
            else:
                if st.button("Tailor resume for this job", key=f"tailor-{job['id']}", use_container_width=True):
                    with st.spinner("Tailoring with OpenAI..."):
                        try:
                            created = service.tailor_job(int(job["id"]))
                            st.success(f"Tailored package created with fit score {created.get('fit_score')}")
                            st.rerun()
                        except Exception as error:
                            st.error(str(error))


def company_card(company: dict[str, Any], reasons: list[Any], kind: str) -> None:
    score = int(company.get("fit_score") or 0)
    st.markdown(
        f"""
        <div class="job-card">
          <div style="display:flex; justify-content:space-between; gap:16px;">
            <div>
              <div class="job-title">{esc(company.get('name'))}</div>
              <div class="job-meta">{esc(company.get('domain'))} · {esc(company.get('stage') or company.get('batch') or kind)}</div>
              <div class="pill-row">
                {pill(kind, 'purple')}
                {pill(company.get('outreach_decision') or 'not drafted', decision_tone(str(company.get('outreach_decision') or 'drafted')))}
                {pill(f"{company.get('pending_contacts', 0)} contacts")}
              </div>
            </div>
            <div style="text-align:center;">
              <div class="score-badge {score_tone(score)}">{score}</div>
              <div class="mini-label">fit</div>
            </div>
          </div>
          <p style="color:#9baabd; margin:14px 0 0;">{esc(short_text(company.get('description') or company.get('one_liner') or company.get('funding_signal') or '', 260))}</p>
          <div class="pill-row">{''.join(pill(reason, 'good') for reason in reasons[:5])}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def outreach_tab(service: JobAgentService) -> None:
    st.markdown(
        """
        <div class="section-card">
          <h3>Company-first outreach</h3>
          <p>Find funded companies that match your profile, then draft direct outreach even when no role is posted.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    yc_col, startup_col = st.columns(2)
    if yc_col.button("Sync YC companies", use_container_width=True):
        with st.spinner("Syncing YC directory..."):
            show_json_result("YC sync complete", service.sync_yc_companies())
    if startup_col.button("Sync funded startups", use_container_width=True):
        with st.spinner("Syncing funded startup sources..."):
            show_json_result("Startup sync complete", service.sync_startups())

    st.markdown("### Funded startup matches")
    startups = service.list_startups()
    if not startups:
        st.markdown('<div class="empty">No funded startups synced yet.</div>', unsafe_allow_html=True)
    for company in startups[:80]:
        reasons = parse_json_list(company.get("fit_reasons"))
        company_card(company, reasons, "funded startup")
        with st.expander("Draft workspace", expanded=False):
            if st.button("Draft startup outreach", key=f"startup-outreach-{company['id']}", use_container_width=True):
                with st.spinner("Drafting outreach..."):
                    try:
                        st.json(service.generate_startup_outreach(int(company["id"])))
                    except Exception as error:
                        st.error(str(error))

    st.markdown("### YC company matches")
    yc_companies = service.list_yc_companies()
    if not yc_companies:
        st.markdown('<div class="empty">No YC companies synced yet.</div>', unsafe_allow_html=True)
    for company in yc_companies[:80]:
        reasons = parse_json_list(company.get("fit_reasons"))
        company_card(company, reasons, "YC")
        with st.expander("Draft workspace", expanded=False):
            st.caption(company.get("tags", ""))
            if st.button("Draft YC outreach", key=f"yc-outreach-{company['id']}", use_container_width=True):
                with st.spinner("Drafting outreach..."):
                    try:
                        st.json(service.generate_outreach(int(company["id"])))
                    except Exception as error:
                        st.error(str(error))

    st.markdown("### Drafted YC outreach")
    drafted = service.list_outreach()
    if not drafted:
        st.markdown('<div class="empty">No YC outreach drafts yet.</div>', unsafe_allow_html=True)
    for outreach in drafted[:50]:
        status = str(outreach.get("decision") or "drafted")
        st.markdown(
            f"""
            <div class="job-card">
              <div class="job-title">{esc(outreach.get('company_name'))}</div>
              <div class="job-meta">To {esc(outreach.get('contact_email'))} · {esc(outreach.get('domain'))}</div>
              <div class="pill-row">{pill(status, decision_tone(status))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.expander("Send / preview", expanded=False):
            st.write(outreach.get("subject", ""))
            if st.button("Dry-run/send through SMTP gate", key=f"send-outreach-{outreach['id']}", use_container_width=True):
                try:
                    st.json(service.send_outreach(int(outreach["id"])))
                except Exception as error:
                    st.error(str(error))


def automation_tab(service: JobAgentService) -> None:
    st.markdown(
        """
        <div class="section-card">
          <h3>Automation log</h3>
          <p>Audit trail for dry runs, approvals, outreach drafts, sends, and application handoffs.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    events = service.get_automation_log(100)
    if not events:
        st.markdown('<div class="empty">No automation events yet.</div>', unsafe_allow_html=True)
        return
    for event in events:
        with st.expander(f"{event.get('event')} · {event.get('created_at')}", expanded=False):
            st.code(json.dumps(event, indent=2), language="json")


def main() -> None:
    st.set_page_config(page_title="Application OS", page_icon="⚡", layout="wide")
    inject_design()
    load_streamlit_secrets_into_env()
    require_password()
    service = get_service()
    sidebar(service)
    hero(service)

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
