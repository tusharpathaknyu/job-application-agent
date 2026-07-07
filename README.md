# Approval-first job agent

A single-user web app that discovers remote jobs, tailors a truthful resume and cover letter for each role, and gates any application handoff behind a per-package approval token — by default granted by a human in the dashboard, or by the worker itself when `AUTO_SUBMIT=true` (see **Autonomous submission and outreach**).

This is a personal tool, not a public multi-user website. Local mode refuses non-loopback hosts. Hosted mode is explicit (`HOSTED_MODE=true`) and requires `APP_PASSWORD`, which protects the dashboard and API with HTTP Basic Auth.

## What works

- Imports roles from Remotive, Arbeitnow, and public Y Combinator startup job pages; uses OpenAI web search for current direct employer/ATS listings when a valid API key is configured; and accepts manually pasted listings.
- Pulls direct employer-owned ATS boards from Greenhouse, Lever, and Ashby. Configure known boards with `ATS_BOARD_TARGETS`, and the agent also discovers board links from already-synced YC/funded-startup company career pages.
- Stores your profile, jobs, review packages, decisions, and audit history in local SQLite.
- Uses the OpenAI Responses API with strict structured output to score fit and tailor materials.
- Enforces approval in the backend with a per-package approval token, not just a UI confirmation.
- Runs recurring discovery and tailors up to `MAX_TAILORS_PER_CYCLE` new jobs automatically.
- Loads a canonical candidate context and the established LaTeX resume template from `profile/`.
- Runs balanced searches for software, backend, platform, AI/ML, applications/field-applications, solutions, power/board/hardware, FPGA, SoC/ASIC/RTL, design-verification, SerDes, and mixed-signal roles in every cycle.
- Searches worldwide while explicitly prioritizing India, Canada, the UK, Australia, Europe, Singapore, global-remote work, and employers whose listings support international hiring or relocation.
- Generates a tailored LaTeX resume, one-page Charter PDF, and cover-letter text file for each review package.
- Stores reusable application-form answers in the permission-restricted local database and exposes them to screening analysis without placing private address or authorization details in resumes.
- Collapses near-duplicate postings across sources and orders the auto-tailoring queue by a free, local keyword-overlap score against your skills/target lanes, so scarce OpenAI calls go to the best-looking matches first.
- Sources active YC-funded companies across the full directory (`YC_MIN_BATCH_YEAR=2005` by default), scores them against your profile, enriches top matches from their public YC company pages for visible contact emails, generates a cold-outreach email + general-purpose resume per company, and can send it via SMTP.
- Pulls YC job listings with their public location, experience, salary/equity, skill, and visa-note fields so YC roles can enter the same approval-first tailoring queue as other jobs.
- Builds a broader funded-startup outreach list from free sources first: the YC directory, recent SEC Form D private-placement filings, and optional public VC/accelerator portfolio URLs. This is company-first, so it can draft outreach even when no role is posted.
- Includes game-development and interactive-systems targeting from GymEZ: Unity/C#, Godot/GDScript, React Native Unity bridge, MediaPipe camera controls, biometric/PvP mechanics, and mobile game integration.
- Can autonomously decide, approve, and submit an application through a real Greenhouse or Lever adapter with no human click, once a package clears a stricter fit-score bar than tailoring alone.

The approval workflow still exists for manual review (review the generated files, approve the package, then use **Open approved application**), but it is optional: with `AUTO_SUBMIT=true` the worker cycle decides, approves, and submits on its own through whichever ATS adapter matches the listing URL. Every adapter run — dry-run or live — writes a screenshot and a field-dump log to `data/packages/package-{id}/submission/` so nothing is invisible. See **Autonomous submission and outreach** below before turning any of this on.

## Run it

Python 3.11+ runs the application. PDF generation uses the bundled Codex runtime or the optional `pdf` dependencies (`pip install -e '.[pdf]'`); LaTeX and cover-letter files are still generated when those libraries are unavailable.

```bash
cp .env.example .env
# Add OPENAI_API_KEY to .env
python3 -m job_agent serve
```

Inside this Codex workspace, you can instead run:

```bash
./scripts/start_agent.sh
```

Open <http://127.0.0.1:8787>. The gathered candidate context and LaTeX house style load automatically; the base-resume field is optional and can hold a newer variant.

Before approving a real package, open **Reusable application-form answers** and complete the safe reusable fields. Do not store SSNs, passport numbers, passwords, birth dates, banking details, or identity documents. Unknown screening answers remain visible for manual review instead of being invented.

