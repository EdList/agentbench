"""Docker sandbox manager for isolated agent execution."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

from agentbench.core.config import SandboxConfig


@dataclass
class SandboxResult:
    """Result from a sandbox execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    container_id: str | None = None


class SandboxManager:
    """Manages Docker sandboxes for isolated agent test execution.

    Creates ephemeral containers for each test run, with configurable
    resource limits, network access, and automatic cleanup.
    """

    def __init__(self, config: SandboxConfig | None = None):
        self._config = config or SandboxConfig()
        self._client = None

    @property
    def client(self):
        """Lazy-initialize Docker client."""
        if self._client is None:
            try:
                import docker

                self._client = docker.from_env()
            except ImportError:
                raise ImportError(
                    "Docker package not installed. Install with: pip install agentbench[docker]"
                )
            except Exception as e:
                raise RuntimeError(f"Could not connect to Docker: {e}")
        return self._client

    def run_agent(
        self,
        agent_code: str,
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> SandboxResult:
        """Run agent code in an isolated Docker container.

        Args:
            agent_code: Python code to execute in the container.
            env: Environment variables to pass to the container.
            timeout: Override timeout in seconds.

        Returns:
            SandboxResult with output and timing.
        """
        timeout = timeout or self._config.timeout_seconds
        start = time.time()
        container = None

        try:
            # Don't use remove=True — we need to read logs before the container
            # is removed (Docker SDK race condition with remove=True + wait).
            container = self.client.containers.run(
                image=self._config.image,
                command=["python", "-c", agent_code],
                environment=env or {},
                mem_limit=self._config.memory_limit,
                network_mode="bridge" if self._config.network_enabled else "none",
                detach=True,
                stdout=True,
                stderr=True,
            )

            try:
                result = container.wait(timeout=timeout)
                exit_code = result.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            except Exception:
                try:
                    container.kill()
                except Exception:
                    pass
                return SandboxResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Container timed out after {timeout}s",
                    duration_ms=(time.time() - start) * 1000,
                    container_id=container.id if container else None,
                )

            return SandboxResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=(time.time() - start) * 1000,
                container_id=container.id,
            )

        except Exception as e:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Sandbox error: {e}",
                duration_ms=(time.time() - start) * 1000,
            )
        finally:
            # Clean up the container
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def build_image(self, dockerfile: str | None = None) -> str:
        """Build the sandbox Docker image."""
        if dockerfile is None:
            dockerfile = self._default_dockerfile()

        try:
            # docker.images.build() requires a file-like object for fileobj=
            dockerfile_bytes = io.BytesIO(dockerfile.encode("utf-8"))
            image, logs = self.client.images.build(
                fileobj=dockerfile_bytes,
                tag=self._config.image,
                rm=True,
            )
            for log in logs:
                if "stream" in log:
                    print(log["stream"].strip())
            return image.id
        except Exception as e:
            raise RuntimeError(f"Failed to build sandbox image: {e}")

    @staticmethod
    def _default_dockerfile() -> str:
        """Return the default Dockerfile content for the sandbox."""
        return """FROM python:3.11-slim

RUN pip install --no-cache-dir \\
    httpx pyyaml

WORKDIR /agent
COPY . .

CMD ["python", "-c", "print('AgentBench sandbox ready')"]
"""
