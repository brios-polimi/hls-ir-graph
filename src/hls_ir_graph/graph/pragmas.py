"""Inject structured Vitis HLS pragma nodes into a ProGraML JSON graph.

Vitis exposes pragmas in two complementary compiler outputs:

* ``-Wdump-hls-pragmas`` reports every recognized source pragma with its
  directive, function, and options.
* the synthesis LLVM IR materializes some variable directives as
  ``llvm.sideeffect`` calls carrying an ``xlx_*`` bundle.

The compiler dump is authoritative for pragma semantics. LLVM carriers are
used only to find precise CDFG anchors. This module deliberately does not read
source files or DWARF metadata, which are not reliable after the Vitis LLVM 7
-> LLVM 16 compatibility conversion.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

try:
    import orjson
except ImportError:  # Keep the pipeline usable in minimal environments.
    orjson = None

# ProGraML graph contract. Graph production owns these values; hls-surrogate-lab
# mirrors them when tensorizing the resulting JSON.
NODE_INSTRUCTION = 0
NODE_VARIABLE = 1
NODE_CONSTANT = 2
NODE_PRAGMA = 3
FLOW_DATA = 1
FLOW_PRAGMA = 3
PRAGMA_SCHEMA_VERSION = 2
ATTACHMENT_SCHEMA_VERSION = 4
NUMERIC_PRAGMA_ARGUMENTS = {
    "ii",
    "factor",
    "dim",
    "depth",
    "min",
    "max",
    "avg",
    "latency",
    "interval",
    "num",
    "max_read_burst_length",
    "max_write_burst_length",
    "num_read_outstanding",
    "num_write_outstanding",
    "max_widen_bitwidth",
    "limit",
}

_DUMP_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): warning: "
    r"HLS pragma dump (?P<fields>.+?) \[-Wdump-hls-pragmas\]$"
)
_FIELD_RE = re.compile(r"(?P<key>Pragma\w+)=(?P<value>.*)")
_PRAGMA_TEXT_RE = re.compile(r"^\s*(#pragma\s+HLS\b.*)$", re.IGNORECASE)
_CARRIER_RE = re.compile(
    r'@llvm\.sideeffect\(\).*?\[\s*"xlx_(?P<directive>[A-Za-z0-9_]+)"'
    r"\((?P<arguments>.*?)\)\s*\]",
    re.DOTALL,
)
_LOCAL_VALUE_RE = re.compile(r'%(?!")([A-Za-z_$.-][\w$.-]*)')
_GLOBAL_VALUE_RE = re.compile(r'@(?!")([A-Za-z_$.-][\w$.-]*)')
_ARGUMENT_KEY_RE = re.compile(r"[^a-z0-9_]+")
_TARGET_KEYS = ("variable", "port")
_CARRIER_DIRECTIVE_ALIASES = {
    ("stream", "reqd_pipe_depth"),
}


@dataclass(frozen=True)
class VitisPragma:
    """A pragma record emitted by ``-Wdump-hls-pragmas``."""

    path: str
    line: int
    column: int
    directive: str
    function: str
    options: str
    arguments: dict[str, tuple[str, ...]]
    raw_fields: str
    raw_dump_line: str
    pragma_text: str
    targets: tuple[str, ...]

    @property
    def text(self) -> str:
        return f"pragma.{self.directive}"


def _normalise_directive(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_") or "unknown"


def _normalise_argument_key(value: str) -> str:
    return _ARGUMENT_KEY_RE.sub("_", value.lower()).strip("_") or "unknown"


def parse_pragma_options(options: str) -> dict[str, tuple[str, ...]]:
    """Parse Vitis' ``key=value`` options and bare flags without losing tokens."""

    try:
        tokens = shlex.split(options, posix=True)
    except ValueError:
        tokens = options.split()

    parsed: dict[str, list[str]] = defaultdict(list)
    positional = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if "=" in token and token != "=":
            key, value = token.split("=", 1)
            if not value and index + 1 < len(tokens):
                index += 1
                value = tokens[index]
            parsed[_normalise_argument_key(key)].append(value)
        elif index + 2 < len(tokens) and tokens[index + 1] == "=":
            parsed[_normalise_argument_key(token)].append(tokens[index + 2])
            index += 2
        elif token == "=":
            parsed["_unparsed"].append(token)
        else:
            # Bare alphabetic tokens are HLS flags (for example ``rewind``).
            # Other positional values receive stable keys and remain lossless.
            key = _normalise_argument_key(token)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", token):
                parsed[key].append("true")
            else:
                parsed[f"_positional_{positional}"].append(token)
                positional += 1
        index += 1
    return {key: tuple(values) for key, values in parsed.items()}


