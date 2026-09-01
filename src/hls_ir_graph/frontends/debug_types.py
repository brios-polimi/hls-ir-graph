"""Recover LLVM identified-type names from compiler-emitted debug associations.

This reads the textual LLVM 16 debug metadata, not an ELF/DWARF binary. It does
not match types by layout, declaration order, or Clang's numeric name suffixes.
Only direct dbg.declare/dbg.value and global-variable associations are trusted.
Raw metadata and a coverage table are retained by the frontend for inspection.
"""

from __future__ import annotations

from collections import defaultdict
import re


NAMED_TYPE = r'%(?:"(?:\\[0-9A-Fa-f]{2}|[^"\\])*"|[-$._A-Za-z0-9]+)'
_TYPE_TOKEN = re.compile(NAMED_TYPE)
_DEFINITION = re.compile(rf'^({NAMED_TYPE})\s*=\s*type\b', re.MULTILINE)
_METADATA = re.compile(r'^!(\d+) = (?:distinct )?!(\w+)?([({].*[)}])$', re.MULTILINE)
_AC = re.compile(r'(?<![A-Za-z0-9_])(?:ac_[A-Za-z0-9_]+|ac_private::[A-Za-z0-9_]+)')


def split_fields(text: str) -> list[str]:
    """Split LLVM lists while respecting quoted strings and nested types."""
    fields, start, depth, quoted, escaped = [], 0, 0, False, False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '([{<':
            depth += 1
        elif char in ')]}>':
            depth -= 1
        elif char == ',' and depth == 0:
            fields.append(text[start:index].strip())
            start = index + 1
    fields.append(text[start:].strip())
    return fields


def _decode(text: str) -> str:
    raw = text.strip('"').encode('utf-8')
    return re.sub(rb'\\([0-9a-fA-F]{2})', lambda m: bytes([int(m[1], 16)]), raw).decode('utf-8')


def _encode(text: str) -> str:
    return '"' + ''.join(f'\\{b:02X}' if b < 32 or b >= 127 or b in (34, 92)
                         else chr(b) for b in text.encode('utf-8')) + '"'


def _stem(text: str) -> str:
    text = _decode(text.removeprefix('%'))
    text = re.sub(r'^(?:class|struct|union)\.', '', text)
    # Only used to reject incompatible anchors, never to select a specialization.
    return re.sub(r'\.\d+$', '', text).split('<', 1)[0]


