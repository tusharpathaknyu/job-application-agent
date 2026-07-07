from __future__ import annotations

from pathlib import Path
from typing import Any

from ._browser import require_playwright, write_submission_log
from .base import (
    ATSAdapter, SubmissionResult, UnmappedQuestionError, ensure_artifact_exists,
    mapped_or_raise,
)


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
                ensure_artifact_exists(resume_path, "resume")
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
        questions = page.locator(".application-question")
        for index in range(questions.count()):
            question = questions.nth(index)
            if not question.is_visible():
                continue
            label_locator = question.locator(".application-label").first
            if label_locator.count() == 0:
                continue
            label_text = label_locator.inner_text().strip()
            if not label_text:
                continue
            required = question.locator(".required").count() > 0
            mapped = mapped_or_raise(label_text, profile, "Lever", required)
            if mapped is None:
                continue
            input_locator = question.locator("input[type=text], input[type=email], input[type=tel], textarea").first
            if input_locator.count() > 0 and input_locator.is_visible() and input_locator.is_enabled():
                if not input_locator.input_value():
                    input_locator.fill(mapped)
                    filled[label_text] = mapped
                continue
            select_locator = question.locator("select").first
            if select_locator.count() > 0 and select_locator.is_visible() and select_locator.is_enabled():
                LeverAdapter._select_best_option(select_locator, mapped)
                filled[label_text] = mapped
                continue
            radio_locator = question.locator("input[type=radio]").first
            if radio_locator.count() > 0:
                LeverAdapter._choose_radio(question, mapped)
                filled[label_text] = mapped
                continue
            if required:
                raise UnmappedQuestionError(f"Unsupported required Lever question: {label_text}")

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
    def _choose_radio(question, value: str) -> None:
        normalized = value.strip().lower()
        labels = question.locator("label")
        for index in range(labels.count()):
            label = labels.nth(index)
            text = label.inner_text().strip().lower()
            if normalized in text or text in normalized:
                label.click()
                return
        if normalized in {"yes", "y", "true"}:
            candidate = question.locator("label:has-text('Yes')").first
            if candidate.count() > 0:
                candidate.click()
                return
        if normalized in {"no", "n", "false"}:
            candidate = question.locator("label:has-text('No')").first
            if candidate.count() > 0:
                candidate.click()
                return
        raise UnmappedQuestionError("Could not match Lever radio option")

    @staticmethod
    def _require_confirmation(page) -> None:
        confirmation_text = page.locator("body").inner_text(timeout=10000).lower()
        if any(term in confirmation_text for term in ("thank you", "application submitted", "received your application")):
            return
        if "confirmation" in page.url.lower() or "submitted" in page.url.lower():
            return
        raise RuntimeError("Lever submit clicked but no confirmation page/text was detected")
