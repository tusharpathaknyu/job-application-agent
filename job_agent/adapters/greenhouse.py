from __future__ import annotations

from pathlib import Path
from typing import Any

from ._browser import require_playwright, write_submission_log
from .base import (
    ATSAdapter, SubmissionResult, UnmappedQuestionError, ensure_artifact_exists,
    mapped_or_raise,
)


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
                ensure_artifact_exists(resume_path, "resume")
                if resume_path:
                    self._upload(page, "#resume, input[name='resume']", resume_path, filled, "resume")
                cover_letter_path = str(package.get("cover_letter_path", ""))
                ensure_artifact_exists(cover_letter_path, "cover letter")
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
                self._require_confirmation(page)
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
        if not locator.is_visible() or not locator.is_enabled():
            return
        locator.fill(value)
        filled[key] = value

    @staticmethod
    def _upload(page, selector: str, path: str, filled: dict[str, Any], key: str) -> None:
        locator = page.locator(selector).first
        if locator.count() == 0:
            return
        if not locator.is_visible() or not locator.is_enabled():
            return
        locator.set_input_files(path)
        filled[key] = path

    @staticmethod
    def _check_custom_questions(page, profile: dict[str, Any], filled: dict[str, Any]) -> None:
        fields = page.locator(".field")
        for index in range(fields.count()):
            field = fields.nth(index)
            if not field.is_visible():
                continue
            label_locator = field.locator("label").first
            if label_locator.count() == 0:
                continue
            label_text = label_locator.inner_text().strip()
            if not label_text:
                continue
            required = "*" in label_text or field.locator("[required]").count() > 0
            mapped = mapped_or_raise(label_text, profile, "Greenhouse", required)
            if mapped is None:
                continue
            input_locator = field.locator("input[type=text], input[type=email], input[type=tel], textarea").first
            if input_locator.count() > 0 and input_locator.is_visible() and input_locator.is_enabled():
                if not input_locator.input_value():
                    input_locator.fill(mapped)
                    filled[label_text] = mapped
                continue
            select_locator = field.locator("select").first
            if select_locator.count() > 0 and select_locator.is_visible() and select_locator.is_enabled():
                GreenhouseAdapter._select_best_option(select_locator, mapped)
                filled[label_text] = mapped
                continue
            radio_locator = field.locator("input[type=radio]").first
            if radio_locator.count() > 0:
                GreenhouseAdapter._choose_radio(field, mapped)
                filled[label_text] = mapped
                continue
            if required:
                raise UnmappedQuestionError(f"Unsupported required Greenhouse question: {label_text}")

    @staticmethod
    def _select_best_option(select_locator, value: str) -> None:
        options = select_locator.locator("option")
        normalized = value.strip().lower()
        for index in range(options.count()):
            option = options.nth(index)
            label = option.inner_text().strip()
            option_value = option.get_attribute("value") or label
            if normalized in label.lower() or label.lower() in normalized:
                select_locator.select_option(option_value)
                return
        select_locator.select_option(label=value)

    @staticmethod
    def _choose_radio(field, value: str) -> None:
        normalized = value.strip().lower()
        labels = field.locator("label")
        for index in range(labels.count()):
            label = labels.nth(index)
            text = label.inner_text().strip().lower()
            if normalized in text or text in normalized:
                label.click()
                return
        if normalized in {"yes", "y", "true"}:
            candidate = field.locator("label:has-text('Yes')").first
            if candidate.count() > 0:
                candidate.click()
                return
        if normalized in {"no", "n", "false"}:
            candidate = field.locator("label:has-text('No')").first
            if candidate.count() > 0:
                candidate.click()
                return
        raise UnmappedQuestionError("Could not match Greenhouse radio option")

    @staticmethod
    def _require_confirmation(page) -> None:
        confirmation_text = page.locator("body").inner_text(timeout=10000).lower()
        if any(term in confirmation_text for term in ("thank you", "application submitted", "received your application")):
            return
        if "confirmation" in page.url.lower() or "submitted" in page.url.lower():
            return
        raise RuntimeError("Greenhouse submit clicked but no confirmation page/text was detected")
