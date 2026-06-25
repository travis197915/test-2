#!/usr/bin/env python3
"""
Spin up Postgres, Redis, Neo4j, MongoDB, RabbitMQ, and storage as Docker containers.

Usage:
    python resouce-creation-script.py --recreate
    python resouce-creation-script.py --recreate --services postgres,mongo
    python resouce-creation-script.py --recreate --services all --volume-mode new
    python resouce-creation-script.py --down --services postgres
    python resouce-creation-script.py --status
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys

import config

IS_WINDOWS = platform.system() == "Windows"


def run(cmd, capture=False, check=True):
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def out(cmd):
    r = run(cmd, capture=True, check=False)
    return (r.stdout or "").strip()


def docker_ready():
    if subprocess.run(["docker", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        print("ERROR: Docker CLI not found. Install Docker Desktop and try again.")
        return False
    if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        print("ERROR: Docker is installed but the daemon isn't running.")
        return False
    return True


def ensure_network():
    existing = out(["docker", "network", "ls", "--format", "{{.Name}}"]).splitlines()
    if config.network_name() not in existing:
        run(["docker", "network", "create", config.network_name()])
        print(f"  • created network '{config.network_name()}'")


def container_exists(name):
    return name in out(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()


def container_running(name):
    return name in out(["docker", "ps", "--format", "{{.Names}}"]).splitlines()


def host_path(service):
    return os.path.abspath(service.get("host_path") or os.path.join(config.data_root(), service["name"]))


def format_bind_source(path):
    path = os.path.abspath(path)
    if IS_WINDOWS:
        return path.replace("\\", "/")
    return path


def docker_context_path(path):
    return format_bind_source(path) if IS_WINDOWS else os.path.abspath(path)


def ensure_dir_with_perms(path):
    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        print(f"  ! cannot create {path} (permission denied).")
        return False
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o777)
        except PermissionError:
            pass
    if not os.access(path, os.W_OK):
        print(f"  ! {path} is not writable.")
        return False
    return True


def add_volume_mount(cmd, service):
    if config.use_named_volumes():
        cmd += ["-v", f"{config.stack_volume_name(service['name'])}:{service['volume']}"]
        return True
    hp = host_path(service)
    if not ensure_dir_with_perms(hp):
        return False
    source = format_bind_source(hp)
    target = service["volume"]
    if IS_WINDOWS:
        cmd += ["--mount", f"type=bind,source={source},target={target}"]
    else:
        cmd += ["-v", f"{source}:{target}"]
    return True


def pull(image):
    print(f"  • pulling {image} ...")
    run(["docker", "pull", image])


def image_exists(image):
    return run(["docker", "image", "inspect", image], capture=True, check=False).returncode == 0


def local_image_ref(image):
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
        result = run(["docker", "build", "-t", image, context], capture=True, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            print(f"  ✗ failed to build '{service['name']}':\n    {detail}")
            return False
        if not image_exists(image):
            result = run(["docker", "build", "--load", "-t", image, context], capture=True, check=False)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                print(f"  ✗ failed to load '{service['name']}' image:\n    {detail}")
                return False
        if not image_exists(image):
            print(f"  ✗ build finished but image '{image}' is not available locally.")
            return False
        print(f"  ✓ built {image}")
        return True
    print(f"  • image '{image}' already exists — skipping build")
    return True


def remove(name):
    run(["docker", "rm", "-f", name], capture=True, check=False)


def remove_service_volumes(service_names: list[str]):
    print("\nRemoving data for selected services ...")
    all_services = {svc["name"]: svc for svc in config.services()}
    for name in service_names:
        svc = all_services.get(name)
        if not svc:
            continue
        if config.use_named_volumes():
            vol = config.stack_volume_name(name)
            print(f"  • removing volume '{vol}' ...")
            run(["docker", "volume", "rm", "-f", vol], check=False)
        else:
            hp = host_path(svc)
            if os.path.isdir(hp):
                print(f"  • removing data dir '{hp}' ...")
                shutil.rmtree(hp, ignore_errors=True)


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
        "--network", config.network_name(),
    ]
    if service.get("build"):
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


def cmd_up(recreate, volume_mode, service_names):
    storage = "Docker named volumes" if config.use_named_volumes() else config.data_root()
    print(f"\nData mode    : {storage}")
    print(f"Volume mode  : {volume_mode}")
    print(f"Services     : {', '.join(service_names)}")
    print(f"Platform     : {platform.system()}\n")

    if volume_mode == "new":
        for svc in config.services():
            if container_exists(svc["name"]) and svc["name"] in service_names:
                print(f"  • removing container '{svc['name']}' before volume wipe ...")
                remove(svc["name"])
        remove_service_volumes(service_names)
        recreate = True

    ensure_network()
    ok = True
    for svc in config.services():
        if svc["name"] not in service_names:
            continue
        print(f"[{svc['name']}]")
        if not create_service(svc, recreate):
            ok = False
    if ok:
        print_summary()
    else:
        print("\nSome services failed. Fix errors above and re-run the failed service only.")
    return 0 if ok else 1


def cmd_down(service_names):
    for svc in config.services():
        if svc["name"] in service_names and container_exists(svc["name"]):
            print(f"  • removing '{svc['name']}'")
            remove(svc["name"])
    print("\nDone. Data volumes/folders were left untouched.")


def cmd_status():
    run(
        ["docker", "ps", "--filter", f"network={config.network_name()}",
         "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
        check=False,
    )


def print_summary():
    port = config.get_int("STORAGE_PORT", 8080)
    storage_url = config.storage_url()
    print("\n" + "─" * 60)
    print("Stack is up. Connect from your host using 'localhost':\n")
    print("  Postgres   localhost:5432")
    print("  Redis      localhost:6379")
    print("  Neo4j      bolt://localhost:7687   browser http://localhost:7474")
    print("  MongoDB    localhost:27017")
    print("  RabbitMQ   localhost:5672   UI http://localhost:15672")
    print(f"  Storage    {storage_url}")
    print("\nUseful:")
    print("  docker ps")
    print("  python master.py --status")
    print("  python master.py --down")
    print("─" * 60)


def main():
    p = argparse.ArgumentParser(description="Create local DB/queue Docker containers.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--recreate", action="store_true", help="remove & recreate selected containers")
    g.add_argument("--down", action="store_true", help="stop & remove selected containers")
    g.add_argument("--status", action="store_true", help="show running containers")
    p.add_argument("--services", default="all", help="comma list or all (default: all)")
    p.add_argument("--volume-mode", choices=config.DATA_MODES, default="existing",
                   help="new=wipe selected volumes; existing/merge=keep volumes")
    args = p.parse_args()

    if not docker_ready():
        sys.exit(1)

    try:
        service_names = config.parse_target_list(args.services, allowed=config.INFRA_SERVICES, label="service")
    except ValueError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    if args.down:
        cmd_down(service_names)
    elif args.status:
        cmd_status()
    else:
        sys.exit(cmd_up(args.recreate, args.volume_mode, service_names))


if __name__ == "__main__":
    main()
