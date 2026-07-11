# GitHub Branch Protection — master (`tenderscope-kg`)

**Status:** Required for Phase 2 production integration  
**Repo:** `paatasurf/tenderscope-kg`  
**Protected branch:** `master`  
**Visibility:** Public (classic branch protection available)

## Required settings

| Setting | Value |
|---------|--------|
| Require a pull request before merging | **Yes** |
| Require status checks to pass | **Yes** |
| Required checks | **Quality Gate** only |
| Require branches to be up to date | Recommended: Yes |
| Include administrators | **Yes** |
| Allow force pushes | **No** |
| Allow deletions | **No** |

## Apply via GitHub UI

1. Repo → **Settings** → **Branches** → **Add rule** / ruleset  
2. Branch name pattern: `master`  
3. Require status checks → select **Quality Gate** (after first workflow run)  
4. Require a pull request before merging  
5. Disable force pushes / deletions  
6. Save

## Apply via API (admin)

```bash
gh api -X PUT repos/paatasurf/tenderscope-kg/branches/master/protection \
  -H "Accept: application/vnd.github+json" \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Quality Gate"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

## Verification

- Open a PR with a failing test → merge blocked / status red  
- Direct `git push origin master` → rejected  
