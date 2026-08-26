#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "${1:?usage: $0 PROJECT_DIR OUTPUT_DIR [EXTRACTED_APPIMAGE_ROOT]}" && pwd -P)"
output_dir="$(mkdir -p "${2:?usage: $0 PROJECT_DIR OUTPUT_DIR [EXTRACTED_APPIMAGE_ROOT]}" && cd "$2" && pwd -P)"
app_root="${3:-}"
cleanup_root=""

if [[ -z "${app_root}" ]]; then
  bambu_image="$(readlink -f "$(command -v bambu)")"
  extraction_parent="$(mktemp -d /tmp/bambu-appimage-smoke.XXXXXX)"
  cleanup_root="${extraction_parent}"
  (
    cd "${extraction_parent}"
    "${bambu_image}" --appimage-extract >/dev/null
  )
  app_root="${extraction_parent}/squashfs-root"
fi

cleanup() {
  if [[ -n "${cleanup_root}" && "${cleanup_root}" == /tmp/bambu-appimage-smoke.* ]]; then
    rm -rf -- "${cleanup_root}"
  fi
}
trap cleanup EXIT

app_root="$(cd "${app_root}" && pwd -P)"
bambu_usr="${app_root}/usr"
compiler_bin="${bambu_usr}/compilers/clang-16/bin"
runtime_libraries="${bambu_usr}/lib:${bambu_usr}/lib/x86_64-linux-gnu:${bambu_usr}/lib64:${app_root}/lib:${app_root}/lib/x86_64-linux-gnu:${app_root}/lib64"

PATH="${compiler_bin}:/usr/bin:/bin" \
LD_LIBRARY_PATH="${runtime_libraries}" \
BAMBU_HLS="${bambu_usr}" \
BAMBU_HLS_BACKEND_PATH="${bambu_usr}/bin:/usr/bin" \
"${bambu_usr}/bin/bambu" \
  "${project_dir}/firmware/myproject.cpp" \
  --top-fname=myproject \
  -lm \
  "-I${project_dir}/firmware/ac_types" \
  --compiler=I386_CLANG16 \
  -ftemplate-depth=2048 \
  --generate-interface=INFER \
  -m64 \
  --output-temporary-directory="${output_dir}/panda-temp" \
  --output-directory="${output_dir}/synthesis"

echo "Bambu synthesis output: ${output_dir}/synthesis"
