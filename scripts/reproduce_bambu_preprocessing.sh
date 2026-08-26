#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
workspace_dir="$(cd "${repo_dir}/.." && pwd -P)"
reference_config="${1:-${workspace_dir}/data/source/2layer/archive_1/0162624c-ca32-4e05-a8be-48c9d355ef4b/hls4ml_config.yml}"
run_dir="${2:-$(mktemp -d /tmp/hls4ml-bambu-preprocess.XXXXXX)}"
project_dir="${run_dir}/bambu-project"
artifact_dir="${run_dir}/artifacts"

/home/brend/anaconda3/bin/conda run -n bambu-env --no-capture-output \
  python "${repo_dir}/scripts/generate_bambu_smoke_project.py" \
  "${project_dir}" --reference-config "${reference_config}"

PYTHONPATH="${repo_dir}/src" \
  /home/brend/anaconda3/bin/conda run -n pipeline-env --no-capture-output \
  python -m hls_ir_graph \
  "${project_dir}" "${artifact_dir}" \
  --backend bambu --clang /usr/bin/clang-16

echo "Reproduction output: ${run_dir}"
