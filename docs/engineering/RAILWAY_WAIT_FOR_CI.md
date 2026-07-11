# Railway — Wait for CI (`tenderscope-kg`)

**Service:** `tenderscope-kg`  
**Repo:** `paatasurf/tenderscope-kg`  
**Deploy branch:** `master`

## Required setting

In Railway dashboard:

1. Open service **tenderscope-kg** → **Settings** → **Source** / **GitHub**  
2. Confirm connected repo + branch `master`  
3. Enable **Wait for CI**  
4. Ensure GitHub App permissions for Railway are accepted (Check suites / commit statuses)

## Expected behavior

| GitHub CI | Railway |
|-----------|---------|
| Workflows running | Deployment **WAITING** |
| Quality Gate **fails** | Deployment **SKIPPED** |
| Quality Gate **passes** | Deployment proceeds |

## Requirements

- Workflow exists in the repo  
- Workflow runs on **`push`** to `master` (included in `quality-gate.yml`)

## CLI note

Railway CLI does not expose a documented Wait-for-CI toggle. Enable in the dashboard.

## Verification

1. Red PR cannot merge (branch protection) → Railway never sees failing commit on `master`  
2. Green merge → Railway deploys after Wait-for-CI succeeds  
3. Health: `/api/graph/health`  