def recover_debug_types(ir: str) -> tuple[str, dict]:
    """Return renamed IR and a JSON-serializable, module-wide evidence table.

    A name is rewritten only when all direct associations agree. Conflicting or
    missing AC mappings are reported; the caller decides whether to reject the
    file. An unrepresented source type is not evidence of an unmapped LLVM type.
    """
    nodes = {}
    for match in _METADATA.finditer(ir):
        fields = {}
        if match[2]:
            for field in split_fields(match[3][1:-1]):
                key, sep, value = field.partition(': ')
                if sep:
                    fields[key] = value
        else:
            fields['items'] = split_fields(match[3][1:-1])
        nodes['!' + match[1]] = {'kind': match[2] or 'tuple', **fields}

    def base(ref):
        seen = set()
        while ref in nodes and ref not in seen:
            seen.add(ref)
            node = nodes[ref]
            if node['kind'] == 'DIDerivedType' or node.get('tag') == 'DW_TAG_array_type':
                ref = node.get('baseType')
            else:
                return ref
        return None

    def qualified(ref, seen=None):
        seen = set() if seen is None else seen
        if ref not in nodes or ref in seen:
            return ''
        seen.add(ref)
        node = nodes[ref]
        name = _decode(node.get('name', ''))
        scope = nodes.get(node.get('scope'), {})
        if scope.get('kind') in ('DINamespace', 'DICompositeType'):
            prefix = qualified(node['scope'], seen)
            if prefix:
                return prefix + '::' + name
        return name

    defined = set(_DEFINITION.findall(ir))
    candidates = defaultdict(lambda: defaultdict(set))
    refs = defaultdict(set)
    skipped = defaultdict(int)

    def transparent_storage(ref, target):
        """Follow a proven single storage member/base at offset zero.

        Clang can emit a constant ac_int object as an anonymous aggregate of
        its storage instead of its C++ wrapper. Its anchored debug type still
        describes the complete inheritance chain. Only size-preserving, unique
        paths are accepted; this is not a search for a similar memory layout.
        """
        seen = set()
        while ref in nodes and ref not in seen:
            seen.add(ref)
            if _stem(qualified(ref)) == target:
                return ref
            node = nodes[ref]
            members = [nodes.get(r, {}) for r in nodes.get(node.get('elements'), {}).get('items', [])]
            members = [m for m in members if m.get('tag') in ('DW_TAG_member', 'DW_TAG_inheritance')
                       and 'DIFlagStaticMember' not in m.get('flags', '')]
            if len(members) != 1:
                return None
            member = members[0]
            child = base(member.get('baseType'))
            if (member.get('offset', '0') != '0' or 'DIFlagVirtual' in member.get('flags', '')
                    or not node.get('size') or node['size'] != nodes.get(child, {}).get('size')):
                return None
            ref = child
        return None

    def associate(operand, variable_ref, evidence):
        variable = nodes.get(variable_ref, {})
        ref = base(variable.get('type'))
        node = nodes.get(ref, {})
        if node.get('kind') != 'DICompositeType' or not node.get('name'):
            return
        # An operand begins with its LLVM type, possibly behind array brackets.
        # Never search past the type into SSA values or constant expressions.
        operand = operand.removeprefix('metadata ').lstrip()
        while operand.startswith('['):
            array = re.match(r'\[\d+ x\s+', operand)
            if not array:
                return
            operand = operand[array.end():]
        anonymous = False
        if operand.startswith('{'):
            # The supported constant-wrapper case has exactly one field, a
            # named record. Other anonymous/packed layouts remain unmapped.
            single = re.match(rf'\{{\s*({NAMED_TYPE})\s*\}}', operand)
            if not single:
                return
            operand = single[1]
            anonymous = True
        match = _TYPE_TOKEN.match(operand)
        if not match or match[0] not in defined:
            return
        llvm_name = match[0]
        if anonymous:
            ref = transparent_storage(ref, _stem(llvm_name))
            if ref is None:
                skipped['nontransparent_constant_wrapper'] += 1
                return
            evidence += ':single_storage_path'
        semantic = qualified(ref)
        if _stem(llvm_name) != _stem(semantic):
            skipped['incompatible_anchor'] += 1
            return
        candidates[llvm_name][semantic].add(evidence)
        refs[(llvm_name, semantic)].add(ref)

    for line in ir.splitlines():
        debug = re.search(r'@llvm\.dbg\.(?:declare|value)\((.*)\)', line)
        if debug:
            args = split_fields(debug[1])
            if len(args) < 3:
                continue
            # Fragments/conversions can describe only part of a source object.
            if args[2] != 'metadata !DIExpression()':
                skipped['nonempty_expression'] += 1
                continue
            variable_ref = args[1].removeprefix('metadata ')
            associate(args[0], variable_ref, 'debug_variable:' + variable_ref)
        elif line.startswith('@'):
            attached = re.search(r'!dbg (!(?:\d+))', line)
            declaration = re.match(r'^@(?:"(?:\\.|[^"\\])*"|[-$._A-Za-z0-9]+)\s*=\s*(.*)', line)
            storage = re.search(r'\b(?:global|constant)\s+(.*)', declaration[1]) if declaration else None
            if attached and storage:
                expression = nodes.get(attached[1], {})
                if expression.get('kind') == 'DIGlobalVariableExpression':
                    if expression.get('expr') != '!DIExpression()':
                        skipped['nonempty_global_expression'] += 1
                        continue
                    associate(storage[1], expression.get('var'), 'global:' + line.split(' =', 1)[0])

    records, replacements, conflicts = [], {}, []
    # Different LLVM types may share a debug spelling (e.g. ABI base subobjects).
    # Keep LLVM identities distinct; a suffix after the template remains regex-compatible.
    used = set(defined)
    for llvm_name in sorted(defined):
        choices = candidates[llvm_name]
        record = {'llvm_name': llvm_name, 'status': 'unmapped'}
        if len(choices) == 1:
            semantic = next(iter(choices))
            new_name = '%' + _encode(semantic)
            suffix = 0
            while new_name in used and new_name != llvm_name:
                suffix += 1
                new_name = '%' + _encode(semantic + f'.debug.{suffix}')
            used.add(new_name)
            replacements[llvm_name] = new_name
            record.update(status='mapped', semantic_name=semantic, emitted_name=new_name,
                          debug_type_ids=sorted(refs[(llvm_name, semantic)]),
                          evidence=sorted(choices[semantic]))
        elif choices:
            record.update(status='conflict', candidates=sorted(choices))
            conflicts.append(llvm_name)
        records.append(record)

    ac_records = [r for r in records if _AC.search(r['llvm_name'])]
    missing = [r['llvm_name'] for r in ac_records if r['status'] != 'mapped']
    # Tokenize the entire file so %class.ac_fixed cannot match the prefix of
    # %class.ac_fixed.10, and neither strings nor comments can be rewritten.
    tokens = re.compile(rf';[^\n]*|{NAMED_TYPE}|"(?:\\.|[^"\\])*"')
    renamed = tokens.sub(lambda m: replacements.get(m[0], m[0]), ir)
    return renamed, {
        'schema_version': 1,
        'source': 'llvm_debug_metadata',
        'scope': 'identified LLVM record types; scalarized values are not relabeled',
        'complete_ac_mapping': not missing,
        'ac_type_count': len(ac_records),
        'mapped_ac_type_count': len(ac_records) - len(missing),
        'unmapped_ac_types': missing,
        'conflicts': conflicts,
        'skipped_anchors': dict(skipped),
        'llvm_types': records,
        'debug_types': [dict(id=ref, qualified_name=qualified(ref), **node)
                        for ref, node in nodes.items() if node['kind'] == 'DICompositeType' and node.get('name')],
    }
