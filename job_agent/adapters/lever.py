from __future__ import annotations

from pathlib import Path
from typing import Any

from ._browser import require_playwright, write_submission_log
from .base import ATSAdapter, SubmissionResult, UnmappedQuestionError, map_known_field


class LeverAdapter(ATSAdapter):
    name = "lever"

    @staticmethod
    def detect(url: str) -> bool:
        return "jobs.lever.co" in url

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
                apply_url = url if url.rstrip("/").endswith("/apply") else url.rstrip("/") + "/apply"
                page.goto(apply_url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_selector("form.application-form, form[data-qa='btn-apply']", timeout=15000)

                self._fill_text(page, "input[name='name']", str(profile.get("full_name", "")), filled, "name")
                self._fill_text(page, "input[name='email']", str(profile.get("email", "")), filled, "email")
                self._fill_text(page, "input[name='phone']", str(profile.get("phone", "")), filled, "phone")
                linkedin = str(profile.get("linkedin_url", ""))
                if linkedin:
                    self._fill_text(page, "input[name='urls[LinkedIn]']", linkedin, filled, "linkedin")

                resume_path = str(package.get("resume_pdf_path", ""))
                if resume_path:
                    self._upload(page, "input[name='resume']", resume_path, filled, "resume")

                self._check_custom_questions(page, profile, filled)

                submission_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = submission_dir / ("dry_run.png" if dry_run else "submitted.png")
                page.screenshot(path=str(screenshot_path))
                log_path = write_submission_log(
                    submission_dir, filled, note="dry run" if dry_run else "live submission"
                )

                if dry_run:
                    return SubmissionResult(status="dry_run", log_path=log_path)

                page.click("button[type='submit']")
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
        questions = page.locator(".application-question")
        for index in range(questions.count()):
            question = questions.nth(index)
            label_locator = question.locator(".application-label").first
            if label_locator.count() == 0:
                continue
            label_text = label_locator.inner_text().strip()
            if not label_text:
                continue
            input_locator = question.locator("input[type=text], textarea, select").first
            if input_locator.count() == 0:
                continue
            if input_locator.input_value():
                continue
            required = question.locator(".required").count() > 0
            mapped = map_known_field(label_text, profile)
            if mapped is not None:
                input_locator.fill(mapped)
                filled[label_text] = mapped
            elif required:
                raise UnmappedQuestionError(f"Unmapped required Lever question: {label_text}")