Discover jobs once across all configured role lanes:

```bash
python3 -m job_agent sync
```

Sync and rank YC companies for outreach without drafting emails:

```bash
python3 -m job_agent yc-sync
```

Draft YC outreach packages for the highest-ranked companies. This uses OpenAI to write the email + tailored resume, then writes dry-run `.eml` previews unless live outreach is explicitly enabled:

```bash
python3 -m job_agent yc-outreach
```

Sync and rank broader funded startups from free sources:

```bash
python3 -m job_agent startup-sync
```

Draft outreach packages for the highest-ranked funded startups:

```bash
python3 -m job_agent startup-outreach
```

Run recurring discovery and tailoring using `JOB_SYNC_INTERVAL_MINUTES`:

```bash
python3 -m job_agent worker
```

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

## Configuration

Copy `.env.example` to `.env` when the API key is not already exported in your shell. If an invalid `OPENAI_API_KEY` is already exported, unset it before starting so the `.env` value can load. The default model is `gpt-5.5`. Edit the comma-separated `JOB_SEARCH_QUERIES` value to change the search lanes. `ENABLE_YC_JOB_SEARCH=true` adds public YC startup jobs to discovery. `AUTO_TAILOR=true` only takes effect when the worker runs; cap spend with `MAX_TAILORS_PER_CYCLE`, or disable it while testing.

Do not commit `.env` or `data/job_agent.db`; they contain secrets and personal data. Your resume is stored locally but is sent to the configured OpenAI API when you click **Tailor**.

## Host it online

The repo includes `render.yaml`, `Dockerfile`, and `Procfile`. For a hosted deployment, set secrets in the hosting provider's environment UI, not in Git:

```bash
HOST=0.0.0.0
HOSTED_MODE=true
APP_USERNAME=tushar
APP_PASSWORD=<choose-a-long-password>
OPENAI_API_KEY=(set this in the host environment)
DATABASE_PATH=/var/data/job_agent.db
ARTIFACT_DIR=/var/data/packages
```

`APP_PASSWORD` is mandatory in hosted mode; the server will refuse to start without it. Leave `AUTO_SUBMIT=false`, `AUTO_OUTREACH=false`, and `ENABLE_LIVE_OUTREACH=false` until you have reviewed dry-run behavior.

## Host it on Streamlit Community Cloud

Use `streamlit_app.py` as the app entrypoint. Streamlit Community Cloud can deploy private GitHub repositories, but the deployed app still has a public URL, so this app requires `APP_PASSWORD` in Streamlit secrets before it will show the dashboard.

In Streamlit Cloud:

1. Create a new app from `tusharpathaknyu/job-application-agent`.
2. Set the main file path to `streamlit_app.py`.
3. In **Advanced settings → Secrets**, add:

```toml
APP_USERNAME = "tushar"
APP_PASSWORD = "choose-a-long-private-password"
OPENAI_API_KEY = "<paste-your-key-here>"
OPENAI_MODEL = "gpt-5.5"
DATABASE_PATH = "data/job_agent.db"
ARTIFACT_DIR = "data/packages"
AUTO_SUBMIT = "false"
AUTO_OUTREACH = "false"
ENABLE_LIVE_OUTREACH = "false"
```

Do not commit `.streamlit/secrets.toml`. Streamlit Community Cloud filesystem persistence is limited, so treat the SQLite database and generated packages there as working state, not your only long-term archive.

The default profile inputs are `profile/candidate_context.json` and `profile/resume_template.tex`. Override them with `CANDIDATE_CONTEXT_PATH` and `RESUME_TEMPLATE_PATH`. The context contains internal provenance notes used to prevent accidental fabrication; those notes are not included in generated application materials.

See `.env.example` for the full list of `AUTO_SUBMIT`/`AUTO_OUTREACH` and related flags — all default off. Auto-submit needs `pip install -e '.[automation]'` plus `playwright install chromium`; outreach needs `SMTP_*` set before it can send live (it stays in dry-run otherwise).

For broader funded-startup discovery, tune `STARTUP_MIN_FIT_SCORE`, `STARTUP_SEC_FORM_D_DAYS`, `STARTUP_SEC_FORM_D_LIMIT`, `STARTUP_ENRICH_MAX_COMPANIES`, and optional comma-separated `STARTUP_PORTFOLIO_URLS`. SEC Form D is noisy and mostly U.S.-only; it is used as a funding signal, then filtered by the local profile-fit scorer before any OpenAI drafting.

## Adding real submission adapters