def _targets(arguments: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    names: list[str] = []
    for key in _TARGET_KEYS:
        for value in arguments.get(key, ()):
            names.extend(item.strip() for item in value.split(",") if item.strip())
    return tuple(dict.fromkeys(names))


def read_vitis_pragma_dump(path: str | Path) -> list[VitisPragma]:
    """Parse Vitis ``-Wdump-hls-pragmas`` diagnostics from ``path``.

    Unknown directives are retained. They become ``pragma.<directive>`` graph
    nodes and therefore remain available to later vocabulary/feature work.
    """

    records: list[VitisPragma] = []
    lines = Path(path).read_text(errors="replace").splitlines()
    for index, line in enumerate(lines):
        match = _DUMP_RE.match(line)
        if not match:
            continue
        fields: dict[str, str] = {}
        for piece in match.group("fields").split("_XLX_SEP_"):
            field = _FIELD_RE.fullmatch(piece.strip())
            if field:
                fields[field.group("key")] = field.group("value").strip()
        directive = _normalise_directive(fields.get("PragmaType", "unknown"))
        options = fields.get("PragmaOptions", "")
        arguments = parse_pragma_options(options)
        pragma_line = (
            _PRAGMA_TEXT_RE.match(lines[index + 1])
            if index + 1 < len(lines)
            else None
        )
        records.append(
            VitisPragma(
                path=match.group("path"),
                line=int(match.group("line")),
                column=int(match.group("column")),
                directive=directive,
                function=fields.get("PragmaFunction", ""),
                options=options,
                arguments=arguments,
                raw_fields=match.group("fields"),
                raw_dump_line=line,
                pragma_text=pragma_line.group(1) if pragma_line else "",
                targets=_targets(arguments),
            )
        )
    return records


def _full_text(node: dict) -> str:
    value = node.get("features", {}).get("full_text", [])
    return "\n".join(value) if isinstance(value, list) else str(value)


def _remove_previous_injection(graph: dict) -> None:
    graph.pop("block_enrichment", None)
    graph.pop("hierarchy_enrichment", None)
    injectors = {
        "ll_hls4ml.vitis_pragmas",
        "hls4ml_pipeline.vitis_pragmas",
        "hls4ml_pipeline.llvm_hierarchy",
        "hls_ir_graph.vitis_pragmas",
        "hls_ir_graph.llvm_hierarchy",
    }
    removed = {
        int(node["id"])
        for node in graph.get("nodes", [])
        if set(node.get("features", {}).get("injector", ())) & injectors
    }
    if not removed:
        return

    nodes = [node for node in graph["nodes"] if int(node["id"]) not in removed]
    id_map = {int(node["id"]): index for index, node in enumerate(nodes)}
    for index, node in enumerate(nodes):
        node["id"] = index
    graph["nodes"] = nodes
    graph["links"] = [
        {**link, "source": id_map[int(link["source"])], "target": id_map[int(link["target"])]}
        for link in graph.get("links", [])
        if int(link["source"]) not in removed and int(link["target"]) not in removed
    ]


def _strip_cpp_comments_and_literals(source: str) -> str:
    """Blank comments and literals while preserving braces and line numbers."""

    result = list(source)
    index = 0
    state = "code"
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == "/" and following == "/":
                result[index] = result[index + 1] = " "
                state = "line_comment"
                index += 2
                continue
            if char == "/" and following == "*":
                result[index] = result[index + 1] = " "
                state = "block_comment"
                index += 2
                continue
            if char == '"':
                result[index] = " "
                state = "string"
            elif char == "'":
                result[index] = " "
                state = "character"
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                result[index] = " "
        elif state == "block_comment":
            if char == "*" and following == "/":
                result[index] = result[index + 1] = " "
                state = "code"
                index += 2
                continue
            if char != "\n":
                result[index] = " "
        else:
            quote = '"' if state == "string" else "'"
            if char == "\\" and following:
                result[index] = " "
                if following != "\n":
                    result[index + 1] = " "
                index += 2
                continue
            if char == quote:
                state = "code"
            if char != "\n":
                result[index] = " "
        index += 1
    return "".join(result)


def _source_loop_labels(
    records: list[VitisPragma],
) -> dict[tuple[str, int], str]:
    """Map pragma locations to their innermost explicitly labelled loop."""

    requested: dict[str, set[int]] = defaultdict(set)
    for pragma in records:
        if pragma.path and pragma.line > 0 and not pragma.targets:
            requested[pragma.path].add(pragma.line)

    resolved: dict[tuple[str, int], str] = {}
    for path, lines in requested.items():
        source_path = Path(path)
        if not source_path.is_file():
            continue
        source = _strip_cpp_comments_and_literals(
            source_path.read_text(errors="replace")
        )
        line_at = [1] * (len(source) + 1)
        line = 1
        for index, char in enumerate(source):
            line_at[index] = line
            if char == "\n":
                line += 1
        line_at[len(source)] = line

        stack: list[tuple[int, str]] = []
        intervals: list[tuple[int, int, str]] = []
        for index, char in enumerate(source):
            if char == "{":
                prefix = source[max(0, index - 2000):index]
                loop = re.search(
                    r"\b(?:for|while)\s*\([^{}]*\)\s*$|\bdo\s*$",
                    prefix,
                    re.DOTALL,
                )
                label = ""
                if loop is not None:
                    before_loop = prefix[:loop.start()]
                    label_match = re.search(
                        r"([A-Za-z_]\w*)\s*:\s*$",
                        before_loop,
                    )
                    if label_match:
                        label = label_match.group(1)
                stack.append((line_at[index], label))
            elif char == "}" and stack:
                open_line, label = stack.pop()
                if label:
                    intervals.append((open_line, line_at[index], label))

        for pragma_line in lines:
            enclosing = [
                (start, label)
                for start, end, label in intervals
                if start <= pragma_line <= end
            ]
            if enclosing:
                resolved[(path, pragma_line)] = max(enclosing)[1]
    return resolved


@dataclass(frozen=True)
class PragmaCarrier:
    directive: str
    targets: tuple[str, ...]
    local_targets: tuple[str, ...]
    global_symbols: tuple[str, ...]
    owner_functions: tuple[str, ...]
    raw_arguments: str
    scalar_arguments: dict[str, tuple[str, ...]]
    node: dict
    anchors: tuple[dict, ...]


@dataclass(frozen=True)
class GlobalSymbol:
    name: str
    object_name: str
    owner_function: str


@dataclass
class ObjectIndex:
    local: dict[tuple[int, str], list[dict]]
    global_: dict[str, list[dict]]
    global_names: set[str]


def _object_index(nodes: list[dict]) -> ObjectIndex:
    """Index every exact local/global graph occurrence in one graph pass."""

    local: dict[tuple[int, str], list[dict]] = defaultdict(list)
    global_: dict[str, list[dict]] = defaultdict(list)
    global_names: set[str] = set()
    for node in nodes:
        text = _full_text(node)
        node_type = int(node.get("type", -1))
        global_values = set(_GLOBAL_VALUE_RE.findall(text))
        global_names.update(global_values)
        if node_type == NODE_CONSTANT:
            for name in global_values:
                global_[name].append(node)
        elif node_type == NODE_VARIABLE:
            function = int(node.get("function", -1))
            for name in set(_LOCAL_VALUE_RE.findall(text)):
                parts = name.split(".")
                for end in range(1, len(parts) + 1):
                    local[(function, ".".join(parts[:end]))].append(node)
    return ObjectIndex(dict(local), dict(global_), global_names)


def _split_carrier_arguments(arguments: str) -> list[str]:
    """Split LLVM bundle operands while respecting nested type delimiters."""

    operands: list[str] = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closing = set(pairs.values())
    for index, character in enumerate(arguments):
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character in pairs:
            depth += 1
        elif character in closing:
            depth = max(depth - 1, 0)
        elif character == "," and depth == 0:
            operands.append(arguments[start:index].strip())
            start = index + 1
    tail = arguments[start:].strip()
    if tail:
        operands.append(tail)
    return operands


def _carrier_scalar_arguments(arguments: str) -> dict[str, tuple[str, ...]]:
    parsed: dict[str, tuple[str, ...]] = {}
    for index, operand in enumerate(_split_carrier_arguments(arguments)):
        # SSA operands are represented by graph edges. Retain scalar constants
        # from the compiler carrier as generic positional semantic arguments.
        if _LOCAL_VALUE_RE.search(operand) or _GLOBAL_VALUE_RE.search(operand):
            continue
        match = re.search(
            r"(?:^|\s)(?:i\d+|half|float|double)\s+"
            r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|true|false)\s*$",
            operand,
        )
        if match:
            parsed[f"_carrier_arg_{index}"] = (match.group(1),)
    return parsed


