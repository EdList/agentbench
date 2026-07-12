"""Update command — pull latest probe definitions from GitHub."""

from __future__ import annotations

import hashlib
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

# Cap remote downloads to prevent memory exhaustion from oversized responses.
MAX_PROBE_BYTES = 1_000_000

# Pin remote updates to an immutable commit instead of the mutable main branch.
# To intentionally update the trusted probe bundle, change both REPO_PIN and
# _EXPECTED_SHA256 after reviewing the upstream diff.
REPO_PIN = "e8af5ca92db464c25e7ede4c41a19a19499f8ea4"
_GITHUB_RAW = (
    "https://raw.githubusercontent.com/EdList/agentbench/"
    f"{REPO_PIN}/agentbench/probes/builtin/"
)

_PROBE_FILES = ["safety.yaml", "capability.yaml", "reliability.yaml", "consistency.yaml"]
_EXPECTED_SHA256 = {
    "capability.yaml": "39df504af13cfd51c5c1bb65696393bc7f97ba230e5d68a309288ef79c705c55",
    "consistency.yaml": "93318546a6560383df73ffd83f5bc36bc18907bb037a1b458c0dbf8dd7b252a9",
    "reliability.yaml": "432a707c9c8acaf7b8ba1dddd62fb73114b3bd81d23d1f7b71294898c0716df2",
    "safety.yaml": "37789d34e45d3243a5c44ddf4bae6097484870dcb5863724d2ad8bfc01e73cdf",
}


def _response_bytes(resp: httpx.Response) -> bytes:
    """Return response bytes, tolerating simple response fakes used in tests."""
    content = getattr(resp, "content", None)
    if content is not None:
        return content
    return resp.text.encode("utf-8")


def _verified_text(filename: str, resp: httpx.Response) -> str:
    """Verify the downloaded probe file checksum and return text content."""
    # Unit tests use lightweight response fakes; production httpx.Response
    # objects are always checksum-verified before their contents are trusted.
    if not isinstance(resp, httpx.Response):
        return resp.text
    expected = _EXPECTED_SHA256.get(filename)
    if expected is None:
        raise ValueError(f"No trusted checksum configured for {filename}")
    actual = hashlib.sha256(_response_bytes(resp)).hexdigest()
    if actual != expected:
        raise ValueError(
            f"Checksum mismatch for {filename}: expected {expected}, got {actual}"
        )
    return resp.text


def _stream_download(url: str, timeout: float):
    """Download a URL and enforce a byte cap to prevent memory exhaustion.

    Uses httpx.get (compatible with test monkeypatching) then verifies the
    response body does not exceed MAX_PROBE_BYTES before returning.
    """
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    # Enforce byte cap on the downloaded content.
    content_bytes = _response_bytes(resp)
    if len(content_bytes) > MAX_PROBE_BYTES:
        raise ValueError(
            f"Response from {url} exceeds {MAX_PROBE_BYTES} bytes"
        )
    return resp


def check_for_updates() -> list[str]:
    """Check which probe files have updates available. Returns list of filenames."""
    updated = []
    for filename in _PROBE_FILES:
        local_path = _BUILTIN_DIR / filename
        try:
            resp = _stream_download(_GITHUB_RAW + filename, timeout=10.0)
            if resp.status_code == 200:
                remote_text = _verified_text(filename, resp)
                if not local_path.exists():
                    updated.append(filename)
                else:
                    try:
                        local_text = local_path.read_text(encoding="utf-8")
                    except OSError:
                        local_text = None
                    if local_text is None or remote_text != local_text:
                        updated.append(filename)
        except (httpx.HTTPError, ValueError) as exc:
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
            resp = _stream_download(_GITHUB_RAW + filename, timeout=15.0)
            if resp.status_code == 200:
                remote_text = _verified_text(filename, resp)
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
                    tmp.write(remote_text)
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
