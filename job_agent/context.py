from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def apply_project_exclusions(context: dict[str, Any]) -> dict[str, Any]:
    excluded_projects = context.get("excluded_projects", [])
    excluded_names = {
        str(item.get("name", "")).strip().casefold()
        for item in excluded_projects
        if isinstance(item, dict)
    }
    excluded_repositories = {
        str(item.get("repository", "")).strip().casefold()
        for item in excluded_projects
        if isinstance(item, dict)
    }
    if not excluded_names and not excluded_repositories:
        return context

    sanitized = dict(context)
    sanitized["projects"] = [
        project
        for project in context.get("projects", [])
        if str(project.get("name", "")).strip().casefold() not in excluded_names
        and str(project.get("repository", "")).strip().casefold() not in excluded_repositories
    ]
    return sanitized


def load_candidate_context(path: Path | str) -> dict[str, Any]:
    context_path = Path(path)
    if not context_path.is_file():
        return {}
    data = json.loads(context_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Candidate context must be a JSON object")
    return apply_project_exclusions(data)


def load_resume_template(path: Path | str) -> str:
    template_path = Path(path)
    return template_path.read_text(encoding="utf-8") if template_path.is_file() else ""


def context_summary(context: dict[str, Any], template: str = "") -> dict[str, Any]:
    return {
        "loaded": bool(context),
        "candidate": context.get("identity", {}).get("resume_name", ""),
        "experience_count": len(context.get("experience", [])),
        "project_count": len(context.get("projects", [])),
        "target_lanes": context.get("target_lanes", []),
        "resume_template_loaded": bool(template),
    }
