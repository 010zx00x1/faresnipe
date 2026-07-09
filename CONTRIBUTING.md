# Contributing

Thanks for helping improve faresnipe.

## Dev setup

```bash
python -m venv .venv
.venv/bin/pip install -e .[dev]
```

## Checks

Run tests:

```bash
python -m unittest discover -s tests
```

Run lint:

```bash
ruff check src/ tests/
```

Build the package:

```bash
python -m build
twine check dist/*
```

## Add a route

Edit `config/origins.example.toml` with the origin, destination, and useful thresholds, then open a PR. Keep routes practical and explain why the route matters.

## Add a provider

Provider work lives in `src/faresnipe/providers/experimental/` until it is proven reliable. Implement the provider there, register it in `src/faresnipe/providers/__init__.py`, and add focused tests in `tests/experimental/`.

## Pull requests

Keep PRs small, include tests for behavior changes, and avoid mixing route updates with provider or CLI changes.
