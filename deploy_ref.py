"""Parse deployment-ref.txt into per-repo deploy steps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from config import REPO_NAMES, deployment_ref_path


@dataclass
class DeployStep:
    command: str
    background: bool = False
    log_file: str | None = None
    process_name: str | None = None


_STEP_RE = re.compile(r"^step\d+:\s*(.+)$", re.IGNORECASE)
_SECTION_RE = re.compile(r"^\[([a-zA-Z0-9_]+)\]$")


def _parse_step_line(body: str) -> DeployStep:
    parts = [part.strip() for part in body.split(" | ") if part.strip()]
    command = parts[0]
    background = False
    log_file = None
    process_name = None
    for token in parts[1:]:
        lower = token.lower()
        if lower == "background":
            background = True
        elif lower.startswith("log="):
            log_file = token.split("=", 1)[1].strip()
        elif lower.startswith("process="):
            process_name = token.split("=", 1)[1].strip()
    if background and not process_name:
        raise ValueError(f"background step missing process= name: {body}")
    if background and not log_file:
        raise ValueError(f"background step missing log= file: {body}")
    return DeployStep(command=command, background=background, log_file=log_file, process_name=process_name)


def parse_deployment_ref(path: Path | None = None) -> dict[str, list[DeployStep]]:
    ref_path = path or deployment_ref_path()
    if not ref_path.is_file():
        raise FileNotFoundError(f"deployment ref not found: {ref_path}")

    sections: dict[str, list[DeployStep]] = {}
    current: str | None = None

    for raw in ref_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        section_match = _SECTION_RE.match(line)
        if section_match:
            current = section_match.group(1).lower()
            if current not in REPO_NAMES:
                raise ValueError(f"unknown repo section [{current}] in {ref_path}")
            sections[current] = []
            continue
        step_match = _STEP_RE.match(line)
        if not step_match:
            raise ValueError(f"invalid deployment-ref line: {raw}")
        if current is None:
            raise ValueError(f"step before repo section in {ref_path}: {raw}")
        sections[current].append(_parse_step_line(step_match.group(1)))

    missing = [name for name in REPO_NAMES if name not in sections]
    if missing:
        raise ValueError(f"deployment-ref missing sections: {', '.join(missing)}")
    return sections


def background_process_names(repo: str, path: Path | None = None) -> list[str]:
    steps = parse_deployment_ref(path).get(repo, [])
    return [step.process_name for step in steps if step.background and step.process_name]
