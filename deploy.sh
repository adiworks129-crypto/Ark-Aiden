#!/usr/bin/env bash
#
# Ark deploy script — stage, test, commit, and push to GitHub.
#
# Usage:
#   ./deploy.sh                              # timestamped commit message, tests run first
#   ./deploy.sh "Fix calibration edge case"  # your own commit message
#   ./deploy.sh --skip-tests "quick doc fix" # push without running the test suite first
#   ./deploy.sh --branch develop "message"   # push to a branch other than the current one
#
# What this does, in order, and why:
#   1. Confirms you're inside a git repo with an `origin` remote configured —
#      refuses to guess at either, since pushing to the wrong place is exactly
#      the kind of mistake a deploy script should make impossible.
#   2. Runs the full test suite (python -m unittest discover -s tests) unless
#      --skip-tests is given. Aborts the whole deploy if any test fails — this
#      script's one job is to make sure nothing broken ever reaches GitHub by
#      accident.
#   3. Stages every change (`git add -A`).
#   4. Commits, but only if something is actually staged — a clean working tree
#      is not an error, it just means there's nothing new to push.
#   5. Pushes to `origin` on the target branch (the branch you're currently on,
#      unless --branch overrides it), using `-u` so the upstream is set/kept on
#      the first push.
#
# This script never touches credentials itself — it relies entirely on
# whatever git/GitHub authentication is already configured on the machine
# it's run from (a saved credential helper, an SSH key, `gh auth login`,
# Git Credential Manager on Windows, etc.). If `git push` prompts you to log
# in, that's your normal GitHub auth flow, not this script.

set -euo pipefail

SKIP_TESTS=0
BRANCH=""
COMMIT_MESSAGE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-tests)
      SKIP_TESTS=1
      shift
      ;;
    --branch)
      BRANCH="${2:-}"
      if [ -z "$BRANCH" ]; then
        echo "error: --branch requires a branch name argument." >&2
        exit 1
      fi
      shift 2
      ;;
    *)
      if [ -n "$COMMIT_MESSAGE" ]; then
        echo "error: unexpected extra argument '$1' (commit message already set to '$COMMIT_MESSAGE')." >&2
        exit 1
      fi
      COMMIT_MESSAGE="$1"
      shift
      ;;
  esac
done

if [ -z "$COMMIT_MESSAGE" ]; then
  COMMIT_MESSAGE="Deploy: $(date -u +'%Y-%m-%d %H:%M:%S UTC')"
fi

# --- 1. Sanity checks -------------------------------------------------------

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: not inside a git repository. Run this script from within the Ark repo." >&2
  exit 1
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "error: no 'origin' remote configured. Set one first, e.g.:" >&2
  echo "  git remote add origin https://github.com/<you>/<repo>.git" >&2
  exit 1
fi

if [ -z "$BRANCH" ]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
fi

ORIGIN_URL="$(git remote get-url origin)"
echo "Repo:   $(git rev-parse --show-toplevel)"
echo "Origin: $ORIGIN_URL"
echo "Branch: $BRANCH"
echo

# --- 2. Test suite (the actual safety gate) --------------------------------

if [ "$SKIP_TESTS" -eq 1 ]; then
  echo "Skipping test suite (--skip-tests given)."
else
  echo "Running test suite before deploying..."
  if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  else
    PYTHON=python
  fi

  if ! "$PYTHON" -m unittest discover -s tests; then
    echo
    echo "error: test suite failed — aborting deploy. Nothing was pushed." >&2
    echo "Fix the failure, or re-run with --skip-tests if you're sure this is safe." >&2
    exit 1
  fi
  echo "Test suite passed."
  echo
fi

# --- 3 & 4. Stage + commit ---------------------------------------------------

git add -A

if git diff --cached --quiet; then
  echo "Nothing staged — working tree matches the last commit. Nothing to commit."
else
  git commit -m "$COMMIT_MESSAGE"
  echo "Committed: $COMMIT_MESSAGE"
fi
echo

# --- 5. Push -----------------------------------------------------------------

echo "Pushing '$BRANCH' to origin..."
git push -u origin "$BRANCH"
echo
echo "Done."
