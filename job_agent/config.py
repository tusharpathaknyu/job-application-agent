from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"").strip("'"))


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    host: str
    port: int
    hosted_mode: bool
    app_username: str
    app_password: str
    database_path: Path
    job_search_query: str
    job_search_limit: int
    sync_interval_minutes: int
    auto_tailor: bool
    max_tailors_per_cycle: int
    min_fit_score: int
    enable_live_applications: bool
    candidate_context_path: Path = Path("profile/candidate_context.json")
    resume_template_path: Path = Path("profile/resume_template.tex")
    job_search_queries: tuple[str, ...] = ()
    artifact_dir: Path = Path("data/packages")
    enable_openai_job_search: bool = True
    web_search_job_limit: int = 20
    web_search_per_lane_limit: int = 10
    job_search_regions: tuple[str, ...] = ()
    enable_yc_job_search: bool = False
    auto_submit: bool = False
    auto_submit_min_fit_score: int = 70
    enable_live_greenhouse: bool = False
    enable_live_lever: bool = False
    auto_outreach: bool = False
    enable_live_outreach: bool = False
    yc_min_batch_year: int = 2020
    yc_outreach_min_fit_score: int = 15
    yc_enrich_max_companies: int = 100
    max_outreach_per_cycle: int = 25
    max_applications_per_cycle: int = 25
    startup_min_fit_score: int = 15
    startup_enrich_max_companies: int = 100
    startup_sec_form_d_days: int = 30
    startup_sec_form_d_limit: int = 200
    startup_portfolio_urls: tuple[str, ...] = ()
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8787")),
            hosted_mode=_bool("HOSTED_MODE"),
            app_username=os.getenv("APP_USERNAME", "tushar"),
            app_password=os.getenv("APP_PASSWORD", ""),
            database_path=Path(os.getenv("DATABASE_PATH", "data/job_agent.db")),
            job_search_query=os.getenv("JOB_SEARCH_QUERY", "software engineer"),
            job_search_limit=int(os.getenv("JOB_SEARCH_LIMIT", "25")),
            sync_interval_minutes=int(os.getenv("JOB_SYNC_INTERVAL_MINUTES", "360")),
            auto_tailor=_bool("AUTO_TAILOR", True),
            max_tailors_per_cycle=int(os.getenv("MAX_TAILORS_PER_CYCLE", "5")),
            min_fit_score=int(os.getenv("MIN_FIT_SCORE", "55")),
            enable_live_applications=_bool("ENABLE_LIVE_APPLICATIONS"),
            candidate_context_path=Path(os.getenv("CANDIDATE_CONTEXT_PATH", "profile/candidate_context.json")),
            resume_template_path=Path(os.getenv("RESUME_TEMPLATE_PATH", "profile/resume_template.tex")),
            job_search_queries=tuple(
                query.strip()
                for query in os.getenv(
                    "JOB_SEARCH_QUERIES",
                    "software engineer,backend engineer,full stack engineer,platform engineer,AI engineer,machine learning engineer,applications engineer,field applications engineer,solutions engineer,power electronics engineer,analog hardware engineer,PCB design engineer,hardware validation engineer,firmware engineer,FPGA engineer,FPGA verification engineer,SoC design engineer,ASIC RTL engineer,design verification engineer,SerDes validation engineer,mixed signal validation engineer,game developer,unity developer,gameplay engineer,technical game designer,XR engineer",
                ).split(",")
                if query.strip()
            ),
            artifact_dir=Path(os.getenv("ARTIFACT_DIR", "data/packages")),
            enable_openai_job_search=_bool("ENABLE_OPENAI_JOB_SEARCH", True),
            web_search_job_limit=int(os.getenv("WEB_SEARCH_JOB_LIMIT", "20")),
            web_search_per_lane_limit=int(os.getenv("WEB_SEARCH_PER_LANE_LIMIT", "10")),
            job_search_regions=tuple(
                region.strip()
                for region in os.getenv(
                    "JOB_SEARCH_REGIONS",
                    "Worldwide remote,India,Canada,United Kingdom,Australia,Europe,Singapore,United States",
                ).split(",")
                if region.strip()
            ),
            enable_yc_job_search=_bool("ENABLE_YC_JOB_SEARCH", True),
            auto_submit=_bool("AUTO_SUBMIT"),
            auto_submit_min_fit_score=int(os.getenv("AUTO_SUBMIT_MIN_FIT_SCORE", "70")),
            enable_live_greenhouse=_bool("ENABLE_LIVE_GREENHOUSE"),
            enable_live_lever=_bool("ENABLE_LIVE_LEVER"),
            auto_outreach=_bool("AUTO_OUTREACH"),
            enable_live_outreach=_bool("ENABLE_LIVE_OUTREACH"),
            yc_min_batch_year=int(os.getenv("YC_MIN_BATCH_YEAR", "2005")),
            yc_outreach_min_fit_score=int(os.getenv("YC_OUTREACH_MIN_FIT_SCORE", "15")),
            yc_enrich_max_companies=int(os.getenv("YC_ENRICH_MAX_COMPANIES", "100")),
            max_outreach_per_cycle=int(os.getenv("MAX_OUTREACH_PER_CYCLE", "25")),
            max_applications_per_cycle=int(os.getenv("MAX_APPLICATIONS_PER_CYCLE", "25")),
            startup_min_fit_score=int(os.getenv("STARTUP_MIN_FIT_SCORE", "15")),
            startup_enrich_max_companies=int(os.getenv("STARTUP_ENRICH_MAX_COMPANIES", "100")),
            startup_sec_form_d_days=int(os.getenv("STARTUP_SEC_FORM_D_DAYS", "30")),
            startup_sec_form_d_limit=int(os.getenv("STARTUP_SEC_FORM_D_LIMIT", "200")),
            startup_portfolio_urls=tuple(
                url.strip()
                for url in os.getenv("STARTUP_PORTFOLIO_URLS", "").split(",")
                if url.strip()
            ),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from_email=os.getenv("SMTP_FROM_EMAIL", ""),
        )
