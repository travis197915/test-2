#!/usr/bin/env python3
"""
Deploy UHC application repos as local background processes using deployment-ref.txt.

Repo .env files are never modified — you manage them manually in each repo folder.

Usage:
    python deploy-apps.py --deploy all
    python deploy-apps.py --deploy agentic_backend
    python deploy-apps.py --stop all
    python deploy-apps.py --stop mcp_server
    python deploy-apps.py --status
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import config
from config import REPO_NAMES, mcp_server_url
from deploy_ref import DeployStep, background_process_names, parse_deployment_ref

IS_WINDOWS = sys.platform.startswith("win")


def run(cmd, *, cwd=None, env=None, capture=False, check=True):
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def out(cmd, *, cwd=None):
    result = run(cmd, cwd=cwd, capture=True, check=False)
    return (result.stdout or "").strip()


def container_running(name: str) -> bool:
    return name in out(["docker", "ps", "--format", "{{.Names}}"]).splitlines()


def ensure_infra_running() -> bool:
    required = [svc["name"] for svc in config.services() if not container_running(svc["name"])]
    if required:
        print(f"ERROR: infrastructure containers not running: {', '.join(required)}")
        print("       Run: python master.py --infra all --recreate")
        return False
    return True


def pid_file(service_dir: Path, process_name: str) -> Path:
    return service_dir / f".deploy-{process_name}.pid"


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        result = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture=True, check=False)
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_pid(pid: int) -> bool:
    if not is_pid_running(pid):
        return True
    if IS_WINDOWS:
        run(["taskkill", "/F", "/T", "/PID", str(pid)], check=False)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    time.sleep(1)
    return not is_pid_running(pid)


def stop_repo_processes(repo: str, service_dir: Path):
    for name in background_process_names(repo):
        pf = pid_file(service_dir, name)
        if not pf.is_file():
            continue
        try:
            pid = int(pf.read_text(encoding="utf-8").strip())
        except ValueError:
            pf.unlink(missing_ok=True)
            continue
        if stop_pid(pid):
            print(f"  • stopped {name} (pid {pid})")
        else:
            print(f"  ! could not stop {name} (pid {pid})")
        pf.unlink(missing_ok=True)


def repo_runtime_env(repo: str) -> dict[str, str]:
    env = os.environ.copy()
    if repo == "agentic_backend":
        env["PYTHONPATH"] = "."
        env["DJANGO_SETTINGS_MODULE"] = "sop_backend.settings"
    return env


def run_foreground_step(service_dir: Path, step: DeployStep, env: dict[str, str]) -> bool:
    print(f"  • {step.command} ...")
    result = subprocess.run(
        step.command,
        shell=True,
        cwd=service_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode == 0:
        print("  ✓ done")
        return True
    err = (result.stderr or result.stdout or "").strip()
    print(f"  ✗ failed:\n{err}")
    return False


def start_background(service_dir: Path, process_name: str, command: str, log_name: str, env: dict[str, str]) -> bool:
    log_path = service_dir / log_name
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    popen_kwargs: dict = {
        "cwd": service_dir,
        "env": env,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "shell": True,
    }
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        log_file.close()
        print(f"  ✗ failed to start {process_name}: {exc}")
        return False
    pid_file(service_dir, process_name).write_text(str(proc.pid), encoding="utf-8")
    print(f"  ✓ started {process_name} (pid {proc.pid}) → {log_name}")
    return True


def run_agentic_automatic_steps(service_dir: Path, env: dict[str, str], *, configure_mcp: bool) -> bool:
    base = [sys.executable]
    print("  • automatic: seed_builder_catalog ...")
    seed = run(
        [*base, "manage.py", "seed_builder_catalog"],
        cwd=service_dir,
        env=env,
        capture=True,
        check=False,
    )
    if seed.returncode != 0:
        print(f"  ! seed_builder_catalog warning:\n{(seed.stderr or '').strip()}")
    else:
        print("  ✓ builder catalog seeded")

    if not configure_mcp:
        print("  • automatic: skipping MCP config")
        return True

    mcp_url = mcp_server_url()
    print(f"  • automatic: configuring MCP endpoint ({mcp_url}) in Postgres ...")
    shell_cmd = (
        "from agent_tools.models import McpServerConfig; "
        "McpServerConfig.objects.update_or_create("
        "label='local-mcp-server', "
        f"defaults={{'base_url':'{mcp_url}','auth_header':'x-api-key',"
        "'api_key':'','http_method':'POST','claim_arg':'claim_number',"
        "'timeout_seconds':30,'is_active':True}); "
        "print('mcp config ok')"
    )
    mcp = run(
        [*base, "manage.py", "shell", "-c", shell_cmd],
        cwd=service_dir,
        env=env,
        capture=True,
        check=False,
    )
    if mcp.returncode != 0:
        print(f"  ! MCP config warning:\n{(mcp.stderr or '').strip()}")
    else:
        print("  ✓ MCP config saved")
    return True


def deploy_repo(repo: str, steps: list[DeployStep], *, configure_mcp: bool = True) -> bool:
    try:
        service_dir = config.repo_path(repo)
    except ValueError as exc:
        print(f"  ✗ {exc}")
        return False

    if not service_dir.is_dir():
        print(f"  ✗ repo path not found: {service_dir}")
        return False

    print(f"[{repo}]  ({service_dir})")
    stop_repo_processes(repo, service_dir)
    env = repo_runtime_env(repo)

    foreground = [step for step in steps if not step.background]
    background = [step for step in steps if step.background]

    for step in foreground:
        if not run_foreground_step(service_dir, step, env):
            return False

    if repo == "agentic_backend":
        run_agentic_automatic_steps(service_dir, env, configure_mcp=configure_mcp)

    ok = True
    for step in background:
        ok = start_background(service_dir, step.process_name, step.command, step.log_file, env) and ok
    return ok


def deploy_repos(repos: list[str], *, configure_mcp: bool = True) -> int:
    if not ensure_infra_running():
        return 1

    try:
        ref = parse_deployment_ref()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("\nDeploying repositories from deployment-ref.txt ...\n")
    failures: list[str] = []
    for repo in repos:
        ok = deploy_repo(repo, ref[repo], configure_mcp=configure_mcp)
        if not ok:
            failures.append(repo)
            print(f"  ! fix {repo} and re-run: python master.py --deploy {repo}")
        print()

    if failures:
        print("Deployment finished with errors:")
        for repo in failures:
            print(f"  ✗ {repo}")
        return 1

    print("Deployment finished successfully.")
    return 0


def stop_repos(repos: list[str]) -> int:
    print("Stopping repository processes ...\n")
    for repo in repos:
        try:
            service_dir = config.repo_path(repo)
        except ValueError as exc:
            print(f"[{repo}]  ✗ {exc}")
            continue
        if not service_dir.is_dir():
            print(f"[{repo}]  ✗ path not found: {service_dir}")
            continue
        print(f"[{repo}]")
        stop_repo_processes(repo, service_dir)
    print("\nDone.")
    return 0


def cmd_status():
    print("Repository process status:\n")
    for repo in REPO_NAMES:
        try:
            service_dir = config.repo_path(repo)
        except ValueError as exc:
            print(f"  {repo:30} — {exc}")
            continue
        if not service_dir.is_dir():
            print(f"  {repo:30} — path not found")
            continue
        parts = []
        for proc in background_process_names(repo):
            pf = pid_file(service_dir, proc)
            if pf.is_file():
                try:
                    pid = int(pf.read_text(encoding="utf-8").strip())
                    status = "running" if is_pid_running(pid) else "stopped"
                    parts.append(f"{proc}={status}(pid {pid})")
                except ValueError:
                    parts.append(f"{proc}=unknown")
            else:
                parts.append(f"{proc}=not started")
        print(f"  {repo:30} {', '.join(parts)}")


def main():
    parser = argparse.ArgumentParser(description="Deploy UHC repos from deployment-ref.txt")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--deploy", metavar="REPO", help="repo name or all")
    group.add_argument("--stop", metavar="REPO", help="repo name or all")
    group.add_argument("--status", action="store_true", help="show process status")
    parser.add_argument("--skip-mcp-config", action="store_true",
                        help="skip automatic MCP config step for agentic_backend")
    args = parser.parse_args()

    if args.status:
        cmd_status()
        return 0

    try:
        repos = config.parse_target_list(
            args.deploy if args.deploy else args.stop,
            allowed=REPO_NAMES,
            label="repo",
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    if args.stop:
        return stop_repos(repos)
    return deploy_repos(repos, configure_mcp=not args.skip_mcp_config)


if __name__ == "__main__":
    sys.exit(main())
