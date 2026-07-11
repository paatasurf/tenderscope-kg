# Phase 2 — Deployment with Quality Gate (`tenderscope-kg`)

## Current deploy path (unchanged)

```
developer → PR / push → GitHub master → Railway auto-deploy → healthcheck
```

Phase 2 **adds** a GitHub Actions Quality Gate on the same events. It does **not** replace Railway builders or start commands.

## Target safe path

```
PR opened
  → Quality Gate (GitHub Actions) must pass
  → Branch protection blocks merge if red
  → Merge to master
  → Quality Gate runs on push
  → Railway Wait-for-CI succeeds
  → Railway build + deploy
  → /api/graph/health
```

## Health path

| Service | Health path |
|---------|-------------|
| tenderscope-kg | `/api/graph/health` |

## Operator checklist (one-time)

### GitHub

1. Settings → Branches → Protect `master`
2. Require status checks: **Quality Gate**
3. Enforce for admins (recommended)
4. Disallow force pushes / deletions

### Railway

1. Open **tenderscope-kg** service → Settings → GitHub
2. Enable **Wait for CI**
3. Confirm deploy branch remains `master`
4. Do **not** inject production `DATABASE_URL` into GitHub Actions

## Deployment readiness artifact

Each workflow uploads `reports/` including `ci-report.md` with:

- passed / failed / error counts
- execution time
- coverage line-rate (when available)
- **Ready for deploy: YES/NO**

## Manual dispatch

Actions → **Quality Gate** → Run workflow.
