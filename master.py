#!/usr/bin/env python3
"""
Master orchestrator: infrastructure, backup import, and application deployment.

Full flow:
    python master.py --backup-zip backup.zip --existing
    python master.py --backup-zip backup.zip --new
    python master.py --backup-zip backup.zip --merge

Data modes:
    --new       Delete all Docker volumes, create fresh containers, full import
    --existing  Keep volumes, recreate containers, full replace import (default)
    --merge     Keep volumes, recreate containers, merge/upsert import

Steps:
    1. resouce-creation-script.py  — Postgres, Redis, Neo4j, Mongo, RabbitMQ, Storage
    2. import-backup.py            — restore backup.zip into databases
    3. deploy-apps.py              — agentic → mcp → claims-backend → frontends

Usage:
    python master.py --backup-zip backup.zip --existing
    python master.py --backup-zip backup.zip --new
    python master.py --backup-zip backup.zip --merge
    python master.py --backup-zip backup.zip --recreate   # alias for --existing
    python master.py --backup-zip backup.zip --skip-import
    python master.py --backup-zip backup.zip --skip-deploy
    python master.py --deploy-only
    python master.py --import-only --backup-zip backup.zip --merge
    python master.py --backup-zip backup.zip --skip-mcp
    python master.py --deploy-only --skip-mcp
    python master.py --down              # stop apps, then Docker infra
    python master.py --status            # infra + app process status
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STACK_SCRIPT = SCRIPT_DIR / "resouce-creation-script.py"
IMPORT_SCRIPT = SCRIPT_DIR / "import-backup.py"
DEPLOY_SCRIPT = SCRIPT_DIR / "deploy-apps.py"


def run_script(script: Path, args: list[str]) -> int:
    result = subprocess.run([sys.executable, str(script), *args])
    return result.returncode


def volume_mode_for(data_mode: str) -> str:
    """Infra volume handling: only --new wipes volumes."""
    return "new" if data_mode == "new" else "existing"


def main():
    parser = argparse.ArgumentParser(
        description="Start infra, import backup, and deploy UHC applications.",
    )
    parser.add_argument(
        "--backup-zip",
        help="Path to backup.zip (required for import unless --skip-import)",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--new", dest="data_mode", action="store_const", const="new",
        help="wipe all data volumes, create fresh containers, full import",
    )
    mode_group.add_argument(
        "--merge", dest="data_mode", action="store_const", const="merge",
        help="keep volumes, merge/upsert backup data into existing databases",
    )
    mode_group.add_argument(
        "--existing", dest="data_mode", action="store_const", const="existing",
        help="keep volumes, full replace import from backup (default)",
    )
    mode_group.add_argument(
        "--recreate", dest="data_mode", action="store_const", const="existing",
        help="alias for --existing (backward compatible)",
    )
    parser.set_defaults(data_mode="existing")

    parser.add_argument("--down", action="store_true",
                        help="stop local app processes, then stop & remove infra containers")
    parser.add_argument("--status", action="store_true",
                        help="show running containers")
    parser.add_argument("--skip-import", action="store_true",
                        help="skip backup import")
    parser.add_argument("--skip-deploy", action="store_true",
                        help="skip application deployment")
    parser.add_argument("--skip-mcp", action="store_true",
                        help="skip MCP server during application deployment")
    parser.add_argument("--import-only", action="store_true",
                        help="only run backup import")
    parser.add_argument("--deploy-only", action="store_true",
                        help="only deploy applications (skip infra + import)")
    args = parser.parse_args()

    if args.down:
        code = run_script(DEPLOY_SCRIPT, ["--down"])
        if code != 0:
            return code
        return run_script(STACK_SCRIPT, ["--down"])

    if args.status:
        run_script(STACK_SCRIPT, ["--status"])
        return run_script(DEPLOY_SCRIPT, ["--status"])

    if args.deploy_only:
        deploy_args = ["--recreate"]
        if args.skip_mcp:
            deploy_args.append("--skip-mcp")
        return run_script(DEPLOY_SCRIPT, deploy_args)

    if not args.import_only:
        stack_args = [
            "--recreate",
            f"--volume-mode={volume_mode_for(args.data_mode)}",
        ]
        print(f"\nData mode: {args.data_mode}\n")
        code = run_script(STACK_SCRIPT, stack_args)
        if code != 0:
            return code

    if not args.skip_import:
        if not args.backup_zip:
            print("ERROR: --backup-zip is required for import.")
            return 1
        backup_zip = Path(args.backup_zip).expanduser()
        if not backup_zip.is_file():
            print(f"ERROR: backup zip not found: {backup_zip}")
            return 1
        import_args = [
            "--backup-zip", str(backup_zip),
            f"--import-mode={args.data_mode}",
        ]
        code = run_script(IMPORT_SCRIPT, import_args)
        if code != 0:
            return code
    elif not args.import_only:
        print("\nSkipping backup import (--skip-import).")

    if args.import_only:
        if not args.backup_zip:
            print("ERROR: --backup-zip is required for import.")
            return 1
        backup_zip = Path(args.backup_zip).expanduser()
        if not backup_zip.is_file():
            print(f"ERROR: backup zip not found: {backup_zip}")
            return 1
        return run_script(IMPORT_SCRIPT, [
            "--backup-zip", str(backup_zip),
            f"--import-mode={args.data_mode}",
        ])

    if args.skip_deploy:
        print("\nSkipping application deployment (--skip-deploy).")
        return 0

    deploy_args = ["--recreate"]
    if args.skip_mcp:
        deploy_args.append("--skip-mcp")
    return run_script(DEPLOY_SCRIPT, deploy_args)


if __name__ == "__main__":
    sys.exit(main())
