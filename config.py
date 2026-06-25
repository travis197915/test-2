"""Load orchestrator settings from .env next to master.py."""

from __future__ import annotations

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"

DATA_MODES = ("new", "existing", "merge")
INFRA_SERVICES = ("postgres", "mongo", "neo4j", "redis", "rabbitmq", "storage")
IMPORT_TARGETS = ("postgres", "mongo", "neo4j")
REPO_NAMES = (
    "agentic_backend",
    "claims_backend",
    "audit_review_dashboard_hitl",
    "claims_frontend_dashboard",
    "mcp_server",
)

_REPO_PATH_KEYS = {
    "agentic_backend": "AGENTIC_BACKEND_PATH",
    "claims_backend": "CLAIMS_BACKEND_PATH",
    "audit_review_dashboard_hitl": "AUDIT_REVIEW_DASHBOARD_HITL_PATH",
    "claims_frontend_dashboard": "CLAIMS_FRONTEND_DASHBOARD_PATH",
    "mcp_server": "MCP_SERVER_PATH",
}

_env: dict[str, str] = {}
_loaded = False


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_env(force: bool = False) -> dict[str, str]:
    global _loaded
    if _loaded and not force:
        return _env

    _env.clear()
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            _env[key.strip()] = _strip_quotes(value)

    _loaded = True
    return _env


def get(key: str, default: str = "") -> str:
    load_env()
    return _env.get(key, default)


def get_bool(key: str, default: bool = False) -> bool:
    value = get(key, str(default)).lower()
    return value in ("1", "true", "yes", "on")


def get_int(key: str, default: int) -> int:
    value = get(key, "")
    try:
        return int(value)
    except ValueError:
        return default


def require(key: str) -> str:
    value = get(key)
    if not value:
        raise ValueError(f"Missing required setting in {ENV_FILE}: {key}")
    return value


def expand_path(key: str) -> Path:
    return Path(require(key)).expanduser().resolve()


def repo_path(repo: str) -> Path:
    if repo not in _REPO_PATH_KEYS:
        raise KeyError(f"unknown repo: {repo}")
    return expand_path(_REPO_PATH_KEYS[repo])


def deployment_ref_path() -> Path:
    path = get("DEPLOYMENT_REF_FILE", "deployment-ref.txt")
    p = Path(path)
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    return p


def stack_volume_name(service_name: str) -> str:
    return f"{service_name}-data"


def data_root() -> str:
    return os.path.expanduser(get("DATA_ROOT", "~/docker-data"))


def use_named_volumes() -> bool:
    return get_bool("USE_NAMED_VOLUMES", True)


def network_name() -> str:
    return get("NETWORK_NAME", "app-net")


def pg_default_db() -> str:
    return get("POSTGRES_DB", "postgres")


def pg_agentic_db() -> str:
    return get("PG_AGENTIC_DB", "agentic_flow_v2db")


def pg_claims_schema() -> str:
    return get("PG_CLAIMS_SCHEMA", "claims_corebackend")


def mongo_app_db() -> str:
    return get("MONGO_DATABASE", "sop_ingestion_v2")


def pg_cluster_databases() -> list[str]:
    raw = get("PG_CLUSTER_DATABASES", "postgres,agentic_flow_v2db,test_postgres")
    return [part.strip() for part in raw.split(",") if part.strip()]


def storage_url() -> str:
    port = get_int("STORAGE_PORT", 8080)
    return f"http://localhost:{port}/storage/"


