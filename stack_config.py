"""Shared Docker stack configuration for resource creation and backup import."""

import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.expanduser("~/docker-data")
USE_NAMED_VOLUMES = True
NETWORK_NAME = "app-net"

STORAGE_ACCESS_KEY = "LKEsRPYs9xK79lGHV1hz7HtPZ7QCJn1eMNYu"
STORAGE_SECRET = "9xK79lGHdfgdfgHt"
STORAGE_PORT = 8080
STORAGE_URL = f"http://localhost:{STORAGE_PORT}/storage/"

# Application database names (infra starts empty; backup import loads data).
PG_DEFAULT_DB = "postgres"              # claims backend database
PG_AGENTIC_DB = "agentic_flow_v2db"       # agentic backend database
PG_CLAIMS_SCHEMA = "claims_corebackend"   # claims backend schema inside postgres
MONGO_APP_DB = "sop_ingestion_v2"         # agentic mongo database
PG_CLUSTER_DATABASES = [PG_DEFAULT_DB, PG_AGENTIC_DB, "test_postgres"]

SERVICES = [
    {
        "name": "postgres",
        "image": "postgres:16",
        "ports": {5432: 5432},
        "env": {
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "s1Hd6Tg1Ksdfs",
            "POSTGRES_DB": PG_DEFAULT_DB,
        },
        "host_path": os.path.join(DATA_ROOT, "postgres"),
        "volume": "/var/lib/postgresql/data",
        "cmd": [],
    },
    {
        "name": "redis",
        "image": "redis:7",
        "ports": {6379: 6379},
        "env": {},
        "host_path": os.path.join(DATA_ROOT, "redis"),
        "volume": "/data",
        "cmd": [
            "redis-server",
            "--requirepass", "s1Hd6lksjdflksdf8dfsdfc2Gl1Uu",
            "--appendonly", "yes",
        ],
    },
    {
        "name": "neo4j",
        "image": "neo4j:5",
        "ports": {7687: 7687, 7474: 7474},
        "env": {
            "NEO4J_AUTH": "neo4j/s1Hd6lsdklfjlasd99ef8dfsdfc2Gl1Uu",
        },
        "host_path": os.path.join(DATA_ROOT, "neo4j"),
        "volume": "/data",
        "cmd": [],
    },
    {
        "name": "mongo",
        "image": "mongo:7",
        "ports": {27017: 27017},
        "env": {
            "MONGO_INITDB_ROOT_USERNAME": "admin",
            "MONGO_INITDB_ROOT_PASSWORD": "s1Hd6lsd34jdkkljsdnflssd99ef8dfsdfc2Gl1Uu",
            "MONGO_INITDB_DATABASE": MONGO_APP_DB,
        },
        "host_path": os.path.join(DATA_ROOT, "mongo"),
        "volume": "/data/db",
        "cmd": [],
    },
    {
        "name": "rabbitmq",
        "image": "rabbitmq:3-management",
        "ports": {5672: 5672, 15672: 15672},
        "env": {
            "RABBITMQ_DEFAULT_USER": "guest",
            "RABBITMQ_DEFAULT_PASS": "s1Hd6lsd34lksdjldfskljsdnflssd99ef8dfsdfc2Gl1Uu",
        },
        "host_path": os.path.join(DATA_ROOT, "rabbitmq"),
        "volume": "/var/lib/rabbitmq",
        "cmd": [],
    },
    {
        "name": "storage",
        "image": "uhc-storage-account:local",
        "build": os.path.join(SCRIPT_DIR, "storage-account"),
        "ports": {STORAGE_PORT: 8080},
        "env": {
            "STORAGE_ACCESS_KEY": STORAGE_ACCESS_KEY,
            "STORAGE_SECRET": STORAGE_SECRET,
        },
        "host_path": os.path.join(DATA_ROOT, "storage-uploads"),
        "volume": "/data/uploads",
        "cmd": [],
    },
]


def service(name):
    for svc in SERVICES:
        if svc["name"] == name:
            return svc
    raise KeyError(f"unknown service: {name}")


def pg_creds():
    env = service("postgres")["env"]
    return env["POSTGRES_USER"], env["POSTGRES_PASSWORD"], env["POSTGRES_DB"]


def mongo_creds():
    env = service("mongo")["env"]
    return (
        env["MONGO_INITDB_ROOT_USERNAME"],
        env["MONGO_INITDB_ROOT_PASSWORD"],
        MONGO_APP_DB,
    )


def neo4j_creds():
    user, password = service("neo4j")["env"]["NEO4J_AUTH"].split("/", 1)
    return user, password, "neo4j"


def storage_creds():
    env = service("storage")["env"]
    return env["STORAGE_ACCESS_KEY"], env["STORAGE_SECRET"], STORAGE_URL


# ── Application services (deploy-apps.py — local processes) ───────────────────
DEV_ENV_ROOT = os.path.join(SCRIPT_DIR, "dev-env")
ENV_DETAILS_ROOT = os.path.join(SCRIPT_DIR, "env-details")

# Folder names under dev-env/ (first match wins).
LOCAL_APP_FOLDERS = {
    "agentic-backend": ["agentic-backend", "uhc-agentic-backend"],
    "claims-backend": ["claims-backend", "uhc-claims-backend"],
    "claims-frontend": ["claims-frontend", "uhc-claims-frontend"],
    "audit-dashboard": ["audit-review-dashboard", "uhc-audit-review-dashbaord"],
    "mcp-server": ["claims-tools-mcp-server", "mcp-server"],
}

APP_PORTS = {
    "agentic-backend": 8000,
    "mcp-server": 3000,
    "claims-backend": 4000,
    "claims-frontend": 5173,
    "audit-dashboard": 5174,
}

APP_SERVICES = [
    {
        "name": "agentic-backend",
        "env_file": os.path.join(ENV_DETAILS_ROOT, ".env-uhc-agentic-backend"),
        "env_overrides": {
            "PG_DATABASE": PG_AGENTIC_DB,
            "MONGO_DATABASE": MONGO_APP_DB,
        },
        "health_url": "http://localhost:8000/api/ingest/health/",
    },
    {
        "name": "mcp-server",
        "env_file": os.path.join(ENV_DETAILS_ROOT, ".env-mcp-server"),
        "env_file_optional": True,
        "env_overrides": {},
        "health_url": "http://localhost:3000/health",
    },
    {
        "name": "claims-backend",
        "env_file": os.path.join(ENV_DETAILS_ROOT, ".env-uhc-claims-backend"),
        "env_overrides": {
            "DJANGO_BASE_URL": "http://localhost:8000",
        },
        "health_url": "http://localhost:4000/health",
    },
    {
        "name": "claims-frontend",
        "env_file": os.path.join(ENV_DETAILS_ROOT, ".env-uhc-claims-frontend"),
        "env_overrides": {},
        "health_url": "http://localhost:5173/",
    },
    {
        "name": "audit-dashboard",
        "env_file": os.path.join(ENV_DETAILS_ROOT, ".env-uhc-audit-review-dashbaord"),
        "env_overrides": {},
        "health_url": "http://localhost:5174/",
    },
]


def app_service(name):
    for svc in APP_SERVICES:
        if svc["name"] == name:
            return svc
    raise KeyError(f"unknown app service: {name}")


def claims_database_url():
    user, password, _ = pg_creds()
    return (
        f"postgresql://{user}:{password}@localhost:5432/"
        f"{PG_DEFAULT_DB}?schema={PG_CLAIMS_SCHEMA}"
    )