Implement one adapter per application system (for example, Greenhouse or Lever) rather than a universal browser bot. Each adapter must:

1. Accept a package ID and approval token.
2. Call `prepare_application` immediately before acting.
3. Stop for unknown or sensitive screening questions.
4. Upload only the approved resume and cover letter.
5. Capture a real confirmation ID or confirmation page.
6. Call `mark_submitted` only after confirmation.

Keep `ENABLE_LIVE_APPLICATIONS=false` until an adapter has integration tests against a non-production test account.

Two adapters ship in `job_agent/adapters/` (Greenhouse, Lever), built with [Playwright](https://playwright.dev/) (`pip install -e '.[automation]'` then `playwright install chromium`). Both follow the six rules above: they fill the real form fields they can confidently map to your saved profile answers, and raise instead of guessing when a required screening question has no known mapping (`job_agent/adapters/base.py:KNOWN_FIELD_MAP`) — that job is marked `needs_manual_review` instead of being submitted. The adapters also verify resume/cover-letter artifacts exist before upload, skip hidden/disabled fields, support mapped text/select/radio questions, block sensitive fields such as SSN/passport/date-of-birth for manual review, and require confirmation text or a confirmation URL after a live submit click before reporting success.

## Autonomous submission and outreach

Two independent pipelines can run unattended from `python3 -m job_agent worker`, each off by default:

**Auto-submit** (`AUTO_SUBMIT=true`) — for packages scoring at or above `AUTO_SUBMIT_MIN_FIT_SCORE` (default 70, stricter than `MIN_FIT_SCORE`) on a job URL a known adapter can drive, the worker decides, approves, and calls the adapter itself — no dashboard click. Each adapter only clicks the real Submit button when **both** `ENABLE_LIVE_APPLICATIONS=true` **and** its own flag (`ENABLE_LIVE_GREENHOUSE` / `ENABLE_LIVE_LEVER`) are true; otherwise it fills the form, screenshots it, and stops (job status `dry_run_submitted`). Roll out one adapter at a time: leave both live flags off, run the worker, review the dry-run screenshots and field dumps under `data/packages/package-*/submission/`, then flip one adapter's live flag once you trust it. Cap volume with `MAX_APPLICATIONS_PER_CYCLE`.

**YC outreach** (`AUTO_OUTREACH=true`) — syncs active YC-funded companies from the public YC directory (`YC_MIN_BATCH_YEAR=2005` by default so older active companies like SnapMagic are included), scores each company locally against your hardware/software/AI profile, guesses likely hiring-alias emails from each company's domain (`founders@`, `careers@`, `jobs@`, `talent@`, `hi@`, `hello@`, `contact@`, `team@`), enriches the top `YC_ENRICH_MAX_COMPANIES` matched YC pages for any visible same-domain public email, drafts a general-purpose cold-outreach email + resume for companies scoring at least `YC_OUTREACH_MIN_FIT_SCORE` (`15` by default to include weaker-but-relevant EV/energy/hardware-adjacent companies), and sends up to `MAX_OUTREACH_PER_CYCLE` per cycle. Sends stay in dry-run (an `.eml` preview under `data/packages/outreach-*/`, nothing sent) until `ENABLE_LIVE_OUTREACH=true` and `SMTP_HOST`/`SMTP_FROM_EMAIL` are set. The dashboard's YC Outreach panel shows every company's fit score, fit reasons, draft/dry-run/sent status, the currently queued contact email, and any bounce count, and lets you trigger a send manually, respecting the same dry-run gate.

Contact discovery has two layers of validation on top of the raw guess: before drafting, `job_agent/yc_source.py:has_mx_record` runs a stdlib-only DNS MX lookup against the company's domain and skips outreach entirely for domains with no mail server at all (dead/parked sites) — no wasted OpenAI call, no doomed send. Once live sending is on, if the SMTP server rejects a specific guessed address outright (`SMTPRecipientsRefused`), that alias is marked `bounced` and the *next*-priority guess is automatically requeued (`decision` resets to `drafted`) instead of the company going permanently silent after one wrong guess; once every alias for a company has bounced, its package is marked `exhausted` and stops retrying. This only catches immediate SMTP-level rejections — it does not monitor an inbox for delayed bounce-back emails.

All of the flags above default to `false`/off — the worker is inert on these two pipelines until you opt in, the same pattern the project already uses for `ENABLE_LIVE_APPLICATIONS`. The **Automation activity** panel in the dashboard tails the audit log for every dry-run and live submission/outreach event.
