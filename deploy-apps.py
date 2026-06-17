#!/usr/bin/env python3
"""
Start UHC application services as local background processes.

Infra (Postgres, Redis, Neo4j, Mongo, RabbitMQ, Storage) still runs in Docker.
Apps run directly on the host (Python/Node) with logs in each service folder.

Deploy order:
  1. agentic-backend  (pip install, celery worker, django runserver)
  2. claims-backend   (npm install, npm run dev)
  3. audit-dashboard  (node serve-dist.mjs)
  4. claims-frontend  (node serve-dist.mjs)
  5. mcp-server       (npm install, npm run dev:rest)

Usage:
    python deploy-apps.py
    python deploy-apps.py --recreate
    python deploy-apps.py --skip-mcp
    python deploy-apps.py --down
    python deploy-apps.py --status
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from stack_config import (
    APP_PORTS,
    APP_SERVICES,
    DEV_ENV_ROOT,
    LOCAL_APP_FOLDERS,
    SERVICES,
    claims_database_url,
)

SCRIPT_DIR = Path(__file__).resolve().parent
IS_WINDOWS = sys.platform.startswith("win")


def npm() -> str:
    return "npm.cmd" if IS_WINDOWS else "npm"


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
    required = [svc["name"] for svc in SERVICES if not container_running(svc["name"])]
    if required:
        print(f"ERROR: infrastructure containers not running: {', '.join(required)}")
        print("       Run master.py or resouce-creation-script.py first.")
        return False
    return True


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def merged_env(app: dict) -> dict[str, str]:
    env = parse_env_file(Path(app["env_file"]))
    env.update(app.get("env_overrides", {}))
    if app["name"] == "claims-backend":
        env["DATABASE_URL"] = claims_database_url()
    return env


def write_env_file(path: Path, env: dict[str, str]):
    lines = [f"{key}={value}" for key, value in env.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolve_service_dir(app_name: str) -> Path | None:
    candidates = LOCAL_APP_FOLDERS.get(app_name, [app_name])
    for name in candidates:
        path = Path(DEV_ENV_ROOT) / name
        if path.is_dir():
            return path
    return None


def pid_file(service_dir: Path, process_name: str) -> Path:
    return service_dir / f".deploy-{process_name}.pid"


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        result = run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture=True,
            check=False,
        )
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


def stop_service_processes(service_dir: Path, process_names: list[str]):
    for name in process_names:
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
            print(f"  ! could not stop {name} (pid {pid}) — kill manually")
        pf.unlink(missing_ok=True)


def start_background(
    service_dir: Path,
    process_name: str,
    cmd: list[str],
    log_name: str,
    env: dict[str, str] | None = None,
) -> bool:
    log_path = service_dir / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)

    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)

    popen_kwargs: dict = {
        "cwd": service_dir,
        "env": proc_env,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except OSError as exc:
        log_file.close()
        print(f"  ✗ failed to start {process_name}: {exc}")
        return False

    pid_file(service_dir, process_name).write_text(str(proc.pid), encoding="utf-8")
    print(f"  ✓ started {process_name} (pid {proc.pid}) → {log_name}")
    return True


def run_setup(cmd: list[str], *, cwd: Path, label: str) -> bool:
    print(f"  • {label} ...")
    result = run(cmd, cwd=cwd, capture=True, check=False)
    if result.returncode == 0:
        print(f"  ✓ {label}")
        return True
    err = (result.stderr or result.stdout or "").strip()
    print(f"  ✗ {label} failed:\n{err}")
    return False


def wait_for_url(url: str, timeout: int = 180) -> bool:
    print(f"  • waiting for {url} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if 200 <= resp.status < 300:
                    print("  ✓ ready")
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2)
    print(f"  ✗ not ready after {timeout}s")
    return False


def run_agentic_post_start(service_dir: Path, env: dict[str, str], *, skip_mcp: bool) -> bool:
    ok = True
    base = [sys.executable]
    shell_env = agentic_runtime_env(env)

    print("  • running Django migrations ...")
    migrate = run(
        [*base, "manage.py", "migrate", "--noinput"],
        cwd=service_dir,
        env=shell_env,
        capture=True,
        check=False,
    )
    if migrate.returncode != 0:
        print(f"  ✗ migrate failed:\n{(migrate.stderr or '').strip()}")
        ok = False
    else:
        print("  ✓ migrations complete")

    print("  • seeding builder catalog ...")
    seed = run(
        [*base, "manage.py", "seed_builder_catalog"],
        cwd=service_dir,
        env=shell_env,
        capture=True,
        check=False,
    )
    if seed.returncode != 0:
        print(f"  ! seed_builder_catalog warning:\n{(seed.stderr or '').strip()}")
    else:
        print("  ✓ builder catalog seeded")

    if skip_mcp:
        print("  • skipping MCP config (--skip-mcp)")
        return ok

    print("  • configuring MCP server endpoint in Postgres ...")
    shell_cmd = (
        "from agent_tools.models import McpServerConfig; "
        "McpServerConfig.objects.update_or_create("
        "label='local-mcp-server', "
        "defaults={'base_url':'http://localhost:3000','auth_header':'x-api-key',"
        "'api_key':'','http_method':'POST','claim_arg':'claim_number',"
        "'timeout_seconds':30,'is_active':True}); "
        "print('mcp config ok')"
    )
    mcp = run(
        [*base, "manage.py", "shell", "-c", shell_cmd],
        cwd=service_dir,
        env=shell_env,
        capture=True,
        check=False,
    )
    if mcp.returncode != 0:
        print(f"  ! mcp config warning:\n{(mcp.stderr or '').strip()}")
    else:
        print("  ✓ MCP config saved")
    return ok


def run_prisma_migrate(service_dir: Path, env: dict[str, str]) -> bool:
    print("  • running Prisma migrations ...")
    proc_env = os.environ.copy()
    proc_env.update(env)
    result = run(
        ["npx", "prisma", "migrate", "deploy"],
        cwd=service_dir,
        env=proc_env,
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"  ✗ prisma migrate failed:\n{(result.stderr or '').strip()}")
        return False
    print("  ✓ prisma migrations complete")
    return True


def prepare_service_env(app: dict, service_dir: Path) -> dict[str, str]:
    env = merged_env(app)
    write_env_file(service_dir / ".env", env)
    return env


def agentic_runtime_env(env: dict[str, str]) -> dict[str, str]:
    """Env vars required for Django/Celery on Windows and Unix."""
    runtime = os.environ.copy()
    runtime.update(env)
    runtime["PYTHONPATH"] = "."
    runtime["DJANGO_SETTINGS_MODULE"] = "sop_backend.settings"
    return runtime


def deploy_agentic(*, recreate: bool, skip_mcp: bool) -> bool:
    app = next(a for a in APP_SERVICES if a["name"] == "agentic-backend")
    service_dir = resolve_service_dir("agentic-backend")
    if service_dir is None:
        print("  ✗ folder not found: dev-env/agentic-backend")
        return False

    process_names = ["celery", "django"]
    if recreate:
        stop_service_processes(service_dir, process_names)

    env = prepare_service_env(app, service_dir)
    if not run_setup(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        cwd=service_dir,
        label="pip install -r requirements.txt",
    ):
        return False

    if not run_agentic_post_start(service_dir, env, skip_mcp=skip_mcp):
        return False

    runtime_env = agentic_runtime_env(env)
    celery_cmd = [
        sys.executable, "-m", "celery", "-A", "sop_backend", "worker",
        "-Q", "job_queue,celery",
        "--concurrency=4", "-l", "INFO",
    ]
    ok = start_background(service_dir, "celery", celery_cmd, "celery.log", runtime_env)
    ok = start_background(
        service_dir,
        "django",
        [sys.executable, "manage.py", "runserver", "0.0.0.0:8000"],
        "agentic-backend.log",
        runtime_env,
    ) and ok

    if ok and app.get("health_url"):
        ok = wait_for_url(app["health_url"])
    return ok


def deploy_claims_backend(*, recreate: bool) -> bool:
    app = next(a for a in APP_SERVICES if a["name"] == "claims-backend")
    service_dir = resolve_service_dir("claims-backend")
    if service_dir is None:
        print("  ✗ folder not found: dev-env/claims-backend")
        return False

    if recreate:
        stop_service_processes(service_dir, ["nodebackend"])

    env = prepare_service_env(app, service_dir)
    if not run_setup([npm(), "install"], cwd=service_dir, label="npm install"):
        return False
    if not run_prisma_migrate(service_dir, env):
        return False

    ok = start_background(
        service_dir,
        "nodebackend",
        [npm(), "run", "dev"],
        "nodebackend.log",
        env,
    )
    if ok and app.get("health_url"):
        ok = wait_for_url(app["health_url"])
    return ok


def deploy_node_static(app_name: str, *, script: str, process_name: str, log_name: str, recreate: bool) -> bool:
    app = next(a for a in APP_SERVICES if a["name"] == app_name)
    service_dir = resolve_service_dir(app_name)
    if service_dir is None:
        print(f"  ✗ folder not found for {app_name} under dev-env/")
        return False

    if recreate:
        stop_service_processes(service_dir, [process_name])

    env = prepare_service_env(app, service_dir)
    script_path = service_dir / script
    if not script_path.is_file():
        print(f"  ✗ script not found: {script_path}")
        return False

    ok = start_background(
        service_dir,
        process_name,
        ["node", script],
        log_name,
        env,
    )
    if ok and app.get("health_url"):
        ok = wait_for_url(app["health_url"])
    return ok


def deploy_mcp(*, recreate: bool) -> bool:
    app = next(a for a in APP_SERVICES if a["name"] == "mcp-server")
    service_dir = resolve_service_dir("mcp-server")
    if service_dir is None:
        print("  ✗ folder not found: dev-env/claims-tools-mcp-server")
        return False

    if recreate:
        stop_service_processes(service_dir, ["mcp"])

    env = prepare_service_env(app, service_dir)
    if not run_setup([npm(), "install"], cwd=service_dir, label="npm install"):
        return False

    ok = start_background(
        service_dir,
        "mcp",
        [npm(), "run", "dev:rest"],
        "mcp-server.log",
        env,
    )
    if ok and app.get("health_url"):
        ok = wait_for_url(app["health_url"])
    return ok


def deploy_apps(*, recreate: bool = False, skip_mcp: bool = False) -> int:
    if not ensure_infra_running():
        return 1

    print("\nDeploying application services (local processes) ...")
    if skip_mcp:
        print("(MCP server will be skipped)\n")
    else:
        print()

    ok = True

    print("[agentic-backend]")
    ok = deploy_agentic(recreate=recreate, skip_mcp=skip_mcp) and ok

    print("\n[claims-backend]")
    ok = deploy_claims_backend(recreate=recreate) and ok

    print("\n[audit-dashboard]")
    ok = deploy_node_static(
        "audit-dashboard",
        script="serve-dist.mjs",
        process_name="audit",
        log_name="audit-dashboard.log",
        recreate=recreate,
    ) and ok

    print("\n[claims-frontend]")
    ok = deploy_node_static(
        "claims-frontend",
        script="serve-dist.mjs",
        process_name="frontend",
        log_name="claims-frontend.log",
        recreate=recreate,
    ) and ok

    if not skip_mcp:
        print("\n[mcp-server]")
        ok = deploy_mcp(recreate=recreate) and ok

    print_summary(ok, skip_mcp=skip_mcp)
    return 0 if ok else 1


def cmd_down():
    print("Stopping local application processes ...")
    stops = [
        ("agentic-backend", ["celery", "django"]),
        ("claims-backend", ["nodebackend"]),
        ("audit-dashboard", ["audit"]),
        ("claims-frontend", ["frontend"]),
        ("mcp-server", ["mcp"]),
    ]
    for app_name, processes in stops:
        service_dir = resolve_service_dir(app_name)
        if service_dir is None:
            continue
        print(f"[{app_name}]")
        stop_service_processes(service_dir, processes)
    print("\nDone. Local application processes stopped.")


def cmd_status():
    print("Local application process status:\n")
    checks = [
        ("agentic-backend", ["celery", "django"]),
        ("claims-backend", ["nodebackend"]),
        ("audit-dashboard", ["audit"]),
        ("claims-frontend", ["frontend"]),
        ("mcp-server", ["mcp"]),
    ]
    for app_name, processes in checks:
        service_dir = resolve_service_dir(app_name)
        if service_dir is None:
            print(f"  {app_name:18} — folder not found")
            continue
        parts = []
        for proc in processes:
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
        print(f"  {app_name:18} {', '.join(parts)}")


def print_summary(ok: bool, *, skip_mcp: bool = False):
    print("\n" + "─" * 60)
    if ok:
        print("Applications started (local processes).\n")
        print(f"  Agentic backend   http://localhost:{APP_PORTS['agentic-backend']}/api/ingest/health/")
        print(f"    logs            dev-env/agentic-backend/agentic-backend.log, celery.log")
        print(f"  Claims backend    http://localhost:{APP_PORTS['claims-backend']}/health")
        print(f"    logs            dev-env/claims-backend/nodebackend.log")
        print(f"  Claims frontend   http://localhost:{APP_PORTS['claims-frontend']}/")
        print(f"    logs            dev-env/claims-frontend/claims-frontend.log")
        print(f"  Audit dashboard   http://localhost:{APP_PORTS['audit-dashboard']}/")
        print(f"    logs            dev-env/audit-review-dashboard/audit-dashboard.log")
        if skip_mcp:
            print("  MCP server        (skipped)")
        else:
            print(f"  MCP server        http://localhost:{APP_PORTS['mcp-server']}/health")
            print(f"    logs            dev-env/claims-tools-mcp-server/mcp-server.log")
        print("\nUseful:")
        print("  python deploy-apps.py --status")
        print("  python deploy-apps.py --down")
    else:
        print("Application deployment finished with errors.")
    print("─" * 60)


def main():
    parser = argparse.ArgumentParser(description="Deploy UHC applications as local processes.")
    parser.add_argument("--skip-mcp", action="store_true",
                        help="skip MCP server")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--recreate", action="store_true",
                       help="stop and restart application processes")
    group.add_argument("--down", action="store_true",
                       help="stop local application processes")
    group.add_argument("--status", action="store_true",
                       help="show local process status")
    args = parser.parse_args()

    if args.down:
        cmd_down()
        return 0
    if args.status:
        cmd_status()
        return 0
    return deploy_apps(recreate=args.recreate, skip_mcp=args.skip_mcp)


if __name__ == "__main__":
    sys.exit(main())