def build_services() -> list[dict]:
    load_env()
    script_dir = str(SCRIPT_DIR)
    data_root_path = data_root()
    storage_port = get_int("STORAGE_PORT", 8080)
    redis_password = get("REDIS_PASSWORD", "changeme")
    storage_build = get("STORAGE_BUILD_PATH", "storage-account")
    if not os.path.isabs(storage_build):
        storage_build = os.path.join(script_dir, storage_build)

    return [
        {
            "name": "postgres",
            "image": get("POSTGRES_IMAGE", "postgres:16"),
            "ports": {get_int("POSTGRES_PORT", 5432): 5432},
            "env": {
                "POSTGRES_USER": get("POSTGRES_USER", "postgres"),
                "POSTGRES_PASSWORD": get("POSTGRES_PASSWORD", "postgres"),
                "POSTGRES_DB": pg_default_db(),
            },
            "host_path": os.path.join(data_root_path, "postgres"),
            "volume": "/var/lib/postgresql/data",
            "cmd": [],
        },
        {
            "name": "redis",
            "image": get("REDIS_IMAGE", "redis:7"),
            "ports": {get_int("REDIS_PORT", 6379): 6379},
            "env": {},
            "host_path": os.path.join(data_root_path, "redis"),
            "volume": "/data",
            "cmd": [
                "redis-server",
                "--requirepass", redis_password,
                "--appendonly", "yes",
            ],
        },
        {
            "name": "neo4j",
            "image": get("NEO4J_IMAGE", "neo4j:5"),
            "ports": {
                get_int("NEO4J_BOLT_PORT", 7687): 7687,
                get_int("NEO4J_HTTP_PORT", 7474): 7474,
            },
            "env": {"NEO4J_AUTH": get("NEO4J_AUTH", "neo4j/changeme")},
            "host_path": os.path.join(data_root_path, "neo4j"),
            "volume": "/data",
            "cmd": [],
        },
        {
            "name": "mongo",
            "image": get("MONGO_IMAGE", "mongo:7"),
            "ports": {get_int("MONGO_PORT", 27017): 27017},
            "env": {
                "MONGO_INITDB_ROOT_USERNAME": get("MONGO_USER", "admin"),
                "MONGO_INITDB_ROOT_PASSWORD": get("MONGO_PASSWORD", "changeme"),
                "MONGO_INITDB_DATABASE": mongo_app_db(),
            },
            "host_path": os.path.join(data_root_path, "mongo"),
            "volume": "/data/db",
            "cmd": [],
        },
        {
            "name": "rabbitmq",
            "image": get("RABBITMQ_IMAGE", "rabbitmq:3-management"),
            "ports": {
                get_int("RABBITMQ_PORT", 5672): 5672,
                get_int("RABBITMQ_MGMT_PORT", 15672): 15672,
            },
            "env": {
                "RABBITMQ_DEFAULT_USER": get("RABBITMQ_USER", "guest"),
                "RABBITMQ_DEFAULT_PASS": get("RABBITMQ_PASSWORD", "changeme"),
            },
            "host_path": os.path.join(data_root_path, "rabbitmq"),
            "volume": "/var/lib/rabbitmq",
            "cmd": [],
        },
        {
            "name": "storage",
            "image": get("STORAGE_IMAGE", "uhc-storage-account:local"),
            "build": storage_build,
            "ports": {storage_port: 8080},
            "env": {
                "STORAGE_ACCESS_KEY": get("STORAGE_ACCESS_KEY", "local-access-key"),
                "STORAGE_SECRET": get("STORAGE_SECRET", "local-secret"),
            },
            "host_path": os.path.join(data_root_path, "storage-uploads"),
            "volume": "/data/uploads",
            "cmd": [],
        },
    ]


def services() -> list[dict]:
    return build_services()


def service(name: str) -> dict:
    for svc in services():
        if svc["name"] == name:
            return svc
    raise KeyError(f"unknown service: {name}")


def pg_creds() -> tuple[str, str, str]:
    env = service("postgres")["env"]
    return env["POSTGRES_USER"], env["POSTGRES_PASSWORD"], env["POSTGRES_DB"]


def mongo_creds() -> tuple[str, str, str]:
    env = service("mongo")["env"]
    return (
        env["MONGO_INITDB_ROOT_USERNAME"],
        env["MONGO_INITDB_ROOT_PASSWORD"],
        mongo_app_db(),
    )


def neo4j_creds() -> tuple[str, str, str]:
    user, password = service("neo4j")["env"]["NEO4J_AUTH"].split("/", 1)
    return user, password, get("NEO4J_DATABASE", "neo4j")


def mcp_server_url() -> str:
    return get("MCP_SERVER_URL", "http://localhost:3000")


def parse_target_list(raw: str | None, *, allowed: tuple[str, ...], label: str) -> list[str]:
    if not raw or raw.strip().lower() == "all":
        return list(allowed)
    items = [part.strip().lower().replace("-", "_") for part in raw.split(",") if part.strip()]
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise ValueError(f"unknown {label}: {', '.join(unknown)} (allowed: {', '.join(allowed)}, all)")
    return items
