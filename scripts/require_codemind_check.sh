#!/usr/bin/env bash
# CodeMind — make the `CodeMind / memory` check a REQUIRED status check on main,
# so a contradictory PR cannot merge until reconciled.
#
# PREREQ: merge PR #6 first (the "green check on clean PRs" feature). Without it,
# clean PRs to main get no CodeMind check at all, and requiring the check would
# block every PR. After #6 is on main, clean PRs get a green CodeMind check and
# conflicting PRs get a red one — so requiring the check is safe.
#
# REQUIRES: admin on the repo (the GITHUB_TOKEN/gh user must be a repo admin).
# USAGE:   bash scripts/require_codemind_check.sh [owner/repo]
#          (defaults to the current repo from `gh repo view`)
set -euo pipefail
REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
CONTEXT="CodeMind / memory"
echo "==> Requiring '$CONTEXT' on $REPO:main (requires admin + PR #6 merged)"

# Branch protection API requires the check to have run at least once on the branch
# so GitHub knows the context. The CodeMind workflow posts the status on every PR,
# so it will be known once any PR has run against main.
gh api -X PUT "repos/$REPO/branches/main/protection" \
  -H "Accept: application/vnd.github+json" \
  -f required_status_checks[strict]=true \
  -f required_status_checks[contexts][]="$CONTEXT" \
  -f enforce_admins=false \
  -f required_pull_request_reviews[required_approving_review_count]=0 \
  -f restrictions= \
  2>&1 | head -5

echo "==> Done. A red CodeMind check now blocks merge; green allows it."
echo "    Verify: https://github.com/$REPO/branches  (protection rule on main)"