#!/usr/bin/env python3
"""
Spin up Postgres, Redis, Neo4j, MongoDB, RabbitMQ, and the storage file server as Docker containers.

Works on macOS, Windows and Linux as long as Docker (Docker Desktop) is
installed and the daemon is running.

Usage:
    python setup_stack.py            # create + start everything
    python setup_stack.py --recreate # remove & recreate containers (data dirs kept)
    python setup_stack.py --down     # stop & remove containers (data dirs kept)
    python setup_stack.py --status   # show what's running

Everything you need to edit (passwords + volume paths) is in stack_config.py.
"""

import argparse
import os
import platform
import subprocess
import sys

from stack_config import DATA_ROOT, NETWORK_NAME, SERVICES, STORAGE_PORT, STORAGE_URL, USE_NAMED_VOLUMES

IS_WINDOWS = platform.system() == "Windows"


# ─────────────────────────── small helpers ───────────────────────────
def run(cmd, capture=False, check=True):
    """Run a command. Returns CompletedProcess. cmd is a list of args."""
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def out(cmd):
    """Run and return stripped stdout (empty string on failure)."""
    r = run(cmd, capture=True, check=False)
    return (r.stdout or "").strip()


def docker_ready():
    """Check docker is installed and the daemon is up."""
    if subprocess.run(["docker", "--version"],
                      stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        print("ERROR: Docker CLI not found. Install Docker Desktop and try again.")
        return False
    if subprocess.run(["docker", "info"],
                      stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        print("ERROR: Docker is installed but the daemon isn't running.")
        print("       Start Docker Desktop, wait until it says 'running', then re-run.")
        return False
    return True


def ensure_network():
    existing = out(["docker", "network", "ls", "--format", "{{.Name}}"]).splitlines()
    if NETWORK_NAME not in existing:
        run(["docker", "network", "create", NETWORK_NAME])
        print(f"  • created network '{NETWORK_NAME}'")


def container_exists(name):
    names = out(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
    return name in names


def container_running(name):
    names = out(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
    return name in names


def host_path(service):
    # Use the explicit "host_path" set per service; fall back to DATA_ROOT/<name>.
    p = service.get("host_path") or os.path.join(DATA_ROOT, service["name"])
    return os.path.abspath(p)


def format_bind_source(path):
    """Return a host path Docker/Podman can bind-mount on this OS."""
    path = os.path.abspath(path)
    if IS_WINDOWS:
        # C:\foo\bar breaks `-v host:container` because the drive-letter colon is
        # treated as the mount delimiter. Forward slashes fix this on Windows.
        return path.replace("\\", "/")
    return path


def docker_context_path(path):
    """Normalize a filesystem path for docker build/run arguments."""
    return format_bind_source(path) if IS_WINDOWS else os.path.abspath(path)


def ensure_dir_with_perms(path):
    """Create the host dir and make sure the container can write to it.

    Returns True if usable, False if the user must fix permissions manually.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        print(f"  ! cannot create {path} (permission denied).")
        print(f"    Create it yourself or pick a DATA_ROOT you own, then re-run.")
        return False

    # On macOS/Linux, container processes often run as a non-root uid and need
    # write access to the bind-mounted folder. Open it up for local dev use.
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o777)
        except PermissionError:
            pass  # fall through to the writability check below

    if not os.access(path, os.W_OK):
        print(f"  ! {path} is not writable by you.")
        if not IS_WINDOWS:
            print(f"    Fix with:  sudo chown -R $(id -u):$(id -g) '{path}'")
        return False
    return True


def add_volume_mount(cmd, service):
    """Append a volume mount to a docker run command.

    Returns False if a host directory could not be prepared.
    """
    if USE_NAMED_VOLUMES:
        cmd += ["-v", f"{service['name']}-data:{service['volume']}"]
        return True

    hp = host_path(service)
    if not ensure_dir_with_perms(hp):
        return False

    source = format_bind_source(hp)
    target = service["volume"]
    if IS_WINDOWS:
        # Avoid `-v C:/host:/container` parsing issues on Windows/Podman.
        cmd += ["--mount", f"type=bind,source={source},target={target}"]
    else:
        cmd += ["-v", f"{source}:{target}"]
    return True


# ─────────────────────────── actions ───────────────────────────
def pull(image):
    print(f"  • pulling {image} ...")
    run(["docker", "pull", image])


def image_exists(image):
    return run(["docker", "image", "inspect", image], capture=True, check=False).returncode == 0


def local_image_ref(image):
    """Return a local image reference docker run can use without pulling."""
    image_id = out(["docker", "images", "-q", image])
    if image_id:
        return image_id.splitlines()[0]
    return image


def build_image(service, force=False):
    image = service["image"]
    context = service.get("build")
    if not context:
        pull(image)
        return True

    context = docker_context_path(context)
    if not os.path.isdir(context):
        print(f"  ✗ build context not found for '{service['name']}': {context}")
        return False

    if force or not image_exists(image):
        print(f"  • building {image} from {context} ...")
        build_cmd = ["docker", "build", "-t", image, context]
        result = run(build_cmd, capture=True, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            print(f"  ✗ failed to build '{service['name']}':\n    {detail}")
            return False

        if not image_exists(image):
            # buildx builders may need --load to make the image visible locally.
            print(f"  • image not visible yet — retrying with docker build --load ...")
            load_cmd = ["docker", "build", "--load", "-t", image, context]
            result = run(load_cmd, capture=True, check=False)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                print(f"  ✗ failed to load '{service['name']}' image:\n    {detail}")
                return False

        if not image_exists(image):
            print(f"  ✗ build finished but image '{image}' is not in local docker images.")
            print(f"    Try manually: docker build -t {image} {context}")
            return False

        print(f"  ✓ built {image}")
        return True

    print(f"  • image '{image}' already exists — skipping build")
    return True


def remove(name):
    run(["docker", "rm", "-f", name], capture=True, check=False)


def create_service(service, recreate):
    name = service["name"]

    if container_exists(name):
        if recreate:
            print(f"  • removing existing '{name}' ...")
            remove(name)
        elif container_running(name):
            print(f"  • '{name}' already running — skipping")
            return True
        else:
            print(f"  • starting existing '{name}' ...")
            run(["docker", "start", name])
            return True

    if not build_image(service, force=recreate):
        print(f"  ✗ skipped '{name}' — image build failed.")
        return False

    image_ref = local_image_ref(service["image"])
    if image_ref != service["image"]:
        print(f"  • using built image id {image_ref[:12]}")

    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "--network", NETWORK_NAME,
    ]
    if service.get("build"):
        # Local-only tags like uhc-storage-account:local must not be pulled from Docker Hub.
        cmd += ["--pull=never"]
    for host_port, cont_port in service["ports"].items():
        cmd += ["-p", f"{host_port}:{cont_port}"]
    for k, v in service["env"].items():
        cmd += ["-e", f"{k}={v}"]
    if not add_volume_mount(cmd, service):
        print(f"  ✗ skipped '{name}' — couldn't prepare its volume.")
        return False
    cmd.append(image_ref)
    cmd += service["cmd"]

    r = run(cmd, capture=True, check=False)
    if r.returncode == 0:
        print(f"  ✓ '{name}' started")
        return True
    detail = (r.stderr or r.stdout or "").strip()
    print(f"  ✗ failed to start '{name}':\n    {detail}")
    return False


def cmd_up(recreate):
    print(f"\nData mode : {'Docker named volumes' if USE_NAMED_VOLUMES else DATA_ROOT}")
    print(f"Platform  : {platform.system()}\n")
    ensure_network()
    ok = True
    for svc in SERVICES:
        print(f"[{svc['name']}]")
        if not create_service(svc, recreate):
            ok = False
    if ok:
        print_summary()
    else:
        print("\nSome services failed to start. Check the errors above, then re-run with --recreate.")
    return 0 if ok else 1


def cmd_down():
    for svc in SERVICES:
        if container_exists(svc["name"]):
            print(f"  • removing '{svc['name']}'")
            remove(svc["name"])
    print("\nDone. Data folders/volumes were left untouched.")


def cmd_status():
    run(["docker", "ps", "--filter", f"network={NETWORK_NAME}",
         "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], check=False)


def print_summary():
    print("\n" + "─" * 60)
    print("Stack is up. Connect from your host using 'localhost':\n")
    print("  Postgres   localhost:5432   user=postgres  db=postgres")
    print("  Redis      localhost:6379   (auth via requirepass)")
    print("  Neo4j      bolt://localhost:7687   browser http://localhost:7474")
    print("  MongoDB    localhost:27017  user=admin  authdb=admin")
    print("  RabbitMQ   localhost:5672   UI http://localhost:15672")
    print(f"  Storage    {STORAGE_URL}  UI login {STORAGE_URL}login")
    print(f"               API {STORAGE_URL}api/storage")
    print("\nUseful:")
    print("  docker ps                 # see containers")
    print("  docker logs -f postgres   # follow a container's logs")
    print("  python master.py --down        # tear it all down")
    print("─" * 60)
    print("Note: RabbitMQ's 'guest' user only connects from localhost by")
    print("design. If you need remote access, change RABBITMQ_DEFAULT_USER.")


def main():
    p = argparse.ArgumentParser(description="Create local DB/queue Docker containers.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--recreate", action="store_true",
                   help="remove & recreate containers (data kept)")
    g.add_argument("--down", action="store_true",
                   help="stop & remove containers (data kept)")
    g.add_argument("--status", action="store_true", help="show running containers")
    args = p.parse_args()

    if not docker_ready():
        sys.exit(1)

    if args.down:
        cmd_down()
    elif args.status:
        cmd_status()
    else:
        sys.exit(cmd_up(recreate=args.recreate))



if __name__ == "__main__":
    main()