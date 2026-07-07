from __future__ import annotations

from pathlib import Path
from typing import Any

from ._browser import require_playwright, write_submission_log
from .base import ATSAdapter, SubmissionResult, UnmappedQuestionError, map_known_field


class GreenhouseAdapter(ATSAdapter):
    name = "greenhouse"

    @staticmethod
    def detect(url: str) -> bool:
        return "boards.greenhouse.io" in url or "job-boards.greenhouse.io" in url

    def submit(
        self,
        url: str,
        package: dict[str, Any],
        profile: dict[str, Any],
        submission_dir: Path,
        dry_run: bool,
    ) -> SubmissionResult:
        sync_playwright = require_playwright()
        filled: dict[str, Any] = {}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_selector("form#application_form, #application_form", timeout=15000)

                name_parts = str(profile.get("full_name", "")).split(" ", 1)
                self._fill_text(page, "#first_name", name_parts[0] if name_parts else "", filled, "first_name")
                self._fill_text(page, "#last_name", name_parts[1] if len(name_parts) > 1 else "", filled, "last_name")
                self._fill_text(page, "#email", str(profile.get("email", "")), filled, "email")
                self._fill_text(page, "#phone", str(profile.get("phone", "")), filled, "phone")

                resume_path = str(package.get("resume_pdf_path", ""))
                if resume_path:
                    self._upload(page, "#resume, input[name='resume']", resume_path, filled, "resume")
                cover_letter_path = str(package.get("cover_letter_path", ""))
                if cover_letter_path:
                    self._upload(page, "#cover_letter, input[name='cover_letter']", cover_letter_path, filled, "cover_letter")

                self._check_custom_questions(page, profile, filled)

                submission_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = submission_dir / ("dry_run.png" if dry_run else "submitted.png")
                page.screenshot(path=str(screenshot_path))
                log_path = write_submission_log(
                    submission_dir, filled, note="dry run" if dry_run else "live submission"
                )

                if dry_run:
                    return SubmissionResult(status="dry_run", log_path=log_path)

                page.click("#submit_app, button[type='submit']")
                page.wait_for_load_state("networkidle", timeout=30000)
                page.screenshot(path=str(submission_dir / "confirmation.png"))
                return SubmissionResult(
                    status="submitted", confirmation_reference=page.url, log_path=log_path
                )
            finally:
                browser.close()

    @staticmethod
    def _fill_text(page, selector: str, value: str, filled: dict[str, Any], key: str) -> None:
        if not value:
            return
        locator = page.locator(selector).first
        if locator.count() == 0:
            return
        locator.fill(value)
        filled[key] = value

    @staticmethod
    def _upload(page, selector: str, path: str, filled: dict[str, Any], key: str) -> None:
        locator = page.locator(selector).first
        if locator.count() == 0:
            return
        locator.set_input_files(path)
        filled[key] = path

    @staticmethod
    def _check_custom_questions(page, profile: dict[str, Any], filled: dict[str, Any]) -> None:
        fields = page.locator(".field")
        for index in range(fields.count()):
            field = fields.nth(index)
            label_locator = field.locator("label").first
            if label_locator.count() == 0:
                continue
            label_text = label_locator.inner_text().strip()
            if not label_text:
                continue
            input_locator = field.locator("input[type=text], textarea, select").first
            if input_locator.count() == 0:
                continue
            if input_locator.input_value():
                continue
            required = "*" in label_text or field.locator("[required]").count() > 0
            mapped = map_known_field(label_text, profile)
            if mapped is not None:
                input_locator.fill(mapped)
                filled[label_text] = mapped
            elif required:
                raise UnmappedQuestionError(f"Unmapped required Greenhouse question: {label_text}")
