#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "${1:?usage: $0 PROJECT_DIR OUTPUT_DIR [EXTRACTED_APPIMAGE_ROOT] [SOURCE_FILE]}" && pwd -P)"
output_dir="$(mkdir -p "${2:?usage: $0 PROJECT_DIR OUTPUT_DIR [EXTRACTED_APPIMAGE_ROOT] [SOURCE_FILE]}" && cd "$2" && pwd -P)"
app_root="${3:-${BAMBU_ROOT:-}}"
source_file="${4:-firmware/myproject.cpp}"
cleanup_root=""

if [[ -z "$app_root" ]]; then
  bambu_image="$(readlink -f "$(command -v bambu)")"
  extraction_parent="$(mktemp -d /tmp/bambu-appimage-metadata.XXXXXX)"
  cleanup_root="$extraction_parent"
  (
    cd "$extraction_parent"
    "$bambu_image" --appimage-extract >/dev/null
  )
  app_root="$extraction_parent/squashfs-root"
fi

cleanup() {
  if [[ -n "$cleanup_root" && "$cleanup_root" == /tmp/bambu-appimage-metadata.* ]]; then
    rm -rf -- "$cleanup_root"
  fi
}
trap cleanup EXIT

app_root="$(cd "$app_root" && pwd -P)"
bambu_usr="$app_root/usr"
compiler="$bambu_usr/compilers/clang-16/bin/clang++-16"
plugin_dir="$bambu_usr/lib/panda/clang-16"
panda_include="$bambu_usr/include/panda"
source_path="$project_dir/$source_file"
top_fname="${TOP_FNAME:-$(basename "$source_file" .cpp)}"
metadata_dir="$output_dir/panda-temp"
mkdir -p "$metadata_dir"

if [[ ! -f "$source_path" ]]; then
  echo "source file not found: $source_path" >&2
  exit 1
fi
if [[ ! -x "$compiler" ]]; then
  echo "Bambu Clang 16 not found under: $app_root" >&2
  exit 1
fi

export BAMBU_HLS="$bambu_usr"
export BAMBU_HLS_BACKEND_PATH="$bambu_usr/bin:/usr/bin"
export PATH="$bambu_usr/compilers/clang-16/bin:$bambu_usr/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$bambu_usr/lib:$bambu_usr/lib/x86_64-linux-gnu:$bambu_usr/lib64:$app_root/lib:$app_root/lib/x86_64-linux-gnu:$app_root/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

ulimit -s 131072
"$compiler" \
  -fplugin="$plugin_dir/ASTAnalyzer.so" \
  -fpass-plugin="$plugin_dir/customSROA.so" -Xclang -load -Xclang "$plugin_dir/customSROA.so" \
  -fpass-plugin="$plugin_dir/topfname.so" -Xclang -load -Xclang "$plugin_dir/topfname.so" \
  -fpass-plugin="$plugin_dir/expandMemOps.so" -Xclang -load -Xclang "$plugin_dir/expandMemOps.so" \
  -fpass-plugin="$plugin_dir/dumpBambuIrSSACpp.so" -Xclang -load -Xclang "$plugin_dir/dumpBambuIrSSACpp.so" \
  -c -D__NO_INLINE__ -m64 -Xclang -no-opaque-pointers \
  -include "$panda_include/csroa_directives.h" \
  -Xclang -add-plugin -Xclang ASTAnalyzer \
  -Xclang -plugin-arg-ASTAnalyzer -Xclang -action \
  -Xclang -plugin-arg-ASTAnalyzer -Xclang analyze \
  -Xclang -plugin-arg-ASTAnalyzer -Xclang -outputdir \
  -Xclang -plugin-arg-ASTAnalyzer -Xclang "$metadata_dir" \
  -Xclang -plugin-arg-ASTAnalyzer -Xclang -cppflag \
  -Xclang -plugin-arg-ASTAnalyzer -Xclang -action \
  -Xclang -plugin-arg-ASTAnalyzer -Xclang optimize \
  -mllvm -panda-outputdir-csroa="$metadata_dir" \
  -mllvm -panda-TFN-csroa="$top_fname" \
  -mllvm -internalize-outputdir="$metadata_dir" \
  -mllvm -panda-TFN="$top_fname" \
  -mllvm -add-noalias -mllvm -panda-Internalize \
  -O2 -mllvm -enable-loop-flatten \
  -fno-builtin-bcmp -fno-builtin-memcpy -fno-builtin-memset \
  -fno-exceptions -ffp-contract=off -finline-functions \
  -fno-slp-vectorize -fno-stack-protector -fno-unroll-loops \
  -fno-use-cxa-atexit -fno-vectorize -fwrapv --std=c++14 \
  -D__BAMBU__ -D__SYNTHESIS__ -isystem "$panda_include" \
  -mllvm -panda-outputdir="$metadata_dir" \
  -mllvm -panda-infile="$source_path" -mllvm -panda-topfname="$top_fname" \
  -o "$metadata_dir/$top_fname.o" "$source_path" \
  >"$metadata_dir/frontend.stdout" 2>"$metadata_dir/frontend.stderr"

for artifact in architecture.xml "$top_fname.cpp.bambuir"; do
  if [[ ! -s "$metadata_dir/$artifact" ]]; then
    echo "Bambu frontend did not produce $metadata_dir/$artifact" >&2
    exit 1
  fi
done

echo "Bambu metadata output: $metadata_dir"