def _signature_head(value: str) -> str:
    """Return a demangled name up to its top-level parameter list."""

    depth = 0
    for index, character in enumerate(value):
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(depth - 1, 0)
        elif character == "(" and depth == 0:
            return value[:index].strip()
    return value.strip()


def _drop_trailing_template_arguments(value: str) -> str:
    value = value.rstrip()
    if not value.endswith(">"):
        return value
    depth = 0
    for index in range(len(value) - 1, -1, -1):
        character = value[index]
        if character == ">":
            depth += 1
        elif character == "<":
            depth -= 1
            if depth == 0:
                return value[:index].rstrip()
    return value


def _function_basename(demangled: str) -> str:
    head = _drop_trailing_template_arguments(_signature_head(demangled))
    if not head:
        return ""
    return head.rsplit("::", 1)[-1].split()[-1]


def _demangle(names: list[str]) -> list[str]:
    """Demangle in one subprocess; remain conservative when unavailable."""

    if not names:
        return []
    binary = shutil.which("c++filt")
    if binary is None:
        return names
    result = subprocess.run(
        [binary],
        input="\n".join(names),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return names
    lines = result.stdout.splitlines()
    return lines if len(lines) == len(names) else names


def _function_demangled(graph: dict) -> list[str]:
    names = [
        str(item.get("name", "")).lstrip("@")
        for item in graph.get("graph", {}).get("function", [])
    ]
    return _demangle(names)


def _function_basenames(
    graph: dict,
    demangled: list[str] | None = None,
) -> list[str]:
    names = [
        str(item.get("name", "")).lstrip("@")
        for item in graph.get("graph", {}).get("function", [])
    ]
    demangled = demangled if demangled is not None else _demangle(names)
    return [
        (
            _function_basename(value)
            if value != raw or not raw.startswith("_Z")
            else ""
        )
        for raw, value in zip(names, demangled)
    ]


def _global_symbols(names: set[str]) -> dict[str, GlobalSymbol]:
    names = sorted(names)
    demangled = _demangle(names)
    result: dict[str, GlobalSymbol] = {}
    for name, value in zip(names, demangled):
        separator = value.rfind("::")
        if separator >= 0 and ")" in value[:separator]:
            owner = _function_basename(value[:separator])
            object_name = value[separator + 2 :]
        else:
            owner = ""
            object_name = value
        result[name] = GlobalSymbol(
            name=name,
            object_name=object_name,
            owner_function=owner,
        )
    return result


def _carrier(
    node: dict,
    objects: ObjectIndex,
    symbols: dict[str, GlobalSymbol],
) -> PragmaCarrier | None:
    if int(node.get("type", -1)) != NODE_INSTRUCTION:
        return None
    match = _CARRIER_RE.search(_full_text(node))
    if not match:
        return None
    arguments = match.group("arguments")
    local_targets = tuple(dict.fromkeys(_LOCAL_VALUE_RE.findall(arguments)))
    global_names = tuple(dict.fromkeys(_GLOBAL_VALUE_RE.findall(arguments)))
    global_targets = tuple(
        dict.fromkeys(
            symbols[name].object_name for name in global_names if name in symbols
        )
    )
    owner_functions = tuple(
        dict.fromkeys(
            symbols[name].owner_function
            for name in global_names
            if name in symbols and symbols[name].owner_function
        )
    )
    function = int(node.get("function", -1))
    # The carrier is only a compiler-inserted transport for the operand bundle.
    # Attach recovered pragmas to the actual objects, then prune the carrier.
    anchors: list[dict] = []
    for target in local_targets:
        anchors.extend(objects.local.get((function, target), ()))
    for name in global_names:
        anchors.extend(objects.global_.get(name, ()))
    return PragmaCarrier(
        directive=_normalise_directive(match.group("directive")),
        targets=tuple(dict.fromkeys((*local_targets, *global_targets))),
        local_targets=local_targets,
        global_symbols=global_names,
        owner_functions=owner_functions,
        raw_arguments=arguments,
        scalar_arguments=_carrier_scalar_arguments(arguments),
        node=node,
        anchors=tuple(
            {int(candidate["id"]): candidate for candidate in anchors}.values()
        ),
    )


def _function_ids(
    graph: dict,
    function: str,
    basenames: list[str] | None = None,
    *,
    pragma: VitisPragma | None = None,
    demangled: list[str] | None = None,
) -> list[int]:
    if not function:
        return []
    basenames = basenames if basenames is not None else _function_basenames(graph)
    matches = [
        index
        for index, basename in enumerate(basenames)
        if basename == function
    ]
    if pragma is None or demangled is None:
        return matches
    from .pragma_values import source_function, source_matches_instantiation

    source = source_function(pragma.path, pragma.line, pragma.function)
    return [
        index
        for index in matches
        if source_matches_instantiation(source, demangled[index], function)
    ]


def _entry(nodes: list[dict], function: int) -> dict | None:
    candidates = [
        node
        for node in nodes
        if int(node.get("type", -1)) == NODE_INSTRUCTION
        and int(node.get("function", -1)) == function
        and node.get("text") != "[external]"
    ]
    return min(candidates, key=lambda node: int(node["id"]), default=None)


def _variable_anchor_groups(
    objects: ObjectIndex,
    symbols: dict[str, GlobalSymbol],
    pragma: VitisPragma,
    function_ids: list[int],
) -> list[list[dict]]:
    """Resolve every target in each exact concrete function instantiation."""

    groups: list[list[dict]] = []
    for function in function_ids:
        anchors: list[dict] = []
        complete = True
        for target in pragma.targets:
            target_nodes = objects.local.get((function, target), [])
            if not target_nodes:
                matching_symbols = {
                    name
                    for name, symbol in symbols.items()
                    if symbol.object_name == target
                    and (
                        not pragma.function
                        or symbol.owner_function == pragma.function
                    )
                }
                target_nodes = [
                    node
                    for name in matching_symbols
                    for node in objects.global_.get(name, ())
                ]
            if not target_nodes:
                complete = False
                break
            anchors.extend(target_nodes)
        if complete and anchors:
            groups.append(
                list(
                    {
                        int(candidate["id"]): candidate
                        for candidate in anchors
                    }.values()
                )
            )
    return groups


def _add_pragma_node(
    nodes: list[dict],
    links: list[dict],
    pragma: VitisPragma,
    anchors: list[dict],
    anchor_reason: str,
    attachment_confidence: str,
    carrier: PragmaCarrier | None = None,
) -> bool:
    node_id = len(nodes)
    first = anchors[0] if anchors else {}
    arguments = dict(pragma.arguments)
    if carrier is not None:
        for key, values in carrier.scalar_arguments.items():
            arguments.setdefault(key, values)
    arguments_json = json.dumps(
        {key: list(values) for key, values in sorted(arguments.items())},
        sort_keys=True,
        separators=(",", ":"),
    )
    nodes.append(
        {
            "block": int(first.get("block", 0)),
            "features": {
                "schema_version": [str(PRAGMA_SCHEMA_VERSION)],
                "attachment_schema_version": [
                    str(ATTACHMENT_SCHEMA_VERSION)
                ],
                "directive": [pragma.directive],
                "full_text": [pragma.raw_dump_line],
                "injector": ["hls_ir_graph.vitis_pragmas"],
                "origin": ["vitis_pragma_dump" if pragma.path else "llvm.sideeffect"],
                "pragma_text": [pragma.pragma_text],
                "raw_options": [pragma.options],
                "carrier_arguments": [carrier.raw_arguments if carrier else ""],
                "arguments_json": [arguments_json],
                "source_file": [pragma.path],
                "source_line": [str(pragma.line)],
                "reported_function": [pragma.function],
                "target_names": [",".join(pragma.targets)],
                "anchor_reason": [anchor_reason],
                "attachment_confidence": [attachment_confidence],
                "source_loop_label": [
                    str(first.get("features", {}).get("name", [""])[0])
                    if anchor_reason == "source_loop_label"
                    else ""
                ],
                "resolved_global_symbols": [
                    ",".join(carrier.global_symbols) if carrier else ""
                ],
            },
            "function": int(first.get("function", 0)),
            "id": node_id,
            "text": pragma.text,
            "type": NODE_PRAGMA,
        }
    )
    for anchor in {int(node["id"]): node for node in anchors}.values():
        links.append(
            {
                "flow": FLOW_PRAGMA,
                "key": 0,
                "position": 0,
                "source": node_id,
                "target": int(anchor["id"]),
            }
        )
    return True


def _pragma_record_json(pragma: VitisPragma) -> dict:
    return {
        "path": pragma.path,
        "line": pragma.line,
        "column": pragma.column,
        "directive": pragma.directive,
        "function": pragma.function,
        "raw_options": pragma.options,
        "arguments": {
            key: list(values) for key, values in sorted(pragma.arguments.items())
        },
        "targets": list(pragma.targets),
    }


def _directives_compatible(
    pragma: VitisPragma,
    carrier: PragmaCarrier,
) -> bool:
    return (
        pragma.directive == carrier.directive
        or (pragma.directive, carrier.directive) in _CARRIER_DIRECTIVE_ALIASES
    )


def _carrier_matches(
    pragma: VitisPragma,
    carrier: PragmaCarrier,
    function_ids: set[int],
) -> bool:
    if not _directives_compatible(pragma, carrier):
        return False
    if not pragma.targets or not set(pragma.targets).issubset(carrier.targets):
        return False

    if pragma.directive == "stream":
        source_depth = set(pragma.arguments.get("depth", ()))
        carrier_depth = set(carrier.scalar_arguments.get("_carrier_arg_1", ()))
        if source_depth and carrier_depth and source_depth.isdisjoint(carrier_depth):
            return False

    carrier_function = int(carrier.node.get("function", -1))
    return (
        carrier_function in function_ids
        or pragma.function in carrier.owner_functions
    )


def inject_vitis_pragmas(
    graph_path: str | Path,
    pragma_dump_path: str | Path,
    *,
    llvm_path: str | Path | None = None,
    project_dir: str | Path | None = None,
    cfg=None,
    label: dict | None = None,
) -> dict[str, int]:
    """Inject compiler-reported Vitis pragma nodes into ``graph_path`` in place.

    Carrier calls recover pragma semantics and attach the pragma node to every
    exact carrier object; the compiler-only carrier instruction is then
    removed. Variable pragmas without carriers attach only when their objects
    resolve exactly. Function pragmas attach to the exact function hierarchy node.
    Labelled loop pragmas attach to their same-named LLVM basic-block node when
    ``llvm_path`` is available.
    """

    graph_path = Path(graph_path)
    records = read_vitis_pragma_dump(pragma_dump_path)
    graph = _load_graph_json(graph_path)
    _remove_previous_injection(graph)
    nodes = graph.setdefault("nodes", [])
    links = graph.setdefault("links", [])
    original_nodes = list(nodes)

    demangled = _function_demangled(graph)
    basenames = _function_basenames(graph, demangled)
    objects = _object_index(original_nodes)
    symbols = _global_symbols(objects.global_names)
    source_loop_labels = _source_loop_labels(records)
    block_lookup: dict[tuple[int, str], dict] = {}
    function_lookup: dict[int, dict] = {}
    hierarchy_stats = {
        "schema_version": 2,
        "function_nodes_injected": 0,
        "block_nodes_injected": 0,
        "functions_mapped": 0,
        "instruction_membership_edges": 0,
        "function_membership_edges": 0,
        "cfg_edges": 0,
        "cfg_validation_failures": [],
        "mapping_failures": [],
    }
    if llvm_path is not None:
        from .hierarchy import add_llvm_hierarchy

        block_lookup, function_lookup, hierarchy_stats = add_llvm_hierarchy(
            graph,
            nodes,
            links,
            llvm_path,
            set(source_loop_labels.values()),
        )
    carriers: list[PragmaCarrier] = []
    for node in original_nodes:
        if int(node.get("type", -1)) != NODE_INSTRUCTION:
            continue
        carrier = _carrier(node, objects, symbols)
        if carrier is not None:
            carriers.append(carrier)

    injected = 0
    dump_injected = 0
    matched_carriers: set[int] = set()
    unmatched = 0
    unmatched_records: list[dict] = []
    loop_scope_nodes = 0
    unresolved_loop_scopes: list[dict] = []
    for pragma in records:
        function_ids = set(
            _function_ids(
                graph,
                pragma.function,
                basenames,
                pragma=pragma,
                demangled=demangled,
            )
        )
        candidates = [
            (index, carrier)
            for index, carrier in enumerate(carriers)
            if index not in matched_carriers
            and _carrier_matches(pragma, carrier, function_ids)
        ]
        record_injected = False
        if candidates:
            for carrier_index, carrier in candidates:
                matched_carriers.add(carrier_index)
                _add_pragma_node(
                    nodes,
                    links,
                    pragma,
                    list(carrier.anchors),
                    "carrier_exact",
                    "exact",
                    carrier,
                )
                injected += 1
                record_injected = True
        elif pragma.targets:
            for anchors in _variable_anchor_groups(
                objects,
                symbols,
                pragma,
                sorted(function_ids),
            ):
                _add_pragma_node(
                    nodes,
                    links,
                    pragma,
                    anchors,
                    "variable_identity",
                    "exact",
                )
                injected += 1
                record_injected = True
        else:
            loop_label = source_loop_labels.get((pragma.path, pragma.line), "")
            if loop_label and llvm_path is not None:
                missing_functions: list[int] = []
                for function in sorted(function_ids):
                    block = block_lookup.get((function, loop_label))
                    if block is None:
                        missing_functions.append(function)
                        continue
                    _add_pragma_node(
                        nodes,
                        links,
                        pragma,
                        [block],
                        "source_loop_label",
                        "exact",
                    )
                    injected += 1
                    loop_scope_nodes += 1
                    record_injected = True
                if missing_functions:
                    unresolved_loop_scopes.append(
                        {
                            **_pragma_record_json(pragma),
                            "source_loop_label": loop_label,
                            "function_ids": missing_functions,
                        }
                    )
            else:
                for function in sorted(function_ids):
                    function_node = function_lookup.get(function)
                    reason = "function_scope"
                    if function_node is None:
                        function_node = _entry(original_nodes, function)
                        reason = "function_scope_entry_fallback"
                    if function_node is None:
                        continue
                    _add_pragma_node(
                        nodes,
                        links,
                        pragma,
                        [function_node],
                        reason,
                        "coarse_scope",
                    )
                    injected += 1
                    record_injected = True

        if not record_injected:
            unmatched += 1
            unmatched_records.append(_pragma_record_json(pragma))
            continue
        dump_injected += 1

    carrier_injected = 0
    for index, carrier in enumerate(carriers):
        if index in matched_carriers:
            continue
        pragma = VitisPragma(
            path="",
            line=0,
            column=0,
            directive=carrier.directive,
            function="",
            options="",
            arguments=carrier.scalar_arguments,
            raw_fields=(
                f"PragmaType={carrier.directive}_XLX_SEP_ "
                "PragmaOptions=ir-carrier"
            ),
            raw_dump_line="",
            pragma_text="",
            targets=carrier.targets,
        )
        _add_pragma_node(
            nodes,
            links,
            pragma,
            list(carrier.anchors),
            "carrier_only",
            "carrier_only",
            carrier,
        )
        injected += 1
        carrier_injected += 1

    numeric_arguments_resolved = 0
    if project_dir is not None and cfg is not None:
        from .pragma_values import resolve_numeric_arguments

        numeric_arguments_resolved = resolve_numeric_arguments(
            nodes,
            demangled,
            numeric_arguments=NUMERIC_PRAGMA_ARGUMENTS,
            project_dir=project_dir,
            cfg=cfg,
        )

    from .intrinsics import prune_nonsemantic_intrinsics

    intrinsic_pruning = prune_nonsemantic_intrinsics(graph)
    hierarchy_stats["instruction_membership_edges"] -= intrinsic_pruning[
        "block_membership_edges_removed"
    ]
    hierarchy_stats["function_membership_edges"] -= intrinsic_pruning[
        "function_membership_edges_removed"
    ]
    hierarchy_stats["function_nodes_injected"] -= intrinsic_pruning[
        "function_nodes_removed"
    ]
    hierarchy_stats["block_nodes_injected"] -= intrinsic_pruning[
        "block_nodes_removed"
    ]
    hierarchy_stats["functions_mapped"] -= intrinsic_pruning[
        "function_nodes_removed"
    ]
    hierarchy_stats["cfg_edges"] -= intrinsic_pruning[
        "block_cfg_edges_removed"
    ]

    graph["pragma_injection"] = {
        "schema_version": PRAGMA_SCHEMA_VERSION,
        "attachment_schema_version": ATTACHMENT_SCHEMA_VERSION,
        "pragma_dump_records": len(records),
        "numeric_arguments_resolved": numeric_arguments_resolved,
        "loop_scope_nodes": loop_scope_nodes,
        "unresolved_loop_scopes": unresolved_loop_scopes,
        "unmatched_records": unmatched_records,
    }
    graph["hierarchy_enrichment"] = hierarchy_stats
    graph["intrinsic_pruning"] = intrinsic_pruning
    from .relations import canonicalize_relations

    relation_stats = canonicalize_relations(graph)
    if label is not None:
        graph["labels"] = label
    _dump_graph_json(graph_path, graph)
    return {
        "pragma_dump_records": len(records),
        "carrier_pragmas_injected": carrier_injected,
        "carrier_pragmas_matched": len(matched_carriers),
        "dump_pragmas_injected": dump_injected,
        "pragma_nodes_injected": injected,
        "pragmas_unmatched": unmatched,
        "numeric_arguments_resolved": numeric_arguments_resolved,
        "function_nodes_injected": hierarchy_stats["function_nodes_injected"],
        "block_nodes_injected": hierarchy_stats["block_nodes_injected"],
        "intrinsic_nodes_pruned": intrinsic_pruning["nodes_removed"],
        "canonical_relations": sum(relation_stats["relations"].values()),
        "loop_scope_nodes": loop_scope_nodes,
    }


def _load_graph_json(path: Path) -> dict:
    if orjson is not None:
        return orjson.loads(path.read_bytes())
    with path.open() as handle:
        return json.load(handle)


def _dump_graph_json(path: Path, graph: dict) -> None:
    if orjson is not None:
        path.write_bytes(orjson.dumps(graph))
        return
    with path.open("w") as handle:
        json.dump(graph, handle, separators=(",", ":"))
