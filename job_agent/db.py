from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    full_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    linkedin_url TEXT NOT NULL DEFAULT '',
    portfolio_url TEXT NOT NULL DEFAULT '',
    target_roles TEXT NOT NULL DEFAULT '',
    preferences TEXT NOT NULL DEFAULT '',
    work_authorization TEXT NOT NULL DEFAULT '',
    sponsorship_required TEXT NOT NULL DEFAULT '',
    location_preferences TEXT NOT NULL DEFAULT '',
    salary_preferences TEXT NOT NULL DEFAULT '',
    application_notes TEXT NOT NULL DEFAULT '',
    application_answers TEXT NOT NULL DEFAULT '{}',
    base_resume TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO profile (id) VALUES (1);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    description TEXT NOT NULL,
    salary TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    role_lane TEXT NOT NULL DEFAULT '',
    search_region TEXT NOT NULL DEFAULT '',
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'discovered',
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    fit_score INTEGER NOT NULL,
    fit_summary TEXT NOT NULL,
    missing_requirements TEXT NOT NULL DEFAULT '[]',
    tailored_resume TEXT NOT NULL,
    resume_data TEXT NOT NULL DEFAULT '{}',
    cover_letter TEXT NOT NULL,
    screening_notes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decision TEXT NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','approved','rejected')),
    decision_at TEXT,
    approval_token TEXT,
    submitted_at TEXT,
    submission_reference TEXT,
    resume_path TEXT NOT NULL DEFAULT '',
    resume_pdf_path TEXT NOT NULL DEFAULT '',
    cover_letter_path TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS yc_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    batch TEXT NOT NULL DEFAULT '',
    one_liner TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    fit_score INTEGER NOT NULL DEFAULT 0,
    fit_reasons TEXT NOT NULL DEFAULT '[]',
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS yc_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES yc_companies(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, email)
);

CREATE TABLE IF NOT EXISTS outreach_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL UNIQUE REFERENCES yc_companies(id) ON DELETE CASCADE,
    contact_email TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    resume_data TEXT NOT NULL DEFAULT '{}',
    resume_path TEXT NOT NULL DEFAULT '',
    resume_pdf_path TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT 'drafted',
    dry_run INTEGER NOT NULL DEFAULT 1,
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS startup_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    name TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    funding_signal TEXT NOT NULL DEFAULT '',
    funding_date TEXT NOT NULL DEFAULT '',
    funding_amount TEXT NOT NULL DEFAULT '',
    evidence_url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    fit_score INTEGER NOT NULL DEFAULT 0,
    fit_reasons TEXT NOT NULL DEFAULT '[]',
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS startup_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES startup_companies(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, email)
);

CREATE TABLE IF NOT EXISTS startup_outreach_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL UNIQUE REFERENCES startup_companies(id) ON DELETE CASCADE,
    contact_email TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    resume_data TEXT NOT NULL DEFAULT '{}',
    resume_path TEXT NOT NULL DEFAULT '',
    resume_pdf_path TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT 'drafted',
    dry_run INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
        self.path.chmod(0o600)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        profile_columns = {row[1] for row in connection.execute("PRAGMA table_info(profile)")}
        profile_additions = {
            "work_authorization": "TEXT NOT NULL DEFAULT ''",
            "sponsorship_required": "TEXT NOT NULL DEFAULT ''",
            "location_preferences": "TEXT NOT NULL DEFAULT ''",
            "salary_preferences": "TEXT NOT NULL DEFAULT ''",
            "application_notes": "TEXT NOT NULL DEFAULT ''",
            "application_answers": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, definition in profile_additions.items():
            if name not in profile_columns:
                connection.execute(f"ALTER TABLE profile ADD COLUMN {name} {definition}")
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        if "role_lane" not in job_columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN role_lane TEXT NOT NULL DEFAULT ''")
        if "search_region" not in job_columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN search_region TEXT NOT NULL DEFAULT ''")
        if "prescreen_score" not in job_columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN prescreen_score INTEGER NOT NULL DEFAULT 0")
        package_columns = {row[1] for row in connection.execute("PRAGMA table_info(packages)")}
        additions = {
            "resume_data": "TEXT NOT NULL DEFAULT '{}'",
            "resume_path": "TEXT NOT NULL DEFAULT ''",
            "resume_pdf_path": "TEXT NOT NULL DEFAULT ''",
            "cover_letter_path": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in package_columns:
                connection.execute(f"ALTER TABLE packages ADD COLUMN {name} {definition}")
        contact_columns = {row[1] for row in connection.execute("PRAGMA table_info(yc_contacts)")}
        if "status" not in contact_columns:
            connection.execute("ALTER TABLE yc_contacts ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if "last_error" not in contact_columns:
            connection.execute("ALTER TABLE yc_contacts ADD COLUMN last_error TEXT NOT NULL DEFAULT ''")
        company_columns = {row[1] for row in connection.execute("PRAGMA table_info(yc_companies)")}
        if "fit_score" not in company_columns:
            connection.execute("ALTER TABLE yc_companies ADD COLUMN fit_score INTEGER NOT NULL DEFAULT 0")
        if "fit_reasons" not in company_columns:
            connection.execute("ALTER TABLE yc_companies ADD COLUMN fit_reasons TEXT NOT NULL DEFAULT '[]'")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS startup_companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                name TEXT NOT NULL,
                domain TEXT NOT NULL DEFAULT '',
                country TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT '',
                funding_signal TEXT NOT NULL DEFAULT '',
                funding_date TEXT NOT NULL DEFAULT '',
                funding_amount TEXT NOT NULL DEFAULT '',
                evidence_url TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                fit_score INTEGER NOT NULL DEFAULT 0,
                fit_reasons TEXT NOT NULL DEFAULT '[]',
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, source_id)
            );
            CREATE TABLE IF NOT EXISTS startup_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL REFERENCES startup_companies(id) ON DELETE CASCADE,
                email TEXT NOT NULL,
                alias_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_id, email)
            );
            CREATE TABLE IF NOT EXISTS startup_outreach_packages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL UNIQUE REFERENCES startup_companies(id) ON DELETE CASCADE,
                contact_email TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                resume_data TEXT NOT NULL DEFAULT '{}',
                resume_path TEXT NOT NULL DEFAULT '',
                resume_pdf_path TEXT NOT NULL DEFAULT '',
                decision TEXT NOT NULL DEFAULT 'drafted',
                dry_run INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connect() as connection:
            cursor = connection.execute(sql, params)
            return int(cursor.lastrowid)

    def audit(self, event: str, entity_type: str, entity_id: int, details: dict[str, Any] | None = None) -> None:
        self.execute(
            "INSERT INTO audit_log(event, entity_type, entity_id, details) VALUES (?, ?, ?, ?)",
            (event, entity_type, entity_id, json.dumps(details or {})),
        )
