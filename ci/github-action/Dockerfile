# =============================================================================
# AgentBench — Dockerfile for container-based GitHub Action (optional)
# =============================================================================
#
# This Dockerfile creates a self-contained environment with AgentBench
# pre-installed. It can be used as an alternative to the composite action
# when you prefer a container-based execution model.
#
# Build & test locally:
#   docker build -t agentbench-action .
#   docker run --rm -v $(pwd)/tests:/tests agentbench-action run /tests
#
# =============================================================================

FROM python:3.11-slim AS base

LABEL org.opencontainers.image.title="AgentBench GitHub Action"
LABEL org.opencontainers.image.description="Behavioral testing framework for AI agents"
LABEL org.opencontainers.image.source="https://github.com/EdList/agentbench"

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system deps (minimal — agentbench is pure Python)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Install agentbench from PyPI
# For a local build, copy the source and use `pip install .` instead.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir agentbench

# Create a non-root user for running tests
RUN useradd --create-home agentbench
USER agentbench
WORKDIR /home/agentbench/tests

# Default entrypoint — can be overridden in action.yml
ENTRYPOINT ["agentbench"]
CMD ["run", "."]
