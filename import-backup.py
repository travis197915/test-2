#!/usr/bin/env python3
"""
Unzip a backup archive and restore Postgres, MongoDB, and Neo4j into the
local Docker stack defined in stack_config.py.

Backup layout is flexible — folder names may differ, but each archive must
contain:
  - at least one .sql file          (PostgreSQL pg_dump)
  - a mongodump folder              (prelude.json + database subdirs)
  - a neo4j.dump file               (Neo4j admin dump)

Usage:
    python import-backup.py --backup-zip /path/to/backup.zip
    python import-backup.py --backup-zip backup.zip --import-mode existing
    python import-backup.py --backup-zip backup.zip --import-mode new
    python import-backup.py --backup-zip backup.zip --import-mode merge
"""



import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from stack_config import (
    DATA_MODES,
    MONGO_APP_DB,
    PG_AGENTIC_DB,
    PG_CLAIMS_SCHEMA,
    PG_CLUSTER_DATABASES,
    PG_DEFAULT_DB,
    mongo_creds,
    neo4j_creds,
    pg_creds,
    service,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_EXTRACT_DIR = SCRIPT_DIR / ".backup-extract"

MACOSX = "__MACOSX"


def run(cmd, *, capture=False, check=True, input_text=None):
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input_text,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def out(cmd):
    result = run(cmd, capture=True, check=False)
    return (result.stdout or "").strip()


def container_running(name):
    names = out(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
    return name in names


def wait_for_postgres(timeout=120):
    user, _, db = pg_creds()
    name = service("postgres")["name"]
    print(f"  • waiting for {name} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if run(
            ["docker", "exec", name, "pg_isready", "-U", user, "-d", db],
            capture=True,
            check=False,
        ).returncode == 0:
            print(f"  ✓ {name} ready")
            return True
        time.sleep(2)
    print(f"  ✗ {name} not ready after {timeout}s")
    return False


def wait_for_mongo(timeout=120):
    user, password, _ = mongo_creds()
    name = service("mongo")["name"]
    print(f"  • waiting for {name} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run(
            [
                "docker", "exec", name,
                "mongosh", "--quiet",
                "--username", user,
                "--password", password,
                "--authenticationDatabase", "admin",
                "--eval", "db.adminCommand('ping')",
            ],
            capture=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"  ✓ {name} ready")
            return True
        time.sleep(2)
    print(f"  ✗ {name} not ready after {timeout}s")
    return False


def wait_for_neo4j(timeout=120):
    user, password, _ = neo4j_creds()
    name = service("neo4j")["name"]
    print(f"  • waiting for {name} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run(
            [
                "docker", "exec", name,
                "cypher-shell", "-u", user, "-p", password,
                "RETURN 1;",
            ],
            capture=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"  ✓ {name} ready")
            return True
        time.sleep(3)
    print(f"  ✗ {name} not ready after {timeout}s")
    return False


def is_junk_path(path: Path) -> bool:
    return MACOSX in path.parts or path.name.startswith("._")


def unzip_backup(zip_path: Path, extract_dir: Path) -> Path:
    zip_path = zip_path.resolve()
    if not zip_path.is_file():
        raise FileNotFoundError(f"backup zip not found: {zip_path}")

    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nExtracting {zip_path.name} → {extract_dir}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    return extract_dir


def pick_newest(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def find_sql_file(root: Path) -> Path | None:
    matches = [
        p for p in root.rglob("*.sql")
        if p.is_file() and not is_junk_path(p)
    ]
    return pick_newest(matches)


def find_mongo_backup_dir(root: Path) -> Path | None:
    candidates: list[Path] = []
    for prelude in root.rglob("prelude.json"):
        if prelude.is_file() and not is_junk_path(prelude):
            candidates.append(prelude.parent)

    if candidates:
        return pick_newest(candidates)

    for path in sorted(root.rglob("*")):
        if not path.is_dir() or is_junk_path(path):
            continue
        if any(path.glob("*.bson")) and any(p.is_dir() for p in path.iterdir()):
            candidates.append(path)
    return pick_newest(candidates)


def find_neo4j_dump(root: Path) -> Path | None:
    matches = [
        p for p in root.rglob("neo4j.dump")
        if p.is_file() and not is_junk_path(p)
    ]
    return pick_newest(matches)


def discover_artifacts(root: Path):
    sql_file = find_sql_file(root)
    mongo_dir = find_mongo_backup_dir(root)
    neo4j_dump = find_neo4j_dump(root)
    return sql_file, mongo_dir, neo4j_dump


def is_cluster_dump(sql_text: str) -> bool:
    head = sql_text[:8192]
    return "PostgreSQL database cluster dump" in head


def sanitize_postgres_sql(sql_text: str, *, cluster: bool, merge: bool = False) -> str:
    """Make dumps from newer Postgres versions import cleanly into PG 16."""
    cleaned: list[str] = []
    create_db = re.compile(r"^CREATE DATABASE\s+", re.IGNORECASE)
    create_default_db = re.compile(
        rf"^CREATE DATABASE\s+{re.escape(PG_DEFAULT_DB)}\b",
        re.IGNORECASE,
    )
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("\\restrict") or stripped.startswith("\\unrestrict"):
            continue
        if stripped.startswith("SET transaction_timeout"):
            continue
        if "CREATE EXTENSION" in stripped and "vector" in stripped:
            continue
        if stripped.startswith("COMMENT ON EXTENSION vector"):
            continue
        if cluster and (
            stripped.startswith("CREATE ROLE")
            or stripped.startswith("ALTER ROLE")
        ):
            continue
        if cluster and merge and create_db.match(stripped):
            continue
        # postgres is recreated empty before import; skip CREATE DATABASE postgres.
        if cluster and not merge and create_default_db.match(stripped):
            continue
        cleaned.append(line)
    return "\n".join(cleaned) + "\n"


def drop_postgres_database(database: str) -> bool:
    user, password, _ = pg_creds()
    name = service("postgres")["name"]

    terminate = run(
        [
            "docker", "exec", name,
            "env", f"PGPASSWORD={password}",
            "psql", "-v", "ON_ERROR_STOP=1", "-U", user, "-d", "template1", "-c",
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{database}' AND pid <> pg_backend_pid();",
        ],
        capture=True,
        check=False,
    )
    if terminate.returncode != 0:
        print(f"  ✗ failed terminating connections to '{database}':\n{terminate.stderr.strip()}")
        return False

    drop = run(
        [
            "docker", "exec", name,
            "env", f"PGPASSWORD={password}",
            "dropdb", "--if-exists", "-U", user, database,
        ],
        capture=True,
        check=False,
    )
    if drop.returncode != 0:
        print(f"  ✗ failed dropping '{database}':\n{drop.stderr.strip()}")
        return False
    return True


def reset_postgres_database() -> bool:
    user, password, database = pg_creds()
    name = service("postgres")["name"]
    print(f"  • resetting postgres database '{database}'")

    if not drop_postgres_database(database):
        return False

    create = run(
        [
            "docker", "exec", name,
            "env", f"PGPASSWORD={password}",
            "createdb", "-U", user, database,
        ],
        capture=True,
        check=False,
    )
    if create.returncode != 0:
        print(f"  ✗ postgres reset failed:\n{create.stderr.strip()}")
        return False
    return True


def prepare_cluster_import() -> bool:
    """Prepare for cluster dump import.

    - Drop satellite databases (agentic_flow_v2db, test_postgres, …) so the dump
      can CREATE DATABASE them.
    - Reset the default postgres DB to an empty shell so \\connect postgres works
      and CREATE DATABASE postgres in the dump is skipped (already exists).
    """
    print("  • preparing cluster import")
    for db in PG_CLUSTER_DATABASES:
        if db == PG_DEFAULT_DB:
            continue
        print(f"    • dropping '{db}'")
        if not drop_postgres_database(db):
            return False

    print(f"    • resetting '{PG_DEFAULT_DB}' (empty shell for \\connect)")
    return reset_postgres_database()


def import_postgres(sql_file: Path, *, import_mode: str) -> bool:
    user, password, database = pg_creds()
    name = service("postgres")["name"]
    sql_text = sql_file.read_text(encoding="utf-8", errors="replace")
    cluster = is_cluster_dump(sql_text)
    merge = import_mode == "merge"
    replace = import_mode in ("new", "existing")

    if cluster:
        print(f"\n[postgres] importing cluster dump {sql_file.name} ({import_mode} mode)")
        if replace:
            if not prepare_cluster_import():
                return False
            sql_text = sanitize_postgres_sql(sql_text, cluster=True, merge=False)
        else:
            print("  • merge mode — keeping existing databases, applying dump updates")
            sql_text = sanitize_postgres_sql(sql_text, cluster=True, merge=True)
        psql_db = "template1"
    else:
        print(f"\n[postgres] importing {sql_file.name} → database '{database}' ({import_mode} mode)")
        if replace and not reset_postgres_database():
            return False
        sql_text = sanitize_postgres_sql(sql_text, cluster=False, merge=merge)
        psql_db = database

    result = run(
        [
            "docker", "exec", "-i", name,
            "env", f"PGPASSWORD={password}",
            "psql", "-v", "ON_ERROR_STOP=1", "-U", user, "-d", psql_db,
        ],
        capture=True,
        check=False,
        input_text=sql_text,
    )
    if result.returncode == 0:
        print("  ✓ postgres import complete")
        if cluster:
            print(f"  • databases restored: {', '.join(PG_CLUSTER_DATABASES)}")
            print(f"  • claims backend: {PG_DEFAULT_DB} (schema {PG_CLAIMS_SCHEMA})")
            print(f"  • agentic backend database: {PG_AGENTIC_DB}")
        return True

    err = (result.stderr or "").strip()
    print(f"  ✗ postgres import failed:\n{err}")
    if cluster and "already exists" in err.lower():
        if merge:
            print("  hint: postgres merge failed — try --existing for full replace or --new for fresh volumes")
        else:
            print("  hint: re-run with --new or master.py --existing")
    return False


def import_mongo(mongo_dir: Path, *, import_mode: str) -> bool:
    user, password, expected_db = mongo_creds()
    name = service("mongo")["name"]
    container_path = "/tmp/mongorestore"
    drop = import_mode in ("new", "existing")
    print(
        f"\n[mongo] importing {mongo_dir.name} → authdb=admin "
        f"(app db: {expected_db}, {import_mode} mode)"
    )
    if drop:
        print("  • replace mode — dropping collections before restore")
    else:
        print("  • merge mode — upserting into existing collections")

    run(["docker", "exec", name, "rm", "-rf", container_path], check=False)
    cp = run(
        ["docker", "cp", str(mongo_dir), f"{name}:{container_path}"],
        capture=True,
        check=False,
    )
    if cp.returncode != 0:
        print(f"  ✗ failed to copy mongo backup into container:\n{cp.stderr.strip()}")
        return False

    # docker cp creates container_path as a copy of the source folder when the
    # destination does not already exist.
    restore_cmd = [
        "docker", "exec", name,
        "mongorestore",
        "--username", user,
        "--password", password,
        "--authenticationDatabase", "admin",
    ]
    if drop:
        restore_cmd.append("--drop")
    restore_cmd.append(container_path)

    result = run(
        restore_cmd,
        capture=True,
        check=False,
    )
    run(["docker", "exec", name, "rm", "-rf", container_path], check=False)

    if result.returncode == 0:
        print("  ✓ mongo import complete")
        return True

    print(f"  ✗ mongo import failed:\n{result.stderr.strip()}")
    return False


def import_neo4j(dump_file: Path, *, import_mode: str) -> bool:
    user, password, database = neo4j_creds()
    neo4j_svc = service("neo4j")
    name = neo4j_svc["name"]
    loader_name = f"{name}-loader-tmp"
    container_dump_path = "/import/neo4j.dump"
    print(f"\n[neo4j] importing {dump_file.name} → database '{database}' ({import_mode} mode)")
    if import_mode == "merge":
        print("  ! neo4j dump restore always replaces the target database (no partial merge)")

    run(["docker", "stop", name], capture=True, check=False)
    run(["docker", "rm", "-f", loader_name], capture=True, check=False)

    create_loader = run(
        [
            "docker", "create",
            "--name", loader_name,
            "--volumes-from", name,
            neo4j_svc["image"],
            "sleep", "infinity",
        ],
        capture=True,
        check=False,
    )
    if create_loader.returncode != 0:
        run(["docker", "start", name], check=False)
        print(f"  ✗ neo4j import failed:\n{(create_loader.stderr or '').strip()}")
        return False

    start_loader = run(["docker", "start", loader_name], capture=True, check=False)
    if start_loader.returncode != 0:
        run(["docker", "rm", "-f", loader_name], check=False)
        run(["docker", "start", name], check=False)
        print(f"  ✗ neo4j import failed:\n{(start_loader.stderr or '').strip()}")
        return False

    run(["docker", "exec", loader_name, "mkdir", "-p", "/import"], check=False)
    cp_dump = run(
        ["docker", "cp", str(dump_file), f"{loader_name}:{container_dump_path}"],
        capture=True,
        check=False,
    )
    if cp_dump.returncode != 0:
        run(["docker", "rm", "-f", loader_name], check=False)
        run(["docker", "start", name], check=False)
        print(f"  ✗ neo4j import failed:\n{(cp_dump.stderr or '').strip()}")
        return False

    load = run(
        [
            "docker", "exec", loader_name,
            "neo4j-admin", "database", "load", database,
            "--from-path=/import",
            "--overwrite-destination=true",
        ],
        capture=True,
        check=False,
    )

    run(["docker", "rm", "-f", loader_name], check=False)
    start = run(["docker", "start", name], capture=True, check=False)

    if load.returncode != 0:
        print(f"  ✗ neo4j import failed:\n{load.stderr.strip()}")
        run(["docker", "start", name], check=False)
        return False
    if start.returncode != 0:
        print(f"  ✗ neo4j failed to restart:\n{start.stderr.strip()}")
        return False

    if not wait_for_neo4j(timeout=180):
        return False

    ping = run(
        [
            "docker", "exec", name,
            "cypher-shell", "-u", user, "-p", password,
            "RETURN 1;",
        ],
        capture=True,
        check=False,
    )
    if ping.returncode != 0:
        print(
            "  ! neo4j restored but login with stack credentials failed.\n"
            "    The dump may have different auth — update NEO4J_AUTH in stack_config.py\n"
            "    or reset the neo4j password inside the container."
        )
        return False

    print("  ✓ neo4j import complete")
    return True


def ensure_containers():
    missing = [
        svc["name"] for svc in (service("postgres"), service("mongo"), service("neo4j"))
        if not container_running(svc["name"])
    ]
    if missing:
        print(f"ERROR: required containers not running: {', '.join(missing)}")
        print("       Run resouce-creation-script.py or master.py first.")
        return False
    return True


def run_import(backup_zip: Path, extract_dir: Path, *, import_mode: str = "existing") -> int:
    if import_mode not in DATA_MODES:
        print(f"ERROR: unknown import mode '{import_mode}' (use: {', '.join(DATA_MODES)})")
        return 1

    print(f"\nImport mode : {import_mode}")

    if not ensure_containers():
        return 1

    print("\nWaiting for databases to accept connections ...")
    if not wait_for_postgres():
        return 1
    if not wait_for_mongo():
        return 1
    if not wait_for_neo4j():
        return 1

    root = unzip_backup(backup_zip, extract_dir)
    sql_file, mongo_dir, neo4j_dump = discover_artifacts(root)

    print("\nDiscovered backup artifacts:")
    print(f"  SQL     : {sql_file or '— not found'}")
    print(f"  Mongo   : {mongo_dir or '— not found'}")
    print(f"  Neo4j   : {neo4j_dump or '— not found'}")

    if not any((sql_file, mongo_dir, neo4j_dump)):
        print("\nERROR: no importable artifacts found in the archive.")
        return 1

    ok = True
    if sql_file:
        ok = import_postgres(sql_file, import_mode=import_mode) and ok
    else:
        print("\n[postgres] skipped — no .sql file found")
        ok = False

    if mongo_dir:
        ok = import_mongo(mongo_dir, import_mode=import_mode) and ok
    else:
        print("\n[mongo] skipped — no mongodump folder found")
        ok = False

    if neo4j_dump:
        ok = import_neo4j(neo4j_dump, import_mode=import_mode) and ok
    else:
        print("\n[neo4j] skipped — no neo4j.dump found")
        ok = False

    print("\n" + "─" * 60)
    if ok:
        user, _, _ = pg_creds()
        m_user, _, m_db = mongo_creds()
        n_user, _, n_db = neo4j_creds()
        print("Backup import finished successfully.\n")
        print(f"  Postgres  localhost:5432  user={user}")
        print(f"             agentic db={PG_AGENTIC_DB}  claims db={PG_DEFAULT_DB} (schema {PG_CLAIMS_SCHEMA})")
        print(f"  MongoDB   localhost:27017 user={m_user}  db={m_db}")
        print(f"  Neo4j     bolt://localhost:7687  user={n_user}  db={n_db}")
    else:
        print("Backup import finished with errors.")
    print("─" * 60)
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="Restore backup.zip into local Docker databases.")
    parser.add_argument(
        "--backup-zip", required=True,
        help="Path to backup.zip (may contain differently named mongo/neo4j folders)",
    )
    parser.add_argument(
        "--extract-dir", default=str(DEFAULT_EXTRACT_DIR),
        help=f"Where to extract the archive (default: {DEFAULT_EXTRACT_DIR})",
    )
    parser.add_argument(
        "--import-mode",
        choices=DATA_MODES,
        default="existing",
        help="new/existing=full replace from backup; merge=upsert into existing data",
    )
    args = parser.parse_args()

    return run_import(
        Path(args.backup_zip).expanduser(),
        Path(args.extract_dir),
        import_mode=args.import_mode,
    )


if __name__ == "__main__":
    sys.exit(main())
