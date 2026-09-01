#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
archive_project="${1:?usage: $0 ARCHIVE_PROJECT [OUTPUT_DIR]}"
run_dir="${2:-$(mktemp -d /tmp/hls4ml-bambu-project.XXXXXX)}"
hls4ml_root="${HLS4ML_ROOT:-/home/brend/hls4ml-dev/hls4ml-bambu/hls4ml}"

mkdir -p "$run_dir"

/home/brend/anaconda3/bin/conda run -n bambu-env --no-capture-output \
  python "$repo_dir/scripts/generate_bambu_hls4ml_project.py" \
  "$archive_project" "$run_dir" --hls4ml-root "$hls4ml_root"

clang="${BAMBU_CLANG:-/usr/bin/clang-16}"
if [[ -n "${BAMBU_ROOT:-}" && -z "${BAMBU_CLANG:-}" ]]; then
  clang="$BAMBU_ROOT/usr/compilers/clang-16/bin/clang-16"
fi

PYTHONPATH="$repo_dir/src" \
  /home/brend/anaconda3/bin/conda run -n pipeline-env --no-capture-output \
  python -m hls_ir_graph \
  "$run_dir/hls4ml-project" "$run_dir/graph-artifacts" \
  --backend bambu --clang "$clang"

echo "Reproduction output: $run_dir"
