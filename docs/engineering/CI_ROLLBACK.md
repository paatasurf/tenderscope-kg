# Phase 2 — Rollback (`tenderscope-kg`)

## If Quality Gate blocks a good change (false failure)

1. Inspect Actions logs + downloaded `ci-reports-kg-*` artifact.
2. Prefer **fix the test or code** — do not disable the gate.
3. Emergency only: temporary admin bypass of branch protection (document why, restore within 24h).

## If a bad build reached Railway despite CI

CI did not gate deploy (Wait-for-CI / branch protection missing). Fix ops config first.

### Application rollback (Railway)

1. Railway → tenderscope-kg → Deployments → **Rollback** to last known-good deployment.
2. Confirm `/api/graph/health` green.

### Git rollback (if master itself is bad)

```powershell
git revert <bad-sha>
git push origin master
```

Prefer revert over force-push. **Never** force-push `master` unless explicitly approved.

## If CI itself is broken (workflow YAML)

1. Fix workflow on a branch; merge via PR once Actions can run.
2. If Actions cannot start, use Railway rollback for runtime; fix workflow next.

## What not to roll back

- Do not point CI at production `DATABASE_URL`.
- Do not disable the Quality Gate to “unblock” a deploy.
