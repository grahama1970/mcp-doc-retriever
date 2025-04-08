# Environment Setup Instructions

## 1. Create and activate virtual environment

We recommend using `uv` for fast, reproducible environments.

```bash
uv venv
source .venv/bin/activate
```

## 2. Install dependencies

```bash
uv pip sync
```

This will install all packages specified in `pyproject.toml` and `uv.lock`.

## 3. Verify environment

Run the dependency check script:

```bash
python scripts/check_deps.py
```

It will verify:

- Virtual environment is active
- Required packages (`httpx`, `aiofiles`) are installed with correct versions
- Package visibility between `uv` and Python interpreter

## 4. Running tests

Use:

```bash
python test_downloader.py
python -m pytest test_downloader_security.py
```

## Notes

- Always activate the virtual environment before running or developing.
- If you see warnings about package visibility, re-activate the virtualenv.
- Avoid running outside the virtualenv to prevent conflicts.
