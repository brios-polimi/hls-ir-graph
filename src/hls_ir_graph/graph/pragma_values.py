"""Resolve symbolic numeric pragma arguments with the Vitis C++ frontend."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess
import tempfile

from ..frontends.common import _remove_generated_weight_initializers
from ..frontends.vitis import (
    _remove_unsupported_resource_pragmas,
    _vitis_args,
    _vitis_env,
)
from ..config import PreprocessConfig


_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_LOCAL_CONSTANT_RE = re.compile(
    r"(?m)^[ \t]*(?P<declaration>"
    r"(?:static\s+)?(?:const|constexpr)\b"
    r"[^;=]*?\b(?P<name>[A-Za-z_]\w*)\s*=\s*[^;]+;)"
)
_PROBE_VALUE_RE = re.compile(
    r"^@__hls4ml_pragma_value_(?P<index>\d+)\s*=.*?"
    r"\bconstant\s+i64\s+(?P<value>-?\d+)\b",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SourceFunction:
    template_parameters: str
    parameter_names: tuple[str, ...]
    local_constants: tuple[str, ...]
    uses_hls_stream: bool


@dataclass(frozen=True)
class ResolutionRequest:
    node: dict
    key: str
    value_index: int
    expression: str
    source: SourceFunction
    template_arguments: tuple[str, ...]


def _split_top_level(value: str) -> tuple[str, ...]:
    pieces: list[str] = []
    start = 0
    angle = 0
    paren = 0
    for index, character in enumerate(value):
        if character == "<":
            angle += 1
        elif character == ">":
            angle = max(angle - 1, 0)
        elif character == "(":
            paren += 1
        elif character == ")":
            paren = max(paren - 1, 0)
        elif character == "," and angle == 0 and paren == 0:
            pieces.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        pieces.append(tail)
    return tuple(pieces)


def _template_parameter_names(parameters: str) -> tuple[str, ...]:
    names: list[str] = []
    for parameter in _split_top_level(parameters):
        without_default = parameter.split("=", 1)[0].strip()
        match = re.search(r"([A-Za-z_]\w*)\s*$", without_default)
        if match:
            names.append(match.group(1))
    return tuple(names)


@lru_cache(maxsize=None)
def source_function(
    path: str | Path,
    line: int,
    function: str,
) -> SourceFunction | None:
    """Find the template and preceding local constants owning a pragma line."""

    source = Path(path)
    if not source.is_file() or line < 1:
        return None
    lines = source.read_text(errors="replace").splitlines(keepends=True)
    if line > len(lines):
        return None
    prefix = "".join(lines[:line])
    function_matches = list(re.finditer(rf"\b{re.escape(function)}\s*\(", prefix))
    if not function_matches:
        return None
    function_start = function_matches[-1].start()
    templates = list(
        re.finditer(r"template\s*<(?P<parameters>[^<>]*)>", prefix[:function_start])
    )
    if not templates:
        return None
    template = templates[-1]
    brace = prefix.find("{", function_start)
    if brace < 0:
        return None
    parameters = template.group("parameters").strip()
    header = prefix[template.start():brace]
    body = prefix[brace + 1 :]
    return SourceFunction(
        template_parameters=parameters,
        parameter_names=_template_parameter_names(parameters),
        local_constants=tuple(
            match.group("declaration").strip()
            for match in _LOCAL_CONSTANT_RE.finditer(body)
        ),
        uses_hls_stream="hls::stream" in header,
    )


def concrete_template_arguments(
    demangled: str,
    function: str,
) -> tuple[str, ...]:
    """Extract the concrete top-level template arguments for ``function``."""

    marker = f"{function}<"
    start = demangled.find(marker)
    if start < 0:
        return ()
    start += len(marker)
    depth = 1
    for index in range(start, len(demangled)):
        character = demangled[index]
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth == 0:
                return _split_top_level(demangled[start:index])
    return ()


def source_matches_instantiation(
    source: SourceFunction | None,
    demangled: str,
    function: str,
) -> bool:
    """Conservatively distinguish stream and non-stream overloads."""

    if source is None:
        return True
    arguments = concrete_template_arguments(demangled, function)
    return (
        len(arguments) == len(source.parameter_names)
        and source.uses_hls_stream == ("hls::stream" in demangled)
    )


def _is_number(value: str) -> bool:
    return bool(_NUMBER_RE.fullmatch(value.strip()))


def _probe_source(index: int, request: ResolutionRequest) -> str:
    declarations = "\n  ".join(request.source.local_constants)
    if declarations:
        declarations = f"  {declarations}\n"
    arguments = ", ".join(request.template_arguments)
    return (
        f"template <{request.source.template_parameters}>\n"
        f"constexpr long long __hls4ml_resolve_{index}() {{\n"
        f"{declarations}"
        f"  return static_cast<long long>({request.expression});\n"
        f"}}\n"
        f'extern "C" const long long __hls4ml_pragma_value_{index} = '
        f"__hls4ml_resolve_{index}<{arguments}>();\n"
    )


def _compile_requests(
    requests: list[ResolutionRequest],
    project_dir: Path,
    cfg: PreprocessConfig,
) -> dict[int, str]:
    firmware = (project_dir / cfg.hls_source_file).resolve().parent
    headers = (firmware / "myproject.h", firmware / "parameters.h")
    missing = [str(path) for path in headers if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "HLS config header(s) not found for pragma resolution: "
            + ", ".join(missing)
        )

    probes = "\n".join(
        _probe_source(index, request)
        for index, request in enumerate(requests)
    )
    wrapper_text = "".join(f'#include "{header}"\n' for header in headers) + probes
    args = _vitis_args(project_dir, cfg)
    env = _vitis_env(cfg)
    with tempfile.TemporaryDirectory(prefix="vitis-pragma-values-") as temp:
        work = Path(temp)
        wrapper = work / "pragma_values.cpp"
        preprocessed = work / "pragma_values.pp.cpp"
        output = work / "pragma_values.ll"
        wrapper.write_text(wrapper_text)

        preprocess = subprocess.run(
            [
                cfg.vitis_clang_binary,
                str(wrapper),
                "-E",
                "-std=c++0x",
                *args,
                "-o",
                str(preprocessed),
            ],
            cwd=project_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=cfg.vitis_timeout_seconds,
        )
        if preprocess.returncode:
            raise RuntimeError(
                "pragma value preprocessing failed: "
                + " ".join(preprocess.stderr.strip().split())[-1000:]
            )
        _remove_unsupported_resource_pragmas(preprocessed)
        _remove_generated_weight_initializers(preprocessed)

        compile_result = subprocess.run(
            [
                cfg.vitis_clang_binary,
                str(preprocessed),
                "-S",
                "-emit-llvm",
                "-std=c++0x",
                *args,
                "-Xclang",
                "-no-opaque-pointers",
                "-o",
                str(output),
            ],
            cwd=project_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=cfg.vitis_timeout_seconds,
        )
        if compile_result.returncode:
            raise RuntimeError(
                "pragma value compilation failed: "
                + " ".join(compile_result.stderr.strip().split())[-1500:]
            )
        values = {
            int(match.group("index")): match.group("value")
            for match in _PROBE_VALUE_RE.finditer(output.read_text())
        }
    missing = sorted(set(range(len(requests))) - set(values))
    if missing:
        raise RuntimeError(f"Vitis did not emit pragma value probes {missing}")
    return values


def resolve_numeric_arguments(
    nodes: list[dict],
    demangled_functions: list[str],
    *,
    numeric_arguments: set[str],
    project_dir: str | Path,
    cfg: PreprocessConfig,
) -> int:
    """Resolve every symbolic numeric pragma argument and update its JSON."""

    context_cache: dict[tuple[str, int, str], SourceFunction | None] = {}
    requests: list[ResolutionRequest] = []
    node_arguments: dict[int, dict[str, list[str]]] = {}
    for node in nodes:
        if int(node.get("type", -1)) != 3:
            continue
        features = node.get("features", {})
        raw_arguments = features.get("arguments_json", ["{}"])[0]
        arguments = json.loads(raw_arguments)
        node_arguments[int(node["id"])] = arguments
        function_id = int(node.get("function", -1))
        if not 0 <= function_id < len(demangled_functions):
            continue
        reported = str(features.get("reported_function", [""])[0])
        source_path = str(features.get("source_file", [""])[0])
        source_line = int(features.get("source_line", ["0"])[0])
        key = (source_path, source_line, reported)
        if key not in context_cache:
            context_cache[key] = source_function(*key)
        context = context_cache[key]
        if context is None:
            continue
        template_arguments = concrete_template_arguments(
            demangled_functions[function_id],
            reported,
        )
        if len(template_arguments) != len(context.parameter_names):
            continue
        for argument, values in arguments.items():
            if argument not in numeric_arguments:
                continue
            for value_index, value in enumerate(values):
                expression = str(value)
                if _is_number(expression):
                    continue
                requests.append(
                    ResolutionRequest(
                        node=node,
                        key=argument,
                        value_index=value_index,
                        expression=expression,
                        source=context,
                        template_arguments=template_arguments,
                    )
                )

    if not requests:
        return 0
    values = _compile_requests(requests, Path(project_dir).resolve(), cfg)
    provenance: dict[int, dict[str, list[dict[str, str]]]] = {}
    for index, request in enumerate(requests):
        node_id = int(request.node["id"])
        resolved = values[index]
        node_arguments[node_id][request.key][request.value_index] = resolved
        provenance.setdefault(node_id, {}).setdefault(request.key, []).append(
            {
                "expression": request.expression,
                "value": resolved,
                "template_arguments": ", ".join(request.template_arguments),
            }
        )

    for node in nodes:
        node_id = int(node["id"])
        if node_id not in provenance:
            continue
        node["features"]["arguments_json"] = [
            json.dumps(
                node_arguments[node_id],
                sort_keys=True,
                separators=(",", ":"),
            )
        ]
        node["features"]["numeric_resolution_json"] = [
            json.dumps(
                provenance[node_id],
                sort_keys=True,
                separators=(",", ":"),
            )
        ]

    unresolved: list[str] = []
    for node in nodes:
        if int(node.get("type", -1)) != 3:
            continue
        arguments = node_arguments.get(int(node["id"]), {})
        for key, values_for_key in arguments.items():
            if key not in numeric_arguments:
                continue
            for value in values_for_key:
                if not _is_number(str(value)):
                    unresolved.append(
                        f"node {node['id']} {node.get('text')} "
                        f"{key}={value!r}"
                    )
    if unresolved:
        raise RuntimeError(
            "Unresolved numeric pragma arguments: " + "; ".join(unresolved[:20])
        )
    return len(requests)
