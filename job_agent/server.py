from __future__ import annotations

import base64
import hmac
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .db import Database
from .service import ApprovalError, JobAgentService


STATIC_DIR = Path(__file__).with_name("static")
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def require_local_host(host: str) -> None:
    if host not in LOCAL_HOSTS:
        raise ValueError(
            "This personal agent is local-only. HOST must be 127.0.0.1 or localhost."
        )


class ApiHandler(BaseHTTPRequestHandler):
    service: JobAgentService
    settings: Settings

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        if not self.settings.hosted_mode:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header.removeprefix("Basic "), validate=True).decode("utf-8")
        except Exception:
            return False
        username, separator, password = decoded.partition(":")
        if not separator:
            return False
        return hmac.compare_digest(username, self.settings.app_username) and hmac.compare_digest(
            password, self.settings.app_password
        )

    def _require_auth(self, path: str) -> bool:
        if path == "/api/health" or self._auth_ok():
            return True
        body = b"Authentication required"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Personal Job Agent"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("Request is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _static(self, name: str) -> None:
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download(self, path: Path, content_type: str, filename: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not self._require_auth(path):
            return
        if path == "/":
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path.removeprefix("/static/"))
        if path == "/api/health":
            return self._json(200, {"ok": True})
        if path == "/api/profile":
            return self._json(200, self.service.get_profile())
        if path == "/api/context":
            return self._json(200, self.service.get_context_summary())
        if path == "/api/jobs":
            return self._json(200, self.service.list_jobs())
        if path == "/api/yc/companies":
            return self._json(200, self.service.list_yc_companies())
        if path == "/api/startups":
            return self._json(200, self.service.list_startups())
        if path == "/api/outreach":
            return self._json(200, self.service.list_outreach())
        if path == "/api/automation/log":
            return self._json(200, self.service.get_automation_log())
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["api", "packages"] and parts[3] == "artifacts":
            package_id = int(parts[2])
            artifact = self.service.get_artifact(package_id, parts[4])
            if not artifact:
                return self._json(404, {"error": "Artifact not found"})
            artifact_path, content_type = artifact
            return self._download(artifact_path, content_type, parts[4])
        if path.startswith("/api/packages/"):
            package_id = int(path.rsplit("/", 1)[-1])
            package = self.service.get_package(package_id)
            return self._json(200 if package else 404, package or {"error": "Package not found"})
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._require_auth(path):
            return
        try:
            data = self._body()
            if path == "/api/profile":
                return self._json(200, self.service.save_profile(data))
            if path == "/api/jobs/sync":
                return self._json(200, self.service.sync_jobs())
            if path == "/api/jobs/manual":
                return self._json(201, self.service.add_manual_job(data))
            if path.startswith("/api/jobs/") and path.endswith("/tailor"):
                job_id = int(path.split("/")[3])
                return self._json(200, self.service.tailor_job(job_id))
            if path.startswith("/api/packages/") and path.endswith("/decision"):
                package_id = int(path.split("/")[3])
                return self._json(200, self.service.decide(package_id, str(data.get("decision", ""))))
            if path.startswith("/api/packages/") and path.endswith("/prepare"):
                package_id = int(path.split("/")[3])
                return self._json(200, self.service.prepare_application(package_id, str(data.get("approval_token", ""))))
            if path == "/api/yc/sync":
                return self._json(200, self.service.sync_yc_companies())
            if path == "/api/startups/sync":
                return self._json(200, self.service.sync_startups())
            if path.startswith("/api/startups/") and path.endswith("/outreach"):
                company_id = int(path.split("/")[3])
                return self._json(200, self.service.generate_startup_outreach(company_id))
            if path.startswith("/api/yc/") and path.endswith("/outreach"):
                company_id = int(path.split("/")[3])
                return self._json(200, self.service.generate_outreach(company_id))
            if path.startswith("/api/outreach/") and path.endswith("/send"):
                outreach_id = int(path.split("/")[3])
                return self._json(200, self.service.send_outreach(outreach_id))
            self._json(404, {"error": "Not found"})
        except ApprovalError as error:
            self._json(403, {"error": str(error)})
        except (ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            self._json(500, {"error": str(error)})


def serve(settings: Settings) -> None:
    if settings.hosted_mode:
        if not settings.app_password:
            raise ValueError("APP_PASSWORD is required when HOSTED_MODE=true")
    else:
        require_local_host(settings.host)
    database = Database(settings.database_path)
    ApiHandler.service = JobAgentService(settings, database)
    ApiHandler.settings = settings
    server = ThreadingHTTPServer((settings.host, settings.port), ApiHandler)
    print(f"Job agent running at http://{settings.host}:{settings.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
