#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
PACKAGE="$ROOT/integrations/leanhammer/.lake/packages/jevhammer_benchmark"
EXPECTED_REV="fc5f5ea5aed665918f7cc4d84fd64408f8d0c2e2"
PATCH="$ROOT/patches/nojev-random-norepeat.patch"

git -C "$PACKAGE" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "pinned JevHammer package is missing; run lake exe cache get first" >&2
  exit 1
}
actual_rev="$(git -C "$PACKAGE" rev-parse HEAD)"
test "$actual_rev" = "$EXPECTED_REV" || {
  echo "unexpected JevHammer revision: $actual_rev" >&2
  exit 1
}

if git -C "$PACKAGE" diff --quiet -- JevHammerBenchmark/Ranker.lean; then
  git -C "$PACKAGE" apply --check "$PATCH"
  git -C "$PACKAGE" apply "$PATCH"
elif grep -Fq 'def noJevRandomNorepeat' "$PACKAGE/JevHammerBenchmark/Ranker.lean" \
    && grep -Fq 'NOJEV_RANKER' "$PACKAGE/JevHammerBenchmark/Ranker.lean" \
    && [ "$(git -C "$PACKAGE" diff --name-only | wc -l | tr -d ' ')" = 1 ]; then
  echo "no-Jev random-norepeat patch is already applied"
else
  echo "Ranker.lean has an unexpected local change; refusing to overwrite it" >&2
  exit 1
fi

grep -Fq 'def noJevRandomNorepeat' "$PACKAGE/JevHammerBenchmark/Ranker.lean"
echo "applied no-Jev ranker patch to pinned dependency $EXPECTED_REV"
