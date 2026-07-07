from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class UnmappedQuestionError(RuntimeError):
    """Raised when a form asks a screening question that can't be mapped to a known,
    candidate-supplied answer. The caller must skip the submission rather than guess."""


@dataclass(frozen=True)
class SubmissionResult:
    status: str  # "dry_run" | "submitted"
    confirmation_reference: str = ""
    log_path: str = ""


# Substrings in a form-field label (lowercased) mapped to a profile.application_answers key.
# Only fields that map here are ever auto-filled from structured data; anything else that looks
# like a required custom question raises UnmappedQuestionError instead of being guessed.
KNOWN_FIELD_MAP: dict[str, str] = {
    "legal name": "legal_name",
    "preferred name": "preferred_name",
    "street address": "street_address",
    "address": "street_address",
    "city": "city",
    "state": "state_province",
    "province": "state_province",
    "postal code": "postal_code",
    "zip": "postal_code",
    "country": "country_of_residence",
    "github": "github_url",
    "earliest start date": "earliest_start_date",
    "start date": "earliest_start_date",
    "employment type": "employment_types",
    "relocate": "willing_to_relocate",
    "travel": "willing_to_travel",
    "authorized to work in the united states": "authorized_us",
    "require sponsorship": "sponsorship_us",
    "authorized to work in canada": "authorized_canada",
    "authorized to work in the uk": "authorized_uk",
    "authorized to work in australia": "authorized_australia",
    "authorized to work in india": "authorized_india",
    "need sponsorship in the united states": "sponsorship_us",
    "need sponsorship in canada": "sponsorship_canada",
    "need sponsorship in the uk": "sponsorship_uk",
    "need sponsorship in australia": "sponsorship_australia",
    "race": "demographic_response_policy",
    "ethnicity": "demographic_response_policy",
    "gender": "demographic_response_policy",
    "veteran": "demographic_response_policy",
    "disability": "demographic_response_policy",
}


SENSITIVE_FIELD_TERMS: tuple[str, ...] = (
    "social security", "ssn", "passport", "driver license", "driver's license",
    "national id", "date of birth", "birth date", "bank", "routing number",
    "account number", "tax id", "sin number", "aadhaar", "pan card",
)


def normalize_label(label: str) -> str:
    value = " ".join(label.replace("*", " ").split()).strip().lower()
    return value.rstrip(":")


def is_sensitive_question(label: str) -> bool:
    normalized = normalize_label(label)
    return any(term in normalized for term in SENSITIVE_FIELD_TERMS)


def ensure_artifact_exists(path: str, label: str) -> None:
    if not path:
        return
    if not Path(path).is_file():
        raise FileNotFoundError(f"{label} artifact does not exist: {path}")


def map_known_field(label: str, profile: dict[str, Any]) -> str | None:
    normalized = normalize_label(label)
    answers = profile.get("application_answers", {}) or {}
    # Longest phrase first: "authorized to work in the united states" must be checked
    # before a short generic phrase like "state", which is a substring of "United States".
    for phrase, field_key in sorted(KNOWN_FIELD_MAP.items(), key=lambda item: -len(item[0])):
        if phrase in normalized:
            value = answers.get(field_key)
            return str(value) if value not in (None, "") else None
    return None


def mapped_or_raise(label: str, profile: dict[str, Any], ats_name: str, required: bool) -> str | None:
    if is_sensitive_question(label):
        raise UnmappedQuestionError(
            f"Sensitive {ats_name} question requires manual review: {label}"
        )
    mapped = map_known_field(label, profile)
    if mapped is not None:
        return mapped
    if required:
        raise UnmappedQuestionError(f"Unmapped required {ats_name} question: {label}")
    return None


class ATSAdapter(ABC):
    name: str

    @staticmethod
    @abstractmethod
    def detect(url: str) -> bool:
        """Return True if this adapter knows how to drive the given job URL."""

    @abstractmethod
    def submit(
        self,
        url: str,
        package: dict[str, Any],
        profile: dict[str, Any],
        submission_dir: Path,
        dry_run: bool,
    ) -> SubmissionResult:
        """Fill (and, only when not dry_run, submit) the application form.

        Every call — dry-run or live — must write a screenshot and a filled-field summary
        to submission_dir so a human can audit exactly what would have been (or was) sent.
        """
