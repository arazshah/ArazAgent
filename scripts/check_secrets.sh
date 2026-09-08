#!/usr/bin/env bash
# Grep-based last line of defense against committing secrets.
# Scans the git staging area (or, with --all, the working tree) for patterns
# that look like live credentials. Abort the commit on any hit.
set -euo pipefail

if [[ "${1:-}" == "--all" ]]; then
    files=$(git ls-files)
else
    files=$(git diff --cached --name-only --diff-filter=ACM)
fi

if [[ -z "$files" ]]; then
    exit 0
fi

patterns=(
    'sk-[A-Za-z0-9]{16,}'
    'Bearer [A-Za-z0-9._-]{16,}'
    'tapi\.bale\.ai/bot[0-9A-Za-z_-]{8,}'
    '[A-Za-z0-9+/]{64,}={0,2}'
)

hit=0
for f in $files; do
    [[ -f "$f" ]] || continue
    case "$f" in
        .env.example|*.lock|*.svg|*.png|*.jpg|*.jpeg) continue ;;
    esac
    for pat in "${patterns[@]}"; do
        if grep -InE "$pat" -- "$f" 2>/dev/null; then
            echo "check_secrets: possible secret matched pattern '$pat' in $f" >&2
            hit=1
        fi
    done
done

if [[ "$hit" -ne 0 ]]; then
    echo "check_secrets: aborting — review the matches above before committing." >&2
    exit 1
fi

exit 0
