from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_candidate_context(path: Path | str) -> dict[str, Any]:
    context_path = Path(path)
    if not context_path.is_file():
        return {}
    data = json.loads(context_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Candidate context must be a JSON object")
    return data


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
