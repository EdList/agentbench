"""Update command — pull latest probe definitions from GitHub."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

import httpx
import yaml

from agentbench.probes.registry import _BUILTIN_DIR, reset_cache
from agentbench.probes.yaml_loader import load_probes_from_yaml

logger = logging.getLogger(__name__)

_GITHUB_RAW = "https://raw.githubusercontent.com/EdList/agentbench/main/agentbench/probes/builtin/"

_PROBE_FILES = ["safety.yaml", "capability.yaml", "reliability.yaml", "consistency.yaml"]


def check_for_updates() -> list[str]:
    """Check which probe files have updates available. Returns list of filenames."""
    updated = []
    for filename in _PROBE_FILES:
        local_path = _BUILTIN_DIR / filename
        try:
            resp = httpx.get(
                _GITHUB_RAW + filename,
                timeout=10.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                if not local_path.exists():
                    updated.append(filename)
                else:
                    try:
                        local_text = local_path.read_text(encoding="utf-8")
                    except OSError:
                        local_text = None
                    if local_text is None or resp.text != local_text:
                        updated.append(filename)
        except httpx.HTTPError as exc:
            logger.warning("Failed to check %s: %s", filename, exc)
    return updated


def pull_updates(filenames: list[str] | None = None) -> list[str]:
    """Download updated probe files from GitHub. Returns list of updated filenames."""
    targets = filenames if filenames is not None else _PROBE_FILES
    updated = []

    for filename in targets:
        if filename not in _PROBE_FILES:
            continue
        tmp_path: Path | None = None
        try:
            resp = httpx.get(
                _GITHUB_RAW + filename,
                timeout=15.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                local_path = _BUILTIN_DIR / filename

                # Write to a temp file in the same directory, validate the YAML,
                # then atomically replace the local file.
                with tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=_BUILTIN_DIR,
                    prefix=f".{filename}.",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp.write(resp.text)
                    tmp_path = Path(tmp.name)

                load_probes_from_yaml(tmp_path)

                # Backup existing
                if local_path.exists():
                    backup = _BUILTIN_DIR / f"{filename}.bak"
                    shutil.copy2(local_path, backup)

                os.replace(tmp_path, local_path)
                tmp_path = None
                updated.append(filename)
        except (httpx.HTTPError, OSError, ValueError, yaml.YAMLError) as exc:
            logger.warning("Failed to pull %s: %s", filename, exc)
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Failed to remove temporary update file %s: %s", tmp_path, exc)

    if updated:
        reset_cache()
    return updated
