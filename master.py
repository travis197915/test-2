#!/usr/bin/env python3
"""
Master orchestrator: infrastructure, backup import, and application deployment.

Configuration: .env next to this script (copy from .env.example)
Deploy steps: deployment-ref.txt

Examples:
    python master.py --infra all --recreate
    python master.py --infra postgres,mongo --recreate --new
    python master.py --import all --backup-zip backup.zip --existing
    python master.py --import mongo --backup-zip backup.zip --merge
    python master.py --deploy all
    python master.py --deploy agentic_backend
    python master.py --stop mcp_server
    python master.py --down

Full stack:
    python master.py --infra all --import all --deploy all --backup-zip backup.zip --new
"""

import argparse
import subprocess
import sys
from pathlib import Path

import config

SCRIPT_DIR = Path(__file__).resolve().parent
STACK_SCRIPT = SCRIPT_DIR / "resouce-creation-script.py"
IMPORT_SCRIPT = SCRIPT_DIR / "import-backup.py"
DEPLOY_SCRIPT = SCRIPT_DIR / "deploy-apps.py"


def run_script(script: Path, args: list[str]) -> int:
    return subprocess.run([sys.executable, str(script), *args]).returncode


def volume_mode_for(data_mode: str) -> str:
    return "new" if data_mode == "new" else "existing"


def main():
    parser = argparse.ArgumentParser(description="UHC local stack orchestrator")
    parser.add_argument("--backup-zip", help="Path to backup.zip for import steps")

    parser.add_argument("--infra", metavar="TARGETS",
                        help="infra services to (re)create: postgres,mongo,... or all")
    parser.add_argument("--import", dest="import_targets", metavar="TARGETS",
                        help="import targets: postgres,mongo,neo4j or all")
    parser.add_argument("--deploy", metavar="REPO", help="deploy repo or all")
    parser.add_argument("--stop", metavar="REPO", help="stop repo process or all")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--new", dest="data_mode", action="store_const", const="new")
    mode_group.add_argument("--merge", dest="data_mode", action="store_const", const="merge")
    mode_group.add_argument("--existing", dest="data_mode", action="store_const", const="existing")
    mode_group.add_argument("--recreate", dest="data_mode", action="store_const", const="existing",
                            help="alias for --existing")
    parser.set_defaults(data_mode="existing")

    parser.add_argument("--down", action="store_true",
                        help="stop all repos, then remove all infra containers")
    parser.add_argument("--status", action="store_true", help="show infra + repo status")
    parser.add_argument("--skip-mcp-config", action="store_true",
                        help="skip automatic MCP config during agentic deploy")
    args = parser.parse_args()

    if not config.ENV_FILE.is_file():
        print(f"ERROR: missing {config.ENV_FILE}")
        print("       Copy .env.example to .env and fill in paths + credentials.")
        return 1

    if args.down:
        code = run_script(DEPLOY_SCRIPT, ["--stop", "all"])
        if code != 0:
            return code
        return run_script(STACK_SCRIPT, ["--down", "--services", "all"])

    if args.status:
        run_script(STACK_SCRIPT, ["--status"])
        return run_script(DEPLOY_SCRIPT, ["--status"])

    any_action = any([args.infra, args.import_targets, args.deploy, args.stop])
    if not any_action:
        parser.print_help()
        return 1

    exit_code = 0

    if args.infra:
        try:
            services = config.parse_target_list(args.infra, allowed=config.INFRA_SERVICES, label="service")
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1
        stack_args = [
            "--recreate",
            "--services", ",".join(services),
            f"--volume-mode={volume_mode_for(args.data_mode)}",
        ]
        print(f"\n=== Infra ({', '.join(services)}) mode={args.data_mode} ===\n")
        code = run_script(STACK_SCRIPT, stack_args)
        if code != 0:
            exit_code = code

    if args.import_targets:
        if not args.backup_zip:
            print("ERROR: --backup-zip is required for --import")
            return 1
        backup_zip = Path(args.backup_zip).expanduser()
        if not backup_zip.is_file():
            print(f"ERROR: backup zip not found: {backup_zip}")
            return 1
        try:
            targets = config.parse_target_list(
                args.import_targets, allowed=config.IMPORT_TARGETS, label="import target",
            )
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1
        import_args = [
            "--backup-zip", str(backup_zip),
            f"--import-mode={args.data_mode}",
            "--targets", ",".join(targets),
        ]
        print(f"\n=== Import ({', '.join(targets)}) mode={args.data_mode} ===\n")
        code = run_script(IMPORT_SCRIPT, import_args)
        if code != 0:
            exit_code = code

    if args.deploy:
        deploy_args = ["--deploy", args.deploy]
        if args.skip_mcp_config:
            deploy_args.append("--skip-mcp-config")
        print(f"\n=== Deploy ({args.deploy}) ===\n")
        code = run_script(DEPLOY_SCRIPT, deploy_args)
        if code != 0:
            exit_code = code

    if args.stop:
        print(f"\n=== Stop ({args.stop}) ===\n")
        code = run_script(DEPLOY_SCRIPT, ["--stop", args.stop])
        if code != 0:
            exit_code = code

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
