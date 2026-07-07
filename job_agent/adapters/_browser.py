from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "playwright is required for ATS adapters. Install with "
            "`pip install -e '.[automation]'` and run `playwright install chromium`."
        ) from error
    return sync_playwright


def write_submission_log(submission_dir: Path, filled_fields: dict[str, Any], note: str = "") -> str:
    submission_dir.mkdir(parents=True, exist_ok=True)
    log_path = submission_dir / "fields.json"
    log_path.write_text(
        json.dumps({"filled_fields": filled_fields, "note": note}, indent=2), encoding="utf-8"
    )
    return str(log_path)
