import json
import re
from pathlib import Path
from typing import Iterator

_TERM_REF = re.compile(r'@([A-Z][A-Za-z]+)')
_TERMS_FOLDER = re.compile(r'^\s*terms_folder:\s*(\S+)', re.MULTILINE)
_USES_BLOCK = re.compile(r'^uses:\n((?:[ \t]+-[ \t]+\S+\n?)+)', re.MULTILINE)
_LIST_ITEM = re.compile(r'-\s+(\S+)')
_REPOSITORY = re.compile(r'^repository:\s*(\S+)', re.MULTILINE)
_DESCRIPTION = re.compile(r'^description:\s*(.+)$', re.MULTILINE)
_EXTENDS = re.compile(r'''^extends:\s*["']?@?([A-Za-z_]\w*)''', re.MULTILINE)
_RECIPES_BLOCK = re.compile(r'^recipes:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_REFERENCES_BLOCK = re.compile(r'^references:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_GUIDELINES_BLOCK = re.compile(r'^guidelines:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_AI_INSTRUCTIONS_BLOCK = re.compile(r'^ai_instructions:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_PROPERTIES_BLOCK = re.compile(r'^properties:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_PATH_REF = re.compile(r'@([A-Z][A-Za-z]+)((?:#[A-Za-z_][\w-]*)+)')
_TOP_FIELD = re.compile(r'^([a-z_][\w-]*):', re.MULTILINE)

# Fields every term may carry regardless of what it extends: its identity, its content, and the
# blocks through which it declares members for its subtypes. Everything else at the top level has
# to be declared as a `properties:` entry somewhere in the extends chain, or it is an unknown-field.
_STRUCTURAL_FIELDS = frozenset({
    'description', 'extends', 'name',
    'properties', 'recipes', 'guidelines', 'ai_instructions',
})

_SETTINGS_PATH = Path.home() / '.duckspec' / 'settings.json'


def _parse_list_block(content: str, pattern: re.Pattern) -> list[str]:
    m = pattern.search(content)
    if not m:
        return []
    return [
        line.strip()[2:].strip()
        for line in m.group(1).splitlines()
        if line.strip().startswith('- ')
    ]


def _parse_extends(content: str) -> str | None:
    m = _EXTENDS.search(content)
    return m.group(1).strip() if m else None


def _own_rules(content: str, term_name: str) -> list[dict]:
    """Guidelines and ai_instructions declared directly on one term's content (no inheritance)."""
    return (
        [{'term': term_name, 'type': 'Guideline', 'text': g}
         for g in _parse_list_block(content, _GUIDELINES_BLOCK)] +
        [{'term': term_name, 'type': 'AI Instruction', 'text': a}
         for a in _parse_list_block(content, _AI_INSTRUCTIONS_BLOCK)]
    )


def _parse_dict_list_block(content: str, pattern: re.Pattern, key_field: str,
                           key_pattern: str | None = None) -> list[dict]:
    """Parses a `key:\\n  - <key_field>: ...\\n    description: ...` block into a list of dicts.
    Shared by _parse_recipes (key_field='name') and _parse_references (key_field='repository')."""
    m = pattern.search(content)
    if not m:
        return []
    key_pattern = key_pattern or key_field
    block = m.group(0)
    results = []
    for item in re.split(r'\n(?=  - )', block)[1:]:
        key_m = re.search(rf'^\s*-\s+{key_pattern}:\s*(.+)$', item, re.MULTILINE)
        desc_m = re.search(r'^[ \t]+description:\s*(.+)$', item, re.MULTILINE)
        if key_m:
            results.append({
                key_field: key_m.group(1).strip(),
                'description': desc_m.group(1).strip() if desc_m else '',
            })
    return results


def _parse_recipes(content: str) -> list[dict]:
    # entries carry `id:` after the rename, `name:` before it; callers keep seeing 'name'
    return _parse_dict_list_block(content, _RECIPES_BLOCK, 'name', key_pattern='(?:id|name)')


def _parse_references(content: str) -> list[dict]:
    """Other projects this term is connected to (installs, targets, is installed by) by identity
    only — repository URL + a short description. Never traversed for reachability, unlike `uses:`;
    surfaced as-is in load_project so the connection stays visible without forcing a full load."""
    return _parse_dict_list_block(content, _REFERENCES_BLOCK, 'repository')


def _find_named_blocks(content: str, name: str) -> list[str]:
    """Every entry with this id, at any depth. `#`-paths are allowed to skip levels, so the search
    cannot be restricted to immediate children; counting the matches is what tells an unambiguous
    path from one that silently resolved to whichever entry came first."""
    lines = content.splitlines()
    target = re.compile(r'^-\s+(?:id|name):\s*[\'"]?' + re.escape(name) + r'[\'"]?\s*$')
    blocks = []
    for i, line in enumerate(lines):
        if not target.match(line.strip()):
            continue
        item_indent = len(line) - len(line.lstrip())
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if not lines[j].strip():
                continue
            if len(lines[j]) - len(lines[j].lstrip()) <= item_indent:
                end = j
                break
        blocks.append('\n'.join(lines[i:end]).rstrip())
    return blocks


def _extract_named_block(content: str, name: str) -> str | None:
    """The first entry with this id, or None. Thin wrapper over _find_named_blocks."""
    blocks = _find_named_blocks(content, name)
    return blocks[0] if blocks else None


def _extract_key_block(content: str, name: str) -> str | None:
    """The counterpart to _extract_named_block for plain mapping keys — `functions:`, `properties:`,
    `guidelines:` — which are not list items and so have no `- name:` to match. Captures the key
    line plus every following line indented deeper than it. A scalar key yields just its own line."""
    lines = content.splitlines()
    target = re.compile(r'^(\s*)' + re.escape(name) + r':(\s|$)')
    for i, line in enumerate(lines):
        m = target.match(line)
        if m is None:
            continue
        key_indent = len(m.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if not lines[j].strip():
                continue
            if len(lines[j]) - len(lines[j].lstrip()) <= key_indent:
                end = j
                break
        return '\n'.join(lines[i:end]).rstrip()
    return None


def _nth_item(content: str, n: int) -> str | None:
    """The list item at position n among the items at the first item column below the first line
    of `content` — what a digits-only segment addresses, for entries that have no id."""
    lines = content.splitlines()
    column = None
    starts: list[int] = []
    for i, line in enumerate(lines[1:], start=1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if column is None:
            if not line.lstrip().startswith('- '):
                return None
            column = indent
        if indent < column:
            break
        if indent == column and line.lstrip().startswith('- '):
            starts.append(i)
    if n >= len(starts):
        return None
    start = starts[n]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= column:
            end = j
            break
    return '\n'.join(lines[start:end]).rstrip()


def _narrow(content: str, segment: str, prefer_key: bool = False) -> str | None:
    """Resolves one `#`-path segment. Named list items win over mapping keys of the same name —
    that ordering is what keeps a component called `settings` addressable in a term that also
    carries a `settings:` key, and preserves the behaviour from before keys were addressable.
    `prefer_key` reverses it for the operations that need a list: @Term's own `guidelines:` sits
    beside the `guidelines` entry of its `properties:`. A digits-only segment picks by position."""
    if segment.isdigit():
        return _nth_item(content, int(segment))
    if prefer_key:
        return _extract_key_block(content, segment) or _extract_named_block(content, segment)
    return _extract_named_block(content, segment) or _extract_key_block(content, segment)


def _line_of(content: str, pos: int) -> int:
    return content.count('\n', 0, pos) + 1



def _member_names(content: str, pattern: re.Pattern) -> list[str]:
    """Names declared directly in one block of a term, ignoring inheritance."""
    m = pattern.search(content)
    if not m:
        return []
    names = []
    for item in re.split(r'\n(?=  - )', m.group(0))[1:]:
        name_m = re.search(r'^\s*-\s+(?:id|name):\s*(.+)$', item, re.MULTILINE)
        if name_m:
            names.append(name_m.group(1).strip())
    return names


def _slot_element_types(content: str) -> dict[str, str]:
    """Maps slot name -> element @Term name for every slot this term declares with an explicit
    `type:`. Only slots that hold named entries carry one — a scalar field whose value happens to
    reference a term (`readme`, `graphic`, `when`) does not, and that distinction is exactly what
    an explicit declaration buys: it cannot be recovered from the slot's prose, which is how the
    earlier inference read `readme` as holding @Project entries."""
    m = _PROPERTIES_BLOCK.search(content)
    if not m:
        return {}
    out: dict[str, str] = {}
    for slot, item, indent in _slot_items(m.group(0), 'properties'):
        # `type:` is looked for anywhere among the entry's own fields, not only on the line after
        # `- id:`: requiring that order let a slot written description-first read as untyped, and
        # an untyped slot is one unknown-field silently skips.
        declared = re.search(rf'^{" " * (indent + 2)}type:\s*["\']?@(\w+)["\']?\s*$', item, re.MULTILINE)
        if declared:
            out[slot] = declared.group(1)
    return out


def _item_name(item_lines: list[str]) -> str:
    m = re.search(r'^\s*-\s+(?:id|name):\s*(.+)$', item_lines[0])
    return m.group(1).strip().strip('\'"') if m else ''


def _has_field(item: str, field: str, item_indent: int) -> bool:
    """True if `field` is set on the item itself rather than on something nested inside it —
    matched at exactly the item's own field column, which is two past its `- ` marker."""
    return re.search(rf'^{" " * (item_indent + 2)}{re.escape(field)}:', item, re.MULTILINE) is not None


def _slot_items(content: str, slot: str) -> Iterator[tuple[str, str, int]]:
    """Yields (item_name, item_block, item_indent) for every named entry under every occurrence
    of `<slot>:`, at any depth — so a `components:` nested three levels down is walked like a
    top-level one. Items are split only on `- ` at the slot's own item column: splitting on any
    `- ` would turn a nested function's arguments into entries of the enclosing slot. Entries that
    are bare strings rather than `- name:` blocks yield an empty name and are skipped by callers —
    those slots hold values, not elements, and have nothing to check."""
    lines = content.splitlines()
    header = re.compile(r'^(\s*)' + re.escape(slot) + r':\s*$')
    for i, header_line in enumerate(lines):
        m = header.match(header_line)
        if m is None:
            continue
        slot_indent = len(m.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if not lines[j].strip():
                continue
            if len(lines[j]) - len(lines[j].lstrip()) <= slot_indent:
                end = j
                break
        block = lines[i + 1:end]
        first = next((l for l in block if l.strip().startswith('- ')), None)
        if first is None:
            continue
        item_indent = len(first) - len(first.lstrip())
        marker = ' ' * item_indent + '- '
        current: list[str] = []
        for item_line in block + [marker]:  # sentinel flushes the final item
            if item_line.startswith(marker) and current:
                name = _item_name(current)
                if name:
                    yield name, '\n'.join(current), item_indent
                current = []
            if item_line.startswith(marker) or current:
                current.append(item_line)


def _slot_mapping(content: str, slot: str) -> Iterator[tuple[list[str], str, int]]:
    """Yields (keys, own_type, key_indent) for every occurrence of `<slot>:` whose body is a block
    of `key: value` lines rather than `- ` entries — the mapping counterpart of _slot_items. A body
    that starts with `- ` belongs to that walk instead, so a slot is read as one shape or the other
    and never as both. `type:` is returned separately rather than among the keys: it names which
    term the mapping is, so reporting it as a field would flag every typed mapping. Without this,
    `settings:`, `workflow:` and `versioning:` are exempt from the field check by accident — the
    entry walk finds no `- ` in them and so reports nothing either way."""
    lines = content.splitlines()
    header = re.compile(r'^(\s*)' + re.escape(slot) + r':\s*$')
    for i, header_line in enumerate(lines):
        m = header.match(header_line)
        if m is None:
            continue
        slot_indent = len(m.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if not lines[j].strip():
                continue
            if len(lines[j]) - len(lines[j].lstrip()) <= slot_indent:
                end = j
                break
        block = [line for line in lines[i + 1:end] if line.strip()]
        if not block or block[0].lstrip().startswith('- '):
            continue
        key_indent = len(block[0]) - len(block[0].lstrip())
        key = re.compile(r'^' + ' ' * key_indent + r'([a-z_][a-z_0-9]*):(.*)$')
        keys, own_type = [], ''
        for line in block:
            m = key.match(line)
            if m is None:
                continue
            if m.group(1) == 'type':
                own = re.match(r'\s*["\']?@(\w+)["\']?\s*$', m.group(2))
                own_type = own.group(1) if own else ''
            else:
                keys.append(m.group(1))
        if keys or own_type:
            yield keys, own_type, key_indent


_DOUBLE_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
_SINGLE_QUOTED = re.compile(r"'(?:[^']|'')*'")


def _is_quoted_scalar(text: str) -> bool:
    """True when the whole text is one complete quoted scalar — not merely a text that starts and
    ends with a quote, like `"Check for updates" or "Update"`."""
    return bool(_DOUBLE_QUOTED.fullmatch(text) or _SINGLE_QUOTED.fullmatch(text))


def _needs_quotes(text: str) -> bool:
    """Whether a value written bare would be read by YAML as something other than its text.
    The one definition of the rule, shared by _quoted when writing and by _unquoted_scalars when
    checking or normalizing, so the two cannot disagree."""
    if not text or _is_quoted_scalar(text):
        return False
    if text != text.strip():
        return True
    if text[0] in '@`&*!|>%#[]{},\'"':
        return True
    if text[:2] in ('- ', '? ', ': ') or text in ('-', '?', ':'):
        return True
    return ': ' in text or ' #' in text or text.endswith(':')


def _quoted(value: str) -> str:
    """A written value, in the form YAML reads as exactly that text. A term reference such as
    `@Recipe` is quoted like anything else: YAML reserves a leading `@`, so leaving references
    bare is what made every term file unreadable to a YAML parser. The readers of `type:` and
    `extends:` accept both forms."""
    text = str(value)
    if not text:
        return "''"
    if not _needs_quotes(text):
        return text
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


_BLOCK_SCALAR = re.compile(r'[|>][+-]?\d*[+-]?')
_KEY_VALUE = re.compile(r'''^(\s*(?:-\s+)?(?:[A-Za-z_][\w.-]*|"[^"]*"|'[^']*'):[ \t]+)(\S.*?)\s*$''')
_BARE_ITEM = re.compile(r'^(\s*-[ \t]+)(\S.*?)\s*$')


def _unquoted_scalars(lines: list[str]) -> list[tuple[int, str, str]]:
    """(index, prefix, value) for every line whose value is written bare although _needs_quotes
    says it must not be. Leaves alone what cannot be rebuilt on one line: the text of block
    scalars, quoted scalars continued over several lines, and bare values continued below."""
    found: list[tuple[int, str, str]] = []
    skip_deeper_than = None   # inside a block scalar: every line indented past this is raw text
    in_quote = None           # inside a quoted scalar continued over lines: its quote character
    for i, raw in enumerate(lines):
        line = raw.rstrip('\n')
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if in_quote:
            body = line.strip()
            if in_quote == '"' and re.search(r'(?<!\\)(?:\\\\)*"\s*$', body):
                in_quote = None
            elif in_quote == "'" and body.endswith("'") and not body.endswith("''"):
                in_quote = None
            continue
        if skip_deeper_than is not None:
            if indent > skip_deeper_than:
                continue
            skip_deeper_than = None
        if line.lstrip().startswith('#'):
            continue
        m = _KEY_VALUE.match(line)
        if m is None:
            item = _BARE_ITEM.match(line)
            if item is None or re.match(r'^(?:[A-Za-z_][\w.-]*):(\s|$)', item.group(2)):
                continue
            m = item
        prefix, value = m.group(1), m.group(2)
        if _BLOCK_SCALAR.fullmatch(value):
            skip_deeper_than = indent
            continue
        if value[0] in '"\'' and not _is_quoted_scalar(value):
            opened = (_DOUBLE_QUOTED if value[0] == '"' else _SINGLE_QUOTED).match(value)
            if opened is None:
                in_quote = value[0]   # opened here, closed on a later line: not ours to touch
                continue
            if re.fullmatch(r'\s+#.*', value[opened.end():]):
                continue   # a quoted value with a comment after it
            # otherwise the quote closes partway, as in `'Roll back' with …`: text a parser
            # stops reading at the closing quote, so the whole of it is wrapped
        if not _needs_quotes(value):
            continue
        continued = next((l for l in lines[i + 1:] if l.strip()), None)
        if continued is not None:
            nxt = continued.rstrip('\n')
            n_indent = len(nxt) - len(nxt.lstrip())
            if (n_indent > indent and not nxt.lstrip().startswith(('- ', '#'))
                    and not re.match(r'^\s*[A-Za-z_][\w.-]*:(\s|$)', nxt)):
                continue   # a bare value folded onto the next line: rewriting one line would break it
        found.append((i, prefix, value))
    return found


def _field_lines(field: str, value, column: int) -> list[str]:
    """One field written at `column`, with whatever it holds nested beneath it: a mapping as keys
    two columns in, a list as `- ` items — an item that is itself a mapping carries its first key
    on the marker line, the way every entry in the format does. Scalars go through _quoted."""
    pad = ' ' * column
    if isinstance(value, dict):
        lines = [f'{pad}{field}:\n']
        for key, inner in value.items():
            lines += _field_lines(key, inner, column + 2)
        return lines
    if isinstance(value, list):
        lines = [f'{pad}{field}:\n']
        for item in value:
            if isinstance(item, dict) and item:
                (first, head), *rest = item.items()
                nested = _field_lines(first, head, column + 4)
                lines.append(f'{pad}  - {nested[0].lstrip()}')
                lines += nested[1:]
                for key, inner in rest:
                    lines += _field_lines(key, inner, column + 4)
            else:
                lines.append(f'{pad}  - {_quoted(item)}\n')
        return lines
    return [f'{pad}{field}: {_quoted(value)}\n']


def _slot_names(content: str) -> Iterator[str]:
    """Yields each distinct `<key>:` header name in the text, at any depth, in first-seen order.
    The `untyped-slot` check needs the slots a term actually uses; the declared ones say nothing
    about what a term put in them."""
    seen = set()
    for m in re.finditer(r'(?m)^\s*([a-z_][a-z_0-9]*):\s*$', content):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            yield name


def _src_values(lines: list[str], i: int, indent: int) -> list[str]:
    """The paths a `src:` on line i declares, written either inline or as a list beneath it.
    Reading only the inline form left every function under a list-form `src:` with no path at
    all, and a function with no path is one this check skips without saying so."""
    inline = re.match(r'^\s*src:\s*(\S.*)$', lines[i])
    if inline:
        return [inline.group(1).strip().strip('\'"')]
    values = []
    for j in range(i + 1, len(lines)):
        if not lines[j].strip():
            continue
        item = re.match(r'^(\s*)-\s+(\S.*)$', lines[j])
        if not item or len(item.group(1)) <= indent:
            break
        values.append(item.group(2).strip().strip('\'"'))
    return values


def _element_paths(content: str) -> list[list[str]]:
    """Module-level pure function. For each line of a term, the `#`-path segments of the element
    it belongs to, so a hit anywhere in the file can name itself. Both kinds of segment count: a
    `key:` opening a block, and a `- id:` entry inside one."""
    stack: list[tuple[int, str]] = []
    paths = []
    for line in content.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped:
            while stack and stack[-1][0] >= indent:
                stack.pop()
        here = [segment for _, segment in stack]
        entry = re.match(r'^-\s+(?:id|name):\s*(\S.*)$', stripped)
        header = re.match(r'^([a-z_][\w-]*):\s*$', stripped)
        if entry or header:
            own = (entry.group(1).strip().strip('\'"') if entry else header.group(1))
            paths.append(here + [own])
            stack.append((indent, own))
        else:
            paths.append(here)
    return paths


def _function_sites(content: str,
                    function_slots: set[str]) -> Iterator[tuple[str, int, list[str], str]]:
    """Yields (function_id, line_number, srcs, type_name) for every entry of a function-holding
    slot, carrying the `src:` and `type:` of the nearest enclosing component — a function is
    verified against the file its own component declares, not the term's top-level one, which
    for a multi-file @Software would be the wrong file or no file at all. `function_slots` comes
    from the declared element types, so a slot is recognised by what it holds rather than by
    being called `functions`."""
    lines = content.splitlines()
    top: list[str] = []
    for i, line in enumerate(lines):
        if re.match(r'^src:', line):
            top = _src_values(lines, i, 0)
            break
    slots: list[tuple[int, str]] = []
    entries: list[tuple[int, list[str], str]] = [(-1, top, '')]

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())

        header = re.match(r'^(\s*)([a-z_][a-z_0-9]*):\s*$', line)
        if header:
            while slots and slots[-1][0] >= indent:
                slots.pop()
            while len(entries) > 1 and entries[-1][0] >= indent:
                entries.pop()
            slots.append((indent, header.group(2)))
            continue

        entry = re.match(r'^(\s*)- id:\s*(\S.*)$', line)
        if not entry:
            continue
        while slots and slots[-1][0] >= indent:
            slots.pop()
        while len(entries) > 1 and entries[-1][0] >= indent:
            entries.pop()
        entry_id = entry.group(2).strip().strip('\'"')

        if slots and slots[-1][1] in function_slots:
            yield entry_id, i + 1, entries[-1][1], entries[-1][2]
            continue

        src, type_name = entries[-1][1], ''
        for j in range(i + 1, len(lines)):
            if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= indent:
                break
            f = re.match(r'^\s{%d}(src|type):\s*(\S*)\s*$' % (indent + 2), lines[j])
            if f and f.group(1) == 'src':
                src = _src_values(lines, j, indent + 2)
            elif f and f.group(2):
                type_name = f.group(2).strip('\'"').lstrip('@')
        entries.append((indent, src, type_name))


_SKIP_DIRS = {'__pycache__', '.git', 'node_modules', '.mypy_cache', '.pytest_cache'}


def _load_settings() -> dict:
    if not _SETTINGS_PATH.is_file():
        return {'active_workspace': None, 'workspaces': {}, 'ducktools': {}}
    try:
        settings = json.loads(_SETTINGS_PATH.read_text())
    except Exception:
        return {'active_workspace': None, 'workspaces': {}, 'ducktools': {}}
    settings.setdefault('active_workspace', None)
    settings.setdefault('workspaces', {})
    settings.setdefault('ducktools', {})
    return settings


def _save_settings(settings: dict) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + '\n')


def _active_projects(settings: dict) -> dict[str, Path]:
    active = settings.get('active_workspace')
    workspace = settings.get('workspaces', {}).get(active)
    if workspace is None:
        return {}
    return {url: Path(p) for url, p in workspace.get('projects', {}).items()}


def _resolve_project(value: str) -> str:
    """A filesystem path stays a path; anything else is looked up in the active workspace by the
    stem of each registered project's path, so `Duckspec` works as an argument from any directory.
    Returns the value untouched when nothing matches, leaving a plain missing-file error rather
    than a confusing lookup one. No command's meaning should depend on the working directory."""
    if '/' in value or value.endswith('.yaml'):
        return value
    for path in _active_projects(_load_settings()).values():
        if Path(path).stem == value:
            return str(path)
    return value


def _resolve_entry(sub: str, base: Path, workspace: dict[str, Path]) -> Path | None:
    if sub.startswith('https://') or sub.startswith('http://'):
        path = workspace.get(sub)
        if path is None:
            print(f'warning: no workspace entry for "{sub}" — skipping')
        return path
    return (base / sub).resolve()


class Resolver:
    def __init__(self) -> None:
        self._search_cache: dict[Path, list[Path]] = {}

    def _collect(self, project_path: Path) -> tuple[list[Path], list[Path]]:
        """Returns (term_folders, project_files) discovered recursively."""
        folders: list[Path] = []
        project_files: list[Path] = []
        visited: set[Path] = set()
        queue = [project_path]
        while queue:
            path = queue.pop(0)
            key = path.resolve()
            if key in visited:
                continue
            visited.add(key)
            try:
                text = path.read_text()
            except Exception:
                continue
            m = _TERMS_FOLDER.search(text)
            if m:
                folder = path.parent / m.group(1)
                if folder.is_dir():
                    folders.append(folder)
            m = _USES_BLOCK.search(text)
            if m:
                for sub in _LIST_ITEM.findall(m.group(1)):
                    sub_path = _resolve_entry(sub, path.parent, _active_projects(_load_settings()))
                    if sub_path is not None:
                        project_files.append(sub_path)
                        queue.append(sub_path)
        return folders, project_files

    def _build_term_map(self, project_path: Path) -> dict[str, Path]:
        folders, project_files = self._collect(project_path)
        term_map: dict[str, Path] = {project_path.stem: project_path}
        for f in project_files:
            if f.is_file():
                term_map.setdefault(f.stem, f)
        for folder in folders:
            for f in sorted(folder.glob('*.yaml')):
                name = f.stem
                if name in term_map and term_map[name] != f:
                    print(f'warning: duplicate term "{name}" at {f} (already at {term_map[name]})')
                else:
                    term_map[name] = f
        return term_map

    def _reachable(self, project_path: Path, term_map: dict[str, Path]) -> dict[str, Path]:
        reachable: dict[str, Path] = {}
        visited: set[Path] = set()
        queue = [project_path]
        while queue:
            path = queue.pop(0)
            key = path.resolve()
            if key in visited:
                continue
            visited.add(key)
            try:
                content = path.read_text()
            except Exception:
                continue
            m = _USES_BLOCK.search(content)
            if m:
                for sub in _LIST_ITEM.findall(m.group(1)):
                    sub_path = _resolve_entry(sub, path.parent, _active_projects(_load_settings()))
                    if sub_path is None:
                        continue
                    name = sub_path.stem
                    if name not in reachable and sub_path.is_file():
                        reachable[name] = sub_path
                        queue.append(sub_path)
            for name in _TERM_REF.findall(content):
                if name in term_map and name not in reachable:
                    reachable[name] = term_map[name]
                    queue.append(term_map[name])
        return reachable

    def _extends_chain_from(self, start_name: str, start_content: str, term_map: dict[str, Path]) -> list[tuple[str, str]]:
        """[(name, content), ...] starting at (start_name, start_content) and following `extends` upward."""
        chain: list[tuple[str, str]] = [(start_name, start_content)]
        seen = {start_name}
        name = _parse_extends(start_content)
        while name and name not in seen:
            seen.add(name)
            term_path = term_map.get(name)
            if term_path is None:
                break
            try:
                content = term_path.read_text()
            except Exception:
                break
            chain.append((name, content))
            name = _parse_extends(content)
        return chain

    def _rules_tree(self, term_name: str, term_map: dict[str, Path]) -> dict:
        """Rules for one term: its own, plus each ancestor's own, grouped separately so scope
        (this term vs. something it merely extends) stays visible instead of flattened."""
        term_path = term_map.get(term_name)
        if term_path is None:
            return {'own': [], 'inherited': []}
        try:
            content = term_path.read_text()
        except Exception:
            return {'own': [], 'inherited': []}
        chain = self._extends_chain_from(term_name, content, term_map)
        own_name, own_content = chain[0]
        inherited = []
        for anc_name, anc_content in chain[1:]:
            anc_rules = _own_rules(anc_content, anc_name)
            if anc_rules:
                inherited.append({'term': anc_name, 'rules': anc_rules})
        return {'own': _own_rules(own_content, own_name), 'inherited': inherited}

    def _project_wide_rules(self, root: Path, root_content: str, term_map: dict[str, Path]) -> list[dict]:
        """Rules that apply regardless of which term is currently being worked on: the root's own
        `extends` chain, plus the own rules (not their further reachable terms — that's term-scoped
        and surfaces via load_terms) of anything the root directly `uses`."""
        chain = self._extends_chain_from(root.stem, root_content, term_map)
        seen = {name for name, _ in chain}
        rules: list[dict] = []
        for name, content in chain:
            rules += _own_rules(content, name)

        m = _USES_BLOCK.search(root_content)
        if m:
            for sub in _LIST_ITEM.findall(m.group(1)):
                sub_path = _resolve_entry(sub, root.parent, _active_projects(_load_settings()))
                if sub_path is None or not sub_path.is_file() or sub_path.stem in seen:
                    continue
                seen.add(sub_path.stem)
                try:
                    use_content = sub_path.read_text()
                except Exception:
                    continue
                rules += _own_rules(use_content, sub_path.stem)
        return rules

    def _walk_terms(self, project_path: str, include_all: bool = False) -> Iterator[tuple[str, Path, str]]:
        """Yields (name, path, content) for each reachable term, reading each file once."""
        path = Path(project_path).resolve()
        term_map = self._build_term_map(path)
        source = term_map if include_all else self._reachable(path, term_map)
        for name, p in sorted(source.items()):
            try:
                content = p.read_text()
            except Exception:
                content = ''
            yield name, p, content

    def load_project(self, project_path: str) -> dict:
        """Returns root file content + term list + aggregated recipes/references + project-wide rules.

        `rules` here is deliberately not every rule from every reachable term — only the root's
        own extends chain and its direct `uses`. Rules scoped to one specific term (e.g. a single
        extension's implementation guidelines) travel with that term instead, attached by
        load_terms, so they surface when the term is actually in play rather than on every load.

        `references` (like `recipes`) aggregates across every reachable term, not just project-wide
        ones — they're compact identity facts (a repository URL + one line), not verbose guidelines,
        so there's no bloat concern in showing all of them. This is what keeps a connection to
        another project visible (e.g. "this installs that GNOME extension") even though the
        resolver never loads that project's content — `references:` is data, not a graph edge.
        """
        project_path = _resolve_project(project_path)
        root = Path(project_path).resolve()
        root_content = root.read_text()
        term_map = self._build_term_map(root)

        recipes = [{'term': root.stem, **r} for r in _parse_recipes(root_content)]
        references = [{'term': root.stem, **r} for r in _parse_references(root_content)]
        rules = self._project_wide_rules(root, root_content, term_map)
        terms = []
        vocabulary: dict[str, list[dict]] = {}

        # A term is this project's own when its file lies under the root file's directory.
        # Anything else came in through another project's `uses:`, and is attributed to the
        # broadest such project containing it, so a framework is one group, not one per
        # sub-project.
        root_dir = root.parent
        _, project_files = self._collect(root)
        sources = sorted(
            {f.resolve() for f in project_files if f.is_file() and not f.resolve().is_relative_to(root_dir)},
            key=lambda f: len(f.parent.parts),
        )

        for name, p, content in self._walk_terms(project_path):
            resolved = p.resolve()
            if resolved == root:
                continue
            m = _DESCRIPTION.search(content)
            entry = {'name': name, 'path': str(p), 'description': m.group(1).strip() if m else ''}
            if resolved.is_relative_to(root_dir):
                terms.append(entry)
            else:
                source = next((f.stem for f in sources if resolved.is_relative_to(f.parent)), 'other')
                vocabulary.setdefault(source, []).append(entry)
            recipes += [{'term': name, **r} for r in _parse_recipes(content)]
            references += [{'term': name, **r} for r in _parse_references(content)]

        return {
            'root_content': root_content,
            'terms': terms,  # already sorted by _walk_terms
            'vocabulary': [{'project': k, 'terms': v} for k, v in sorted(vocabulary.items())],
            'recipes': recipes,
            'references': references,
            'rules': rules,
            'workflow': self._workflow_hint(root_content),
        }

    @staticmethod
    def _workflow_hint(root_content: str) -> dict:
        """Which term describes how work is done here, and a note that it has not been read.

        Opening a project is not the moment to load it: nothing is in hand yet, so its steps and
        rules would be context spent on a question nobody has asked. The name plus the pointer is
        enough for the reader to fetch it when there is actually work to place."""
        block = _extract_key_block(root_content, 'workflow')
        if block is None:
            return {'name': None,
                    'note': 'this project declares no `workflow:`, so nothing describes how work '
                            'is done here'}
        m = re.search(r'^\s+type:\s*["\']?@?(\w+)', block, re.MULTILINE)
        if not m:
            return {'name': None, 'note': "the project's `workflow:` names no `type:`"}
        return {'name': m.group(1),
                'note': f'how work is done here is described by @{m.group(1)}; call '
                        f'load_workflow to read its steps — not loaded with the project, since '
                        f'at open time there is no work to place among them'}

    def load_workflow(self, project_path: str) -> dict:
        """The project's configured workflow in full, with the root's overrides beside it."""
        root = Path(_resolve_project(project_path)).resolve()
        root_content = root.read_text()
        hint = self._workflow_hint(root_content)
        if hint['name'] is None:
            return {'error': hint['note']}
        configuration = _extract_key_block(root_content, 'workflow')
        terms = self.load_terms(project_path, [hint['name']])
        if not terms:
            return {'error': f"unknown term: @{hint['name']}", 'configuration': configuration}
        return {'name': hint['name'], 'configuration': configuration, 'terms': terms}

    def list_terms(self, project_path: str, include_all: bool = False) -> list[dict]:
        project_path = _resolve_project(project_path)
        result = []
        for name, p, content in self._walk_terms(project_path, include_all):
            m = _DESCRIPTION.search(content)
            result.append({'name': name, 'path': str(p), 'description': m.group(1).strip() if m else ''})
        return result

    def load_terms(self, project_path: str, term_names: list[str]) -> list[dict]:
        """Loads the named terms, each followed by its `extends` chain. Only the explicitly
        requested terms (not the ancestors inlined with them) get a `rules` tree attached — own
        guidelines/ai_instructions plus each `extends` ancestor's own, kept separate so scope
        stays visible instead of flattened into one list — and a `references` list naming the
        terms they mention without loading them."""
        project_path = _resolve_project(project_path)
        path = Path(project_path).resolve()
        term_map = self._build_term_map(path)
        requested = [n for n in term_names if n in term_map]
        result: list[dict] = []
        seen: set[str] = set()
        for n in requested:
            try:
                own = term_map[n].read_text()
            except Exception:
                own = ''
            for name, content in self._extends_chain_from(n, own, term_map):
                if name in seen:
                    continue
                seen.add(name)
                entry = {'name': name, 'path': str(term_map[name]), 'content': content}
                if name in requested:
                    entry['rules'] = self._rules_tree(name, term_map)
                    # neighbours by name, not by content: a mention is not a dependency, and
                    # following them reaches the whole corpus through the @Term and
                    # @DuckspecProject hubs. Naming them is what lets the reader pick a branch.
                    entry['references'] = sorted(
                        {r for r in _TERM_REF.findall(content)
                         if r in term_map and r != name and r not in
                         {a for a, _ in self._extends_chain_from(name, content, term_map)}})
                result.append(entry)
        return result

    def grep_terms(self, project_path: str, query: str, include_all: bool = False) -> list[dict]:
        """Each hit carries the `#`-path of the element it sits in, not just its text. A bare
        matching line says nothing about whose line it is, so every hit used to need the file
        opened again to find out; the ref it comes back with now is the one resolve_path and
        set_field already take."""
        project_path = _resolve_project(project_path)
        q = query.lower()
        results = []
        for name, p, content in self._walk_terms(project_path, include_all):
            paths = _element_paths(content)
            hits = [{'ref': '#'.join([name] + paths[i]), 'line': i + 1, 'text': line.strip()}
                    for i, line in enumerate(content.splitlines()) if q in line.lower()]
            if hits:
                results.append({'name': name, 'path': str(p), 'hits': hits,
                                'lines': [h['text'] for h in hits]})
        return results

    def resolve_path(self, project_path: str, ref: str) -> dict | None:
        project_path = _resolve_project(project_path)
        term_name, *segments = ref.split('#')
        term_name = term_name.lstrip('@')

        path = Path(project_path).resolve()
        term_map = self._build_term_map(path)
        term_path = term_map.get(term_name)
        if term_path is None:
            return None

        try:
            content = term_path.read_text()
        except Exception:
            return None

        for segment in segments:
            # Refuse an ambiguous segment instead of returning whichever entry came first, the
            # way _locate already does for the editing operations. A stale duplicate of an entire
            # recipe sat in DuckspecProject.yaml across five releases precisely because nothing
            # that reads a path ever objected to there being two of something.
            if segment.isdigit():
                block = _nth_item(content, int(segment))
                if block is None:
                    return None
                content = block
                continue
            candidates = _find_named_blocks(content, segment)
            if len(candidates) > 1:
                return {'name': term_name, 'path': str(term_path), 'ref': ref, 'content': '',
                        'error': f"ambiguous: '{segment}' matches {len(candidates)} elements "
                                 f"in {ref} — the spec has more than one, which is itself a defect"}
            block = candidates[0] if candidates else _extract_key_block(content, segment)
            if block is None:
                return None
            content = block

        return {'name': term_name, 'path': str(term_path), 'ref': ref, 'content': content}

    def _duplicate_terms(self, project_path: Path) -> list[dict]:
        """One term name defined in several files. Kept out of _build_term_map, which resolves
        collisions silently and sits on the hot path of every other operation; only directory
        listings are re-read here, never file contents."""
        folders, project_files = self._collect(project_path)
        seen: dict[str, Path] = {}
        duplicates: list[dict] = []
        for f in project_files:
            if f.is_file():
                seen.setdefault(f.stem, f)
        for folder in folders:
            for f in sorted(folder.glob('*.yaml')):
                if f.stem in seen and seen[f.stem] != f:
                    duplicates.append({'name': f.stem, 'path': f, 'first_path': seen[f.stem]})
                else:
                    seen.setdefault(f.stem, f)
        return duplicates


    def _schema(self, term: str, term_map: dict[str, Path], cache: dict[str, str]) -> set[str]:
        """Every member a thing of this type may carry — the term's own `properties:` ids plus
        every ancestor's. Empty when the term is unknown, which makes the caller skip rather than
        report everything as unknown."""
        members: set[str] = set()
        current, guard = term, set()
        while current in term_map and current not in guard:
            guard.add(current)
            content = cache.get(current)
            if content is None:
                try:
                    content = term_map[current].read_text()
                except Exception:
                    break
                cache[current] = content
            members |= set(_member_names(content, _PROPERTIES_BLOCK))
            current = _parse_extends(content)
        return members

    def verify_project(self, project_path: str, unreachable: bool = False,
                       untyped: bool = False) -> list[dict]:
        """Mechanical consistency pass — everything provable about a spec without reading source
        or judging meaning. Returns findings sorted errors-first; an empty list means the spec is
        internally consistent. The semantic half (do descriptions match the implementation?) is
        @DuckspecProject#verify_source's job and cannot be settled by regex."""
        project_path = _resolve_project(project_path)
        root = Path(project_path).resolve()
        term_map = self._build_term_map(root)
        reachable = self._reachable(root, term_map) if unreachable else {}
        cache: dict[str, str] = {}
        findings: list[dict] = []
        slot_types: dict[str, str] = {}
        own_members: dict[str, dict[str, list[str]]] = {}

        def add(severity, check, term, path, message, line=None):
            finding = {'severity': severity, 'check': check, 'term': term,
                       'path': str(path), 'message': message}
            if line is not None:
                finding['line'] = line
            findings.append(finding)

        def read(name: str) -> str:
            if name not in cache:
                try:
                    cache[name] = term_map[name].read_text()
                except Exception:
                    cache[name] = ''
            return cache[name]

        for dup in self._duplicate_terms(root):
            add('error', 'duplicate-term', dup['name'], dup['path'],
                f"also defined at {dup['first_path']}")

        workspace = _active_projects(_load_settings())   # once, not once per term with a `uses:`
        own_paths = set(self._own_files(root))
        for name, path, content in self._walk_terms(project_path, include_all=True):
            cache[name] = content
            for slot, element in _slot_element_types(content).items():
                slot_types.setdefault(slot, element)
            own_members[name] = _member_names(content, _PROPERTIES_BLOCK)

            if not _DESCRIPTION.search(content):
                add('error', 'unparsed-term', name, path, 'no top-level description:')
                continue

            # Only this project's own files: a term reached from another project is fixed there.
            if Path(path).resolve() in own_paths:
                for i, _prefix, value in _unquoted_scalars(content.splitlines(keepends=True)):
                    shown = value if len(value) <= 60 else value[:57] + '...'
                    add('error', 'unquoted-scalar', name, path,
                        f"'{shown}' is written bare, and YAML would not read it as text — quote "
                        f"it, or run normalize on the project", i + 1)

            # A `uses:` entry that resolves to nothing was skipped in silence, so the project it
            # names simply vanished from the term map and the report blamed the dangling
            # references that followed instead of the one line that caused them.
            m = _USES_BLOCK.search(content)
            if m:
                for entry in _LIST_ITEM.findall(m.group(1)):
                    if entry.startswith(('http://', 'https://')):
                        if workspace.get(entry) is None:
                            add('warning', 'broken-uses', name, path,
                                f"'{entry}' has no entry in the active workspace — the project is "
                                f"not registered here, so nothing it defines is in scope",
                                _line_of(content, m.start() + m.group(0).index(entry)))
                    elif not (path.parent / entry).resolve().exists():
                        add('error', 'broken-uses', name, path,
                            f"'{entry}' does not resolve to a file — everything that project "
                            f"defines is missing from this one",
                            _line_of(content, m.start() + m.group(0).index(entry)))

            parent = _parse_extends(content)
            if parent and parent not in term_map:
                add('error', 'unknown-extends', name, path, f'extends unknown term @{parent}')
            elif parent:
                walked = {name}
                current = parent
                while current in term_map:
                    if current in walked:
                        add('error', 'extends-cycle', name, path,
                            f'extends chain revisits @{current}')
                        break
                    walked.add(current)
                    current = _parse_extends(read(current))
                    if not current:
                        break

            for m in _TERM_REF.finditer(content):
                referenced = m.group(1)
                if referenced in term_map or referenced == root.stem:
                    continue
                add('error', 'dangling-ref', name, path,
                    f'@{referenced} is not a term in this project — define it, reach its '
                    f'project via `uses:`, or write it as @<{referenced}> if it is a placeholder',
                    _line_of(content, m.start()))

            for m in _PATH_REF.finditer(content):
                referenced = m.group(1)
                if referenced not in term_map:
                    continue  # unknown terms are already reported as dangling-ref
                segments = [s for s in m.group(2).split('#') if s]
                narrowed = read(referenced)
                for segment in segments:
                    candidates = _find_named_blocks(narrowed, segment)
                    if len(candidates) > 1:
                        add('error', 'ambiguous-path-ref', name, path,
                            f"@{referenced}#{'#'.join(segments)} is ambiguous — "
                            f"'{segment}' matches {len(candidates)} entries; qualify the path "
                            f"with the parent that scopes it",
                            _line_of(content, m.start()))
                        break
                    block = candidates[0] if candidates else _extract_key_block(narrowed, segment)
                    if block is None:
                        add('error', 'broken-path-ref', name, path,
                            f"@{referenced}#{'#'.join(segments)} does not resolve — "
                            f"no '{segment}' in @{referenced}",
                            _line_of(content, m.start()))
                        break
                    narrowed = block

        for name, path in sorted(term_map.items()):
            content = cache.get(name)
            if content is None:
                continue

            parent = _parse_extends(content)
            if parent is None:
                continue
            for ancestor, ancestor_content in self._extends_chain_from(name, content, term_map)[1:]:
                ancestor_members = own_members.get(ancestor)
                if ancestor_members is None:
                    continue
                for member in own_members[name]:
                    if member in ancestor_members:
                        add('error', 'redeclared-member', name, path,
                            f"'{member}' is already declared on @{ancestor} — "
                            f"inheritance replaces it silently")

        for name, path in sorted(term_map.items()):
            content = cache.get(name)
            if content is None:
                continue
            # An unresolvable or cyclic extends chain means _schema saw only part of the
            # vocabulary; unknown-extends and extends-cycle already report that, and listing every
            # inherited field as unknown on top of it would bury the real finding.
            chain, ancestor, complete = {name}, _parse_extends(content), True
            while ancestor:
                if ancestor not in term_map or ancestor in chain:
                    complete = False
                    break
                chain.add(ancestor)
                ancestor = _parse_extends(cache.get(ancestor, ''))
            if not complete:
                continue
            allowed = self._schema(name, term_map, cache) | _STRUCTURAL_FIELDS
            for m in _TOP_FIELD.finditer(content):
                if m.group(1) not in allowed:
                    add('error', 'unknown-field', name, path,
                        f"'{m.group(1)}' is set here, which neither @{name} nor anything in its "
                        f"extends chain declares",
                        _line_of(content, m.start()))

        for name, path in sorted(term_map.items()):
            content = cache.get(name)
            if content is None:
                continue
            for slot, element in slot_types.items():
                allowed = self._schema(element, term_map, cache)
                if not allowed:
                    continue
                for item_id, item, indent in _slot_items(content, slot):
                    column = ' ' * (indent + 2)
                    own = re.search(rf'^{column}type:\s*["\']?@(\w+)', item, re.MULTILINE)
                    fields = allowed | (self._schema(own.group(1), term_map, cache) if own else set())
                    for key in re.findall(rf'^{column}([a-z_]+):', item, re.MULTILINE):
                        if key not in fields:
                            add('error', 'unknown-field', name, path,
                                f"{slot} entry '{item_id}' sets '{key}', which no type it has "
                                f"declares (@{element}"
                                + (f" + @{own.group(1)}" if own else "") + ")")
                for keys, own_type, _ in _slot_mapping(content, slot):
                    fields = allowed | (self._schema(own_type, term_map, cache) if own_type else set())
                    for key in keys:
                        if key not in fields:
                            add('error', 'unknown-field', name, path,
                                f"{slot} sets '{key}', which no type it has "
                                f"declares (@{element}"
                                + (f" + @{own_type}" if own_type else "") + ")")

        # A step's operation has to be something that can be run. broken-path-ref only proves the
        # reference lands somewhere, and a component describing a pipeline file is somewhere — a
        # step pointing at one looks implemented while nothing could ever perform it.
        call = re.compile(r'^"?@([A-Z]\w*)#([A-Za-z_][\w-]*)(?:\((.*)\))?"?$')
        for name, path in sorted(term_map.items()):
            content = cache.get(name)
            if content is None:
                continue
            for step_id, item, indent in _slot_items(content, 'steps'):
                op = re.search(rf'^{" " * (indent + 2)}operation:\s*(.+?)\s*$', item, re.MULTILINE)
                if not op:
                    continue
                m = call.match(op.group(1))
                if not m:
                    add('error', 'broken-operation', name, path,
                        f"step '{step_id}' has operation {op.group(1)}, which is not a call of the "
                        f"form @<TermName>#<recipe>(<argument>=<value>, ...)")
                    continue
                target, recipe, args = m.group(1), m.group(2), m.group(3)
                if target not in term_map:
                    continue                      # dangling-ref already names it
                block = None
                for _, anc in self._extends_chain_from(target, cache.get(target) or
                                                       term_map[target].read_text(), term_map):
                    rb = _RECIPES_BLOCK.search(anc)
                    found = _find_named_blocks(rb.group(0), recipe) if rb else []
                    if found:
                        block = found[0]
                        break
                if block is None:
                    add('error', 'broken-operation', name, path,
                        f"step '{step_id}' calls @{target}#{recipe}, which is not a recipe of "
                        f"@{target} or anything it extends — something that describes a file is "
                        f"not something that can be run")
                    continue
                if args:
                    arguments = _extract_key_block(block, 'arguments') or ''
                    declared = set(re.findall(r'^\s*-\s+id:\s*(\S+)', arguments, re.MULTILINE))
                    for given in re.findall(r'(?:^|,)\s*([A-Za-z_]\w*)\s*=', args):
                        if given not in declared:
                            add('error', 'broken-operation', name, path,
                                f"step '{step_id}' passes '{given}' to @{target}#{recipe}, which "
                                f"declares no such argument")

        if unreachable:
            for name, path in sorted(term_map.items()):
                if name not in reachable and path.resolve() != root:
                    add('warning', 'unreachable-term', name, path,
                        'no reachable term mentions this term')

        if untyped:
            for name, path in sorted(term_map.items()):
                content = cache.get(name)
                if content is None:
                    continue
                for slot in _slot_names(content):
                    if slot in slot_types:
                        continue
                    entries = sum(1 for _ in _slot_items(content, slot))
                    mappings = sum(1 for _ in _slot_mapping(content, slot))
                    if not entries and not mappings:
                        continue  # holds bare strings or nothing — no fields to check
                    held = (f'{entries} entr{"y" if entries == 1 else "ies"}' if entries
                            else f'{mappings} mapping{"" if mappings == 1 else "s"}')
                    add('warning', 'untyped-slot', name, path,
                        f"'{slot}' holds {held} that unknown-field did not read, because no type "
                        f"declares what the slot holds. A slot with a fixed shape wants "
                        f"`type: @X`; one whose keys vary with the thing it sits on cannot have "
                        f"one, and stays unverified until a check is written for it")

        findings.sort(key=lambda f: (f['severity'] != 'error', f['path'], f.get('line', 0)))
        return findings

    def _source_root(self, term_path: Path) -> Path:
        """The directory a term's `src:` values resolve against: the `settings.src` (default `..`)
        of the project whose `terms_folder:` names the folder the term sits in. Matched on that
        declaration, not on the folder's name, which need not match the project's. A term no
        project claims — a root project file itself — is read as its own project."""
        folder = term_path.parent.resolve()
        project = term_path
        for candidate in sorted(folder.parent.glob('*.yaml')):
            try:
                declared = re.search(r'(?m)^terms_folder:\s*(\S+)\s*$', candidate.read_text())
            except OSError:
                continue
            if declared and (candidate.parent / declared.group(1).strip('\'"').rstrip('/')
                             ).resolve() == folder:
                project = candidate    # the file naming this terms folder owns what is in it
                break
        try:
            content = project.read_text()
        except OSError:
            return term_path.parent
        m = re.search(r'(?m)^settings:\s*$((?:\n[ \t]+.*|\n\s*)*)', content)
        src = '..'
        if m:
            s = re.search(r'(?m)^\s+src:\s*(\S+)\s*$', m.group(1))
            if s:
                src = s.group(1).strip('\'"')
        return (project.parent / src).resolve()

    def _source_files(self, target: Path) -> list[Path]:
        """The files a function id is looked for in. A component may point at a folder rather
        than one file — @DuckToolsApp's `ducktools/src/` is the whole package — and skipping
        those silently is how the first run of this check read none of its 103 functions while
        reporting nothing. Each file is returned separately so the caller searches them one at a
        time: concatenating a folder let a symbol defined in one module answer for a component
        pointing at another, and pulled `__pycache__` bytecode into the text besides."""
        if target.is_file():
            return [target]
        cached = self._search_cache.get(target)
        if cached is None:
            cached = [f for f in sorted(target.rglob('*'))
                      if f.is_file() and not _SKIP_DIRS.intersection(f.parts)]
            self._search_cache[target] = cached
        return cached

    def verify_source(self, project_path: str) -> list[dict]:
        """Mechanical half of @DuckspecProject#verify_source: declared paths exist, and named
        functions are present in the file their component points at. Whether a description tells
        the truth about the code is the other half, and stays a reading task."""
        project_path = _resolve_project(project_path)
        root = Path(project_path).resolve()
        term_map = self._build_term_map(root)
        cache = {name: path.read_text() for name, path in term_map.items()}
        self._search_cache.clear()   # sources may have changed since the last run
        findings: list[dict] = []

        implicit: dict[str, set[str]] = {}
        for name, content in cache.items():
            m = re.search(r'(?m)^implicit_functions:\s*(.+)$', content)
            if m:
                implicit[name] = {v.strip() for v in m.group(1).split(',') if v.strip()}

        slot_types: dict[str, str] = {}
        for term_content in cache.values():
            for slot, element in _slot_element_types(term_content).items():
                slot_types.setdefault(slot, element)

        def is_function_type(type_name: str) -> bool:
            seen = set()
            current = type_name
            while current and current not in seen:
                if current == 'Function':
                    return True
                seen.add(current)
                if current not in cache:
                    return False
                current = _parse_extends(cache[current])
            return False

        function_slots = {slot for slot, element in slot_types.items()
                          if is_function_type(element)}

        def inherited_implicit(type_name: str) -> set[str]:
            out, seen = set(), set()
            current = type_name
            while current and current in cache and current not in seen:
                seen.add(current)
                out |= implicit.get(current, set())
                current = _parse_extends(cache[current])
            return out

        for name, path in sorted(term_map.items()):
            if root.parent not in path.parents:
                continue   # a dependency's source belongs to its own project
            content = cache[name]
            src_root = self._source_root(path)
            lines = content.splitlines()

            in_settings = False
            for i, line in enumerate(lines):
                if line.strip() and not line[:1].isspace():
                    in_settings = line.startswith('settings:')
                m = re.match(r'^\s*(?:- )?src:\s*(\S+)\s*$', line)
                if not m or in_settings:
                    continue   # settings.src defines the source root; it is not a path within it
                value = m.group(1).strip('\'"')
                if value.startswith('@'):
                    continue
                if value.startswith('~'):
                    continue   # a path in the user's home, not something this repo ships
                if not (src_root / value).exists():
                    findings.append({'severity': 'error', 'check': 'missing-src', 'term': name,
                                     'path': str(path), 'line': i + 1,
                                     'message': f"src '{value}' does not exist under {src_root}"})

            for fn_id, fn_line, srcs, type_name in _function_sites(content, function_slots):
                targets = [src_root / s for s in srcs if (src_root / s).exists()]
                if not targets:
                    continue   # nothing declared, or already reported as missing-src
                if fn_id in inherited_implicit(type_name):
                    continue
                bare = fn_id.split('.')[-1]
                needle = re.compile(r'\b' + re.escape(bare) + r'\b')
                found = False
                for target in targets:
                    for source_file in self._source_files(target):
                        try:
                            text = source_file.read_text()
                        except (OSError, UnicodeDecodeError):
                            continue      # binary, or unreadable — it defines nothing
                        if needle.search(text):
                            found = True
                            break
                    if found:
                        break
                if not found:
                    findings.append({'severity': 'error', 'check': 'absent-function', 'term': name,
                                     'path': str(path), 'line': fn_line,
                                     'message': f"function '{fn_id}' appears nowhere in "
                                                f"{', '.join(srcs)} — renamed, or moved to "
                                                f"another file"})

        findings.sort(key=lambda f: (f['severity'] != 'error', f['path'], f.get('line', 0)))
        return findings

    def term_uses(self, project_path: str, term_name: str) -> dict:
        """Reverse index: who extends this term, who mentions it, who names it as a `type:`."""
        project_path = _resolve_project(project_path)
        term_map = self._build_term_map(Path(project_path).resolve())
        out = {'extended_by': [], 'referenced_by': [], 'typed_by': []}
        for name, path in sorted(term_map.items()):
            if name == term_name:
                continue
            try:
                content = path.read_text()
            except Exception:
                continue
            if _parse_extends(content) == term_name:
                out['extended_by'].append(name)
            if re.search(rf'^\s*type:\s*["\']?@{re.escape(term_name)}\b', content, re.MULTILINE):
                out['typed_by'].append(name)
            elif term_name in _TERM_REF.findall(content):
                out['referenced_by'].append(name)
        return out

    def term_schema(self, project_path: str, term_name: str) -> list[dict]:
        """Every member the term effectively has, tagged with the ancestor that declared it."""
        project_path = _resolve_project(project_path)
        term_map = self._build_term_map(Path(project_path).resolve())
        if term_name not in term_map:
            return []
        chain = self._extends_chain_from(term_name, term_map[term_name].read_text(), term_map)
        seen, out = set(), []
        for ancestor, content in chain:
            for member in _member_names(content, _PROPERTIES_BLOCK):
                if member not in seen:
                    seen.add(member)
                    out.append({'member': member, 'declared_by': ancestor})
        return out

    def query_terms(self, project_path: str, rootless: bool = False, extending: str | None = None,
                    declaring: str | None = None, folder: str | None = None) -> list[str]:
        """Filters the term map by structure rather than by text."""
        project_path = _resolve_project(project_path)
        term_map = self._build_term_map(Path(project_path).resolve())
        out = []
        for name, path in sorted(term_map.items()):
            try:
                content = path.read_text()
            except Exception:
                continue
            if rootless and _parse_extends(content):
                continue
            if folder and folder not in str(path):
                continue
            if extending:
                chain = [a for a, _ in self._extends_chain_from(name, content, term_map)[1:]]
                if extending not in chain:
                    continue
            if declaring and declaring not in _member_names(content, _PROPERTIES_BLOCK):
                continue
            out.append(name)
        return out

    def slot_entries(self, project_path: str, term_name: str, slot: str) -> list[dict]:
        """Entries of one slot as {id, fields} — fields set on the entry itself, not on nested ones."""
        project_path = _resolve_project(project_path)
        term_map = self._build_term_map(Path(project_path).resolve())
        if term_name not in term_map:
            return []
        content = term_map[term_name].read_text()
        out = []
        for item_id, item, indent in _slot_items(content, slot):
            fields = re.findall(rf'^{" " * (indent + 2)}([a-z_]+):', item, re.MULTILINE)
            out.append({'id': item_id, 'fields': fields})
        return out

    def _locate(self, project_path: str, ref: str,
                slot: bool = False) -> tuple[Path, list[str], int, int] | str:
        """(file, lines, start, end) for the element `ref` addresses, or an error string. Narrows
        segment by segment like resolve_path, but tracking line numbers, and refuses an ambiguous
        step instead of taking the first match. With `slot`, the last segment prefers a mapping
        key over a named entry of the same name, since the caller needs the element holding a list."""
        term_name, *segments = ref.split('#')
        term_name = term_name.lstrip('@')
        term_map = self._build_term_map(Path(_resolve_project(project_path)).resolve())
        if term_name not in term_map:
            return f'unknown term: @{term_name}'
        path = term_map[term_name]
        lines = path.read_text().splitlines(keepends=True)
        start, end = 0, len(lines)
        for n, segment in enumerate(segments):
            window = ''.join(lines[start:end])
            prefer_key = slot and n == len(segments) - 1
            keyed = prefer_key and _extract_key_block(window, segment) is not None
            if (not segment.isdigit() and not keyed
                    and len(_find_named_blocks(window, segment)) > 1):
                return f"ambiguous: '{segment}' matches more than one element in {ref}"
            block = _narrow(window, segment, prefer_key=prefer_key)
            if block is None:
                return f"not found: '{segment}' in {ref}"
            # Found by its whole run of lines, not its first line alone: two list items can open
            # with the same line, and a digits-only segment picks one of them by position.
            wanted = [l.rstrip() for l in block.splitlines()]
            have = [l.rstrip() for l in lines[start:end]]
            offset = next(i for i in range(len(have)) if have[i:i + len(wanted)] == wanted)
            start += offset
            end = start + len(wanted)
        return path, lines, start, end

    @staticmethod
    def _field_column(head: str) -> int:
        """The column the fields of the element opening on `head` sit at."""
        indent = len(head) - len(head.lstrip())
        if head.lstrip().startswith('- ') or re.match(r'^\s*[\w-]+:\s*$', head):
            return indent + 2   # a list item or a key opening a block nests its fields
        return indent           # the term itself: fields sit at the top level

    @staticmethod
    def _field_span(lines: list[str], start: int, end: int, field: str) -> tuple[int, int, int] | None:
        """(first line, line past the last, column) of `field` inside the element spanning
        lines[start:end], or None when the element has no such field. The span takes in the lines a
        value is continued on — a bare or quoted value folded below, a block scalar's body — so a
        caller replacing it leaves nothing of the old value stranded."""
        head = lines[start]
        column = Resolver._field_column(head)
        # An entry's first field lives on the `- ` line itself, indented two columns short of
        # its siblings. Matching only the sibling column meant `id` was never found on an entry
        # and a second `id:` was appended below the first, leaving two.
        if re.match(rf'^(\s*-\s+){re.escape(field)}:', head):
            return start, start + 1, column
        pattern = re.compile(rf'^{" " * column}{re.escape(field)}:')
        for i in range(start, end):
            if pattern.match(lines[i]):
                old = lines[i].split(':', 1)[1].strip()
                block_scalar = bool(_BLOCK_SCALAR.fullmatch(old))
                j = i + 1
                while j < end and (block_scalar and not lines[j].strip()
                                   or lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) > column
                                   and (block_scalar
                                        or not lines[j].lstrip().startswith('- ')
                                        and not re.match(r'^\s*[A-Za-z_][\w.-]*:(\s|$)', lines[j]))):
                    j += 1
                while j > i + 1 and not lines[j - 1].strip():
                    j -= 1   # blank lines after a block scalar belong to the file, not the value
                return i, j, column
        return None

    def set_field(self, project_path: str, ref: str, field: str, value: str) -> str:
        located = self._locate(project_path, ref)
        if isinstance(located, str):
            return located
        path, lines, start, end = located
        value = _quoted(value)
        span = self._field_span(lines, start, end, field)
        if span is None:
            lines.insert(start + 1, f'{" " * self._field_column(lines[start])}{field}: {value}\n')
            path.write_text(''.join(lines))
            return f'{path}: added {field}'
        i, j, column = span
        on_head = re.match(rf'^(\s*-\s+){re.escape(field)}:', lines[i])
        # A value continued on the lines below belongs to this field too, and replacing only the
        # first line of it would leave the rest stranded under the new value.
        lines[i:j] = [f'{on_head.group(1) if on_head else " " * column}{field}: {value}\n']
        path.write_text(''.join(lines))
        return f'{path}: replaced {field}'

    def edit_field(self, project_path: str, ref: str, field: str, old: str, new: str) -> str:
        """Replaces one piece of a field's text with another, leaving the rest as it was. A long
        description gaining a sentence otherwise had to be rewritten whole through set_field, every
        character of it retyped, or edited by hand outside the tools. The piece has to occur
        exactly once, so an edit never lands somewhere its author did not look."""
        located = self._locate(project_path, ref)
        if isinstance(located, str):
            return located
        path, lines, start, end = located
        span = self._field_span(lines, start, end, field)
        if span is None:
            return f'not found: {ref} has no field {field}'
        i, j, _ = span
        first = re.sub(rf'^\s*(?:-\s+)?{re.escape(field)}:', '', lines[i].rstrip('\n')).strip()
        rest = [l.strip() for l in lines[i + 1:j]]
        if _BLOCK_SCALAR.fullmatch(first) or '' in rest:
            return f'refused: {field} spans paragraphs; rewrite it with set_field'
        text = ' '.join([first] + rest)
        if _DOUBLE_QUOTED.fullmatch(text):
            try:
                text = json.loads(text)
            except ValueError:
                return f'refused: {field} uses an escape only YAML knows; rewrite it with set_field'
        elif _SINGLE_QUOTED.fullmatch(text):
            text = text[1:-1].replace("''", "'")
        count = text.count(old) if old else 0
        if count != 1:
            return f'refused: the text to replace occurs {count} times in {field}, not once'
        return self.set_field(project_path, ref, field, text.replace(old, new))

    def add_entry(self, project_path: str, ref: str, entry_id: str,
                  fields: dict | None = None, after: str | None = None,
                  before: str | None = None) -> str:
        """Appends a named entry to the slot `ref` addresses. The item column comes from the
        entries already in the slot, so a nested slot lands at its own depth instead of a guessed
        one — hand-written indentation is what put a field outside its block during the identity
        migration. A slot that does not exist yet is created on the element the rest of the path
        addresses, the way add_item creates a missing list. `after` or `before` names the sibling
        the entry goes next to, for a slot whose order is its meaning — a workflow's steps."""
        if after is not None and before is not None:
            return 'refused: `after` and `before` both given — an entry has one place'
        anchor = after if after is not None else before
        located = self._locate(project_path, ref, slot=True)
        if isinstance(located, str):
            if anchor is not None and located.startswith('not found'):
                return f"refused: '{anchor}' is not an entry of {ref}, which does not exist yet"
            parent_ref, _, slot_name = ref.rpartition('#')
            if not parent_ref or not located.startswith('not found') or slot_name.isdigit():
                return located
            parent = self._locate(project_path, parent_ref)
            if isinstance(parent, str):
                return located
            path, lines, p_start, p_end = parent
            if '#' not in parent_ref:
                column, at = 0, len(lines)   # the term itself: a new top-level slot goes last
                if lines and not lines[-1].endswith('\n'):
                    lines[-1] += '\n'
            else:
                p_head = lines[p_start]
                column = len(p_head) - len(p_head.lstrip()) + 2
                at = p_end
                while at > p_start + 1 and not lines[at - 1].strip():
                    at -= 1
            lines.insert(at, f'{" " * column}{slot_name}:\n')
            path.write_text(''.join(lines))
            located = self._locate(project_path, ref, slot=True)
            if isinstance(located, str):
                return located
        path, lines, start, end = located
        head = lines[start]
        if not re.match(r'^\s*[\w-]+:\s*$', head):
            return f'refused: {ref} is not a slot — it does not open a block'

        body = [l for l in lines[start + 1:end] if l.strip()]
        existing = [l for l in body if l.lstrip().startswith('- ')]
        column = (len(existing[0]) - len(existing[0].lstrip()) if existing
                  else len(head) - len(head.lstrip()) + 2)

        for line in existing:
            m = re.match(r'^\s*- id:\s*(\S.*)$', line)
            if m and m.group(1).strip().strip('\'"') == entry_id:
                return (f"refused: '{entry_id}' is already an entry of {ref} — two entries "
                        f"under one id cannot be told apart by a #-path")

        block = [f'{" " * column}- id: {entry_id}\n']
        for field, value in (fields or {}).items():
            block += _field_lines(field, value, column + 2)

        at = end
        while at > start + 1 and not lines[at - 1].strip():
            at -= 1   # keep trailing blank lines below the slot, not inside it
        if anchor is not None:
            # siblings are the `- ` lines at the slot's own item column; anything deeper belongs
            # to one of them, so a nested entry carrying the same id is never taken for the anchor
            siblings = [i for i in range(start + 1, at)
                        if re.match(rf'^ {{{column}}}- ', lines[i])]
            hit = next((n for n, i in enumerate(siblings)
                        if (m := re.match(r'^\s*- id:\s*(\S.*)$', lines[i]))
                        and m.group(1).strip().strip('\'"') == anchor), None)
            if hit is None:
                return f"refused: '{anchor}' is not an entry of {ref}"
            if before is not None:
                at = siblings[hit]
            elif hit + 1 < len(siblings):
                at = siblings[hit + 1]   # the anchor's own block ends where the next one starts
        lines[at:at] = block
        path.write_text(''.join(lines))
        return f'{path}: added {entry_id} to {ref}'

    def remove_element(self, project_path: str, ref: str) -> str:
        if '#' not in ref:
            return 'refused: a bare term name removes a whole file, which this does not do'
        located = self._locate(project_path, ref)
        if isinstance(located, str):
            return located
        path, lines, start, end = located
        del lines[start:end]
        path.write_text(''.join(lines))
        return f'{path}: removed {end - start} line(s)'

    @staticmethod
    def _find_item(lines: list[str], start: int, end: int, match: str):
        """(i, j) bounding the one item in lines[start:end] whose text contains `match`, or an
        error string. Refuses more than one hit rather than editing whichever came first."""
        hits = [i for i in range(start + 1, end)
                if lines[i].lstrip().startswith('- ') and match in lines[i]]
        if not hits:
            return f'no item matching {match!r}'
        if len(hits) > 1:
            return f'{len(hits)} items match {match!r} — narrow it'
        i = hits[0]
        base = len(lines[i]) - len(lines[i].lstrip())
        j = i + 1
        while j < end and lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) > base:
            j += 1
        return i, j

    @staticmethod
    def _item_line(text: str, indent: int) -> str:
        """One list item at the given column, its text written through _quoted like every other
        value, so a leading `@` or a `: ` inside it cannot turn it into something else."""
        return " " * indent + "- " + _quoted(text) + "\n"

    @staticmethod
    def _item_text(item_lines: list[str]) -> str:
        """The text of one list item, as _item_line would take it — the inverse of that function.
        Double quotes are undone together with the escapes inside them, single quotes with their
        doubled `''`, and a value continued over several lines is joined the way YAML folds it."""
        text = ' '.join(l.strip() for l in item_lines)[2:].strip()
        if len(text) >= 2 and text[0] == text[-1] == '"':
            return re.sub(r'\\(.)', r'\1', text[1:-1])
        if len(text) >= 2 and text[0] == text[-1] == "'":
            return text[1:-1].replace("''", "'")
        return text

    def _term_file(self, project_path: str, term_name: str) -> Path | None:
        term_map = self._build_term_map(Path(_resolve_project(project_path)).resolve())
        return term_map.get(term_name)


    def add_item(self, project_path: str, ref: str, text: str, after: str | None = None) -> str:
        located = self._locate(project_path, ref, slot=True)
        if isinstance(located, str):
            head, _, block = ref.rpartition("#")
            if not head:
                return located
            parent = self._locate(project_path, head)
            if isinstance(parent, str):
                return parent
            path, lines, p_start, p_end = parent
            # p_start == 0 means the parent is the whole file, so the block sits at column 0
            indent = 0 if p_start == 0 else len(lines[p_start]) - len(lines[p_start].lstrip()) + 2
            anchor = p_end
            if p_start == 0:
                anchor = next((i for i, l in enumerate(lines)
                               if l.startswith(("properties:", "recipes:"))), len(lines))
            lines[anchor:anchor] = [f"{' ' * indent}{block}:\n",
                                    self._item_line(text, indent + 2)]
            path.write_text("".join(lines))
            return f"{path}: created {block} and added to it"
        path, lines, start, end = located
        indent = len(lines[start]) - len(lines[start].lstrip()) + 2
        at = end
        if after is not None:
            found = self._find_item(lines, start, end, after)
            if isinstance(found, str):
                return found
            at = found[1]
        lines.insert(at, self._item_line(text, indent))
        path.write_text("".join(lines))
        return f"{path}: added to {ref}"

    def set_item(self, project_path: str, ref: str, match: str, text: str) -> str:
        located = self._locate(project_path, ref, slot=True)
        if isinstance(located, str):
            return located
        path, lines, start, end = located
        found = self._find_item(lines, start, end, match)
        if isinstance(found, str):
            return found
        i, j = found
        indent = len(lines[i]) - len(lines[i].lstrip())
        lines[i:j] = [self._item_line(text, indent)]
        path.write_text("".join(lines))
        return f"{path}: replaced in {ref}"

    def remove_item(self, project_path: str, ref: str, match: str) -> str:
        located = self._locate(project_path, ref, slot=True)
        if isinstance(located, str):
            return located
        path, lines, start, end = located
        found = self._find_item(lines, start, end, match)
        if isinstance(found, str):
            return found
        i, j = found
        del lines[i:j]
        if end - start == j - i + 1:          # the block held only that item
            del lines[start]
        path.write_text("".join(lines))
        return f"{path}: removed from {ref}"

    def move_item(self, project_path: str, from_ref: str, to_ref: str, match: str) -> str:
        located = self._locate(project_path, from_ref, slot=True)
        if isinstance(located, str):
            return located
        _, lines, start, end = located
        found = self._find_item(lines, start, end, match)
        if isinstance(found, str):
            return found
        i, j = found
        text = self._item_text(lines[i:j])
        removed = self.remove_item(project_path, from_ref, match)
        if "removed from" not in removed:
            return removed
        return self.add_item(project_path, to_ref, text)

    def create_term(self, project_path: str, term_name: str, description: str,
                    extends: str = 'Term') -> str:
        root = Path(_resolve_project(project_path)).resolve()
        m = _TERMS_FOLDER.search(root.read_text())
        if not m:
            return 'project declares no terms_folder'
        folder = root.parent / m.group(1)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{term_name}.yaml'
        if path.exists():
            return f'already exists: {path}'
        path.write_text(f'description: {_quoted(description)}\n'
                        f'extends: {_quoted("@" + extends.lstrip("@"))}\n')
        return f'{path}: created'

    def _own_files(self, root: Path) -> list[Path]:
        """The root project file and every term file under its directory — what normalize and the
        unquoted-scalar check cover. Terms reached from another project are that project's."""
        term_map = self._build_term_map(root)
        own = {root}
        own.update(p.resolve() for p in term_map.values() if p.resolve().is_relative_to(root.parent))
        return sorted(own)

    @staticmethod
    def _normalized(text: str) -> tuple[str, int]:
        """The text with every value _unquoted_scalars finds written through _quoted, and the count."""
        lines = text.splitlines(keepends=True)
        found = _unquoted_scalars(lines)
        for i, prefix, value in found:
            ending = '\n' if lines[i].endswith('\n') else ''
            lines[i] = prefix + _quoted(value) + ending
        return ''.join(lines), len(found)

    def normalize(self, project_path: str) -> list[dict]:
        """Rewrites every term file of the project so each value YAML would misread is quoted.
        Returns [{path, quoted}] for the files it changed."""
        root = Path(_resolve_project(project_path)).resolve()
        changed = []
        for path in self._own_files(root):
            try:
                text = path.read_text()
            except Exception:
                continue
            new, count = self._normalized(text)
            if count:
                path.write_text(new)
                changed.append({'path': str(path), 'quoted': count})
        return changed

    def replace_term(self, project_path: str, term_name: str, content: str) -> str:
        """Replaces the whole content of an existing term file, normalized on the way in."""
        path = self._term_file(project_path, term_name)
        if path is None:
            return f'unknown term: @{term_name.lstrip("@")}'
        if not _DESCRIPTION.search(content):
            return 'refused: the content has no top-level description:, which every term needs'
        new, _ = self._normalized(content if content.endswith('\n') else content + '\n')
        path.write_text(new)
        return f'{path}: replaced'

    def rename_term(self, project_path: str, old_name: str, new_name: str) -> str:
        resolved = _resolve_project(project_path)
        term_map = self._build_term_map(Path(resolved).resolve())
        if old_name not in term_map:
            return f'unknown term: @{old_name}'
        if new_name in term_map:
            return f'already taken: @{new_name}'
        old_path = term_map[old_name]
        touched = 0
        for name, path in term_map.items():
            try:
                content = path.read_text()
            except Exception:
                continue
            updated = re.sub(rf'@{re.escape(old_name)}\b', f'@{new_name}', content)
            if updated != content:
                path.write_text(updated)
                touched += 1
        old_path.rename(old_path.with_name(f'{new_name}.yaml'))
        return f'@{old_name} -> @{new_name}; {touched} file(s) updated'

    def remove_term(self, project_path: str, term_name: str) -> str:
        uses = self.term_uses(project_path, term_name)
        blocking = uses['extended_by'] + uses['typed_by'] + uses['referenced_by']
        if blocking:
            return f'refused: still referenced by ' + ', '.join('@' + n for n in sorted(set(blocking)))
        path = self._term_file(project_path, term_name)
        if path is None:
            return f'unknown term: @{term_name}'
        path.unlink()
        return f'{path}: removed'

    def create_workspace(self, name: str, activate: bool = True) -> None:
        settings = _load_settings()
        settings['workspaces'].setdefault(name, {'projects': {}})
        if activate or settings.get('active_workspace') is None:
            settings['active_workspace'] = name
        _save_settings(settings)

    def use_workspace(self, name: str) -> bool:
        settings = _load_settings()
        if name not in settings['workspaces']:
            return False
        settings['active_workspace'] = name
        _save_settings(settings)
        return True

    def list_projects(self) -> dict:
        """Every registered workspace (not just the active one) with its projects enriched by
        name/description read from each project's own root file — so browsing the registry
        doesn't require opening each file to see what it is. A project whose file is missing or
        unreadable (moved, deleted, bad path) still lists with name=None rather than being
        silently dropped, so a stale registry entry is visible instead of hidden."""
        settings = _load_settings()
        workspaces = {}
        for wname, workspace in settings.get('workspaces', {}).items():
            projects = []
            for repository, path in workspace.get('projects', {}).items():
                name = None
                description = ''
                try:
                    content = Path(path).read_text()
                    name = Path(path).stem
                    m = _DESCRIPTION.search(content)
                    description = m.group(1).strip() if m else ''
                except Exception:
                    pass
                projects.append({'repository': repository, 'path': path, 'name': name, 'description': description})
            workspaces[wname] = {'projects': projects}
        return {'active_workspace': settings.get('active_workspace'), 'workspaces': workspaces}

    def add_project(self, project_path: str, workspace_name: str | None = None) -> str | None:
        """Registers project_path under its own `repository` in workspace_name (or the active workspace). Returns the repository URL used, or None if the project has no `repository` field."""
        try:
            content = Path(project_path).read_text()
        except Exception:
            content = ''
        m = _REPOSITORY.search(content)
        if not m:
            return None
        repository = m.group(1)

        settings = _load_settings()
        name = workspace_name or settings.get('active_workspace')
        if name is None:
            return None
        settings['workspaces'].setdefault(name, {'projects': {}})
        settings['workspaces'][name].setdefault('projects', {})[repository] = str(Path(project_path).resolve())
        _save_settings(settings)
        return repository

    def remove_project(self, repository: str, workspace_name: str | None = None) -> bool:
        settings = _load_settings()
        name = workspace_name or settings.get('active_workspace')
        workspace = settings.get('workspaces', {}).get(name)
        if workspace is None or repository not in workspace.get('projects', {}):
            return False
        del workspace['projects'][repository]
        _save_settings(settings)
        return True


resolver = Resolver()
