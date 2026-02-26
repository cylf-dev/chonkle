# Contributing

## Prerequisites

- **Python 3.14+**
- **[uv](https://docs.astral.sh/uv/)** — Python package and project manager

## Setup

Clone the repo and install dependencies:

```bash
git clone git@github.com:cylf-dev/chonkle.git
cd chonkle
uv sync --all-extras
```

This creates a virtualenv in `.venv/` and installs all dependencies (including dev tools and the `cog` extra needed for the full test suite).

Set up pre-commit hooks:

```bash
uv run pre-commit install
```

## Running tests

```bash
uv run pytest tests/ -v
```

## Linting and formatting

Ruff is used for both linting and formatting:

```bash
uv run ruff check .
uv run ruff format .
```

Type checking:

```bash
uv run mypy src/
```

Pre-commit runs all of these automatically on staged files.
