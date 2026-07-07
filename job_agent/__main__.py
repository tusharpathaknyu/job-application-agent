from __future__ import annotations

import argparse
import json
import time

from .config import Settings
from .db import Database
from .server import serve
from .service import JobAgentService


def main() -> None:
    parser = argparse.ArgumentParser(description="Approval-first job application agent")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the local review dashboard")
    subparsers.add_parser("sync", help="Discover jobs once")
    subparsers.add_parser("yc-sync", help="Sync and rank YC companies for outreach")
    subparsers.add_parser("yc-outreach", help="Draft/send dry-run YC outreach for ranked companies")
    subparsers.add_parser("startup-sync", help="Sync and rank free funded-startup sources")
    subparsers.add_parser("startup-outreach", help="Draft outreach for ranked funded startups")
    subparsers.add_parser("worker", help="Discover jobs on a recurring interval")
    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command in {None, "serve"}:
        serve(settings)
        return

    service = JobAgentService(settings, Database(settings.database_path))
    if args.command == "sync":
        print(json.dumps(service.sync_jobs(), indent=2))
        return

    if args.command == "yc-sync":
        print(json.dumps(service.sync_yc_companies(), indent=2))
        return

    if args.command == "yc-outreach":
        print(json.dumps(service.run_outreach_cycle(), indent=2))
        return

    if args.command == "startup-sync":
        print(json.dumps(service.sync_startups(), indent=2))
        return

    if args.command == "startup-outreach":
        print(json.dumps(service.run_startup_outreach_cycle(), indent=2))
        return

    if args.command == "worker":
        while True:
            try:
                print(json.dumps(service.run_automatic_cycle()), flush=True)
            except Exception as error:
                print(json.dumps({"error": str(error)}), flush=True)
            time.sleep(settings.sync_interval_minutes * 60)


if __name__ == "__main__":
    main()
