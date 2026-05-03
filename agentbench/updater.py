"""Update command — pull latest probe definitions from GitHub."""

from __future__ import annotations

import shutil

import httpx

from agentbench.probes.registry import _BUILTIN_DIR

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
                elif resp.text != local_path.read_text():
                    updated.append(filename)
        except httpx.HTTPError:
            pass
    return updated


def pull_updates(filenames: list[str] | None = None) -> list[str]:
    """Download updated probe files from GitHub. Returns list of updated filenames."""
    targets = filenames or _PROBE_FILES
    updated = []

    for filename in targets:
        if filename not in _PROBE_FILES:
            continue
        try:
            resp = httpx.get(
                _GITHUB_RAW + filename,
                timeout=15.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                # Backup existing
                local_path = _BUILTIN_DIR / filename
                if local_path.exists():
                    backup = _BUILTIN_DIR / f"{filename}.bak"
                    shutil.copy2(local_path, backup)

                local_path.write_text(resp.text)
                updated.append(filename)
        except httpx.HTTPError:
            pass

    return updated
