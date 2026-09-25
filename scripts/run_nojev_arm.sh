#!/usr/bin/env bash
# Run one arm's six shipped batches in order. Invoke inside a hard-capped cgroup.
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <given|random|hybrid-fallback> <batch-dataset-dir> <output-dir>" >&2
  exit 2
fi

ROOT="$(git rev-parse --show-toplevel)"
ARM="$1"
DATASET_DIR="$(cd "$2" && pwd)"
OUTPUT_DIR="$3"
case "$OUTPUT_DIR" in /*) ;; *) OUTPUT_DIR="$ROOT/$OUTPUT_DIR" ;; esac
case "$ARM" in given|random|hybrid-fallback) ;; *) echo "unknown arm: $ARM" >&2; exit 2 ;; esac

export PATH="$HOME/.elan/bin:$PATH"
unset TYPESAFE_API_KEY || true
export JEVSELECTOR_PUBLIC_INDEX="$ROOT/artifacts/confirmation/index.json"
test -f "$JEVSELECTOR_PUBLIC_INDEX" || { echo "missing prepared selector index" >&2; exit 1; }
PACKAGE="$ROOT/integrations/leanhammer/.lake/packages/jevhammer_benchmark"
if [ "$ARM" != given ]; then
  grep -Fq 'def noJevRandomNorepeat' "$PACKAGE/JevHammerBenchmark/Ranker.lean" || {
    echo "random-norepeat patch is missing from the pinned dependency" >&2
    exit 1
  }
fi

memory_max="$(cat /sys/fs/cgroup/memory.max)"
swap_max="$(cat /sys/fs/cgroup/memory.swap.max)"
test "$memory_max" = "16000000000" || { echo "expected 16,000,000,000-byte cgroup cap, observed $memory_max" >&2; exit 1; }
test "$swap_max" = "0" || { echo "expected zero-swap cgroup, observed $swap_max" >&2; exit 1; }

test ! -e "$OUTPUT_DIR" || { echo "refusing to overwrite $OUTPUT_DIR" >&2; exit 1; }
mkdir -p "$OUTPUT_DIR"
export NOJEV_STATS="$OUTPUT_DIR/ranker-stats.jsonl"
export NOJEV_DUMP="$OUTPUT_DIR/ranker-examples.jsonl"
if [ "$ARM" = given ]; then
  unset NOJEV_RANKER || true
else
  export NOJEV_RANKER=random-norepeat
fi

for batch in 0 1 2 3 4 5; do
  dataset="$DATASET_DIR/batch-$batch.json"
  test -f "$dataset" || { echo "missing $dataset" >&2; exit 1; }
  python3 -m jevhammer_benchmark run \
    --project "$ROOT/integrations/leanhammer" \
    --dataset "$dataset" \
    --methods LeanHammerComparison.cpu \
    --mock --config '{}' \
    --memory-limit 16000000000 \
    --threads 2 --heartbeats 200000 --timeout 900 \
    --output "$OUTPUT_DIR/batch-$batch" \
    >"$OUTPUT_DIR/batch-$batch.log" 2>&1
  echo "completed $ARM batch $batch"
done

echo "completed $ARM at $(date -u +%FT%TZ)"
