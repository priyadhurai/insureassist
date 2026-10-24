# ADR-0010: Python 3.12 environment and dependencies via uv

- Status: Proposed (**needs owner decision, OD-02**)
- Date: 2026-10-18

## Context
The machine has Python 3.13.9 (Anaconda, default on PATH) and 3.14, but **not 3.12**, which the stack requires. `uv` 0.12.17 is installed.

## Decision
- `uv python install 3.12` (isolated, does not touch Anaconda) with `.python-version` = `3.12`.
- `pyproject.toml` (PEP 621) + a committed `uv.lock` for reproducible installs; `uv sync`, `uv run pytest`.
- Docker images use `python:3.12-slim` and install from the lock file.
- Ruff, mypy and pytest configuration lives in `pyproject.toml`.

## Consequences
- Fast, reproducible environments with a lock file (supports the dependency-audit control).
- Contributors need uv. The README documents the `python -m venv` + `pip install -e .[dev]` alternative.

## Alternatives considered
- `venv` + `pip` + `requirements*.txt` (pip-tools): no new tool, but a manual lock workflow, and installing 3.12 separately.
- Conda env: already present, but mixing conda and pip wheels for torch/faiss is a common source of breakage.
