# Phase 2 CI — Developer Guide (`tenderscope-kg`)

## What CI does

On every **pull request** and **push** to `master` (and **workflow_dispatch**), GitHub Actions runs **Quality Gate**:

1. Clean Python 3.12 environment  
2. Install editable package with `[dev,rest]` + `pytest-cov`  
3. Syntax check (`compileall` on `src/tenderscope_kg`)  
4. Import smoke for core packages  
5. Formatting / ruff (**advisory**)  
6. **Full `tests/` suite** — **blocking**  
7. Publishes `reports/ci-report.md` + JUnit + coverage artifacts  

## How to run the same checks locally

```powershell
cd C:\Users\DAVIDSURF\Projects\tenderscope-kg
pip install -e ".[dev,rest]" pytest-cov

python -m compileall -q src/tenderscope_kg
pytest tests -q --tb=short
```

Optional with coverage:

```powershell
pytest tests -q --cov=tenderscope_kg --cov-report=term-missing
```

## How developers should use the workflow

1. Open a PR against `master`.  
2. Wait for **Quality Gate** (green check).  
3. Do not merge red PRs.  
4. Download the `ci-reports-kg-*` artifact if needed.  
5. Fix failing **tests** or **code**; do not disable the workflow.

## Files

| Path | Role |
|------|------|
| `.github/workflows/quality-gate.yml` | Pipeline |
| `pyproject.toml` | Runtime + `[dev,rest]` extras |
| `scripts/ci_report_summary.py` | Report generator |
| `docs/engineering/` | Engineering docs |
