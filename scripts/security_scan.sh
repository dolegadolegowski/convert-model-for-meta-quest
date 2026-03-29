#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[security] Running credential leak scan in worktree and git history..."

PATTERNS=(
  'ghp_[A-Za-z0-9]{30,}'
  'gho_[A-Za-z0-9]{30,}'
  'github_pat_[A-Za-z0-9_]{40,}'
  'sk-[A-Za-z0-9]{30,}'
  'AKIA[0-9A-Z]{16}'
  '-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----'
  '(?i)authorization:\s*bearer\s+[A-Za-z0-9._-]{20,}'
)

worktree_hits=""
history_hits=""

for pattern in "${PATTERNS[@]}"; do
  if hits="$(rg -n -I --hidden -S -g '!.git' -e "$pattern" . 2>/dev/null)"; then
    if [[ -n "$hits" ]]; then
      worktree_hits+=$'\n'"$hits"
    fi
  fi
done

while IFS= read -r commit; do
  for pattern in "${PATTERNS[@]}"; do
    if hits="$(git grep -n -I -E "$pattern" "$commit" -- . 2>/dev/null)"; then
      if [[ -n "$hits" ]]; then
        history_hits+=$'\n'"[$commit]"$'\n'"$hits"
      fi
    fi
  done
done < <(git rev-list --all)

if [[ -n "$worktree_hits" || -n "$history_hits" ]]; then
  echo "[security] Potential credential leaks detected."
  if [[ -n "$worktree_hits" ]]; then
    echo "[security] Worktree matches:"
    echo "$worktree_hits"
  fi
  if [[ -n "$history_hits" ]]; then
    echo "[security] History matches:"
    echo "$history_hits"
  fi
  exit 1
fi

echo "[security] Scan passed: no high-confidence credential patterns found."
exit 0
