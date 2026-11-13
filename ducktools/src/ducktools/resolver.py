import json
import re
import sys
from pathlib import Path
from typing import Iterator

_TERM_REF = re.compile(r'@([A-Z][A-Za-z]+)')
_TERMS_FOLDER = re.compile(r'^\s*terms_folder:\s*(\S+)', re.MULTILINE)
# Items at column 0 are valid YAML too; missing them dropped the framework from a project silently
_SOFTWARE_BLOCK = re.compile(r'^software:\n((?:[ \t]*-[ \t]+.+\n?)+)', re.MULTILINE)
_USES_BLOCK = re.compile(r'^uses:\n((?:[ \t]*-[ \t]+\S+\n?)+)', re.MULTILINE)
_LIST_ITEM = re.compile(r'-\s+(\S+)')
_REPOSITORY = re.compile(r'^repository:\s*(\S+)', re.MULTILINE)
_DESCRIPTION = re.compile(r'^description:\s*(.+)$', re.MULTILINE)
_EXTENDS = re.compile(r'''^extends:\s*["']?@?([A-Za-z_]\w*)''', re.MULTILINE)
_RECIPES_BLOCK = re.compile(r'^recipes:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_REFERENCES_BLOCK = re.compile(r'^references:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_GUIDELINES_BLOCK = re.compile(r'^guidelines:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_PROPERTIES_BLOCK = re.compile(r'^properties:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_PATH_REF = re.compile(r'@([A-Z][A-Za-z]+)((?:#[A-Za-z_][\w-]*)+)')
_TOP_FIELD = re.compile(r'^([a-z_][\w-]*):', re.MULTILINE)

# Fields every term may carry regardless of what it extends: its identity, its content, and the
# blocks through which it declares members for its subtypes. Everything else at the top level has
# to be declared as a `properties:` entry somewhere in the extends chain, or it is an unknown-field.
_STRUCTURAL_FIELDS = frozenset({
    'description', 'extends', 'name',
    'properties', 'recipes', 'guidelines',
})

_SETTINGS_PATH = Path.home() / '.duckspec' / 'settings.json'


def _parse_list_block(content: str, pattern: re.Pattern) -> list[str]:
    m = pattern.search(content)
    if not m:
        return []
    return [
        _unquote(line.strip()[2:])
        for line in m.group(1).splitlines()
        if line.strip().startswith('- ')
    ]


def _parse_extends(content: str) -> str | None:
    m = _EXTENDS.search(content)
    return m.group(1).strip() if m else None


def _own_rules(content: str, term_name: str) -> list[dict]:
    """Guidelines declared directly on one term's content (no inheritance)."""
    return [{'term': term_name, 'type': 'Guideline', 'text': g}
            for g in _parse_list_block(content, _GUIDELINES_BLOCK)]


_SOFTWARE_NON_CODE_SLOTS = {'behaviour', 'scenarios', 'tests', 'decisions', 'recipes', 'steps',
                            'branches', 'channels', 'guidelines', 'codestyle'}
_LOCAL_REF = re.compile(r'(?<![@\w/.`:-])([a-z][a-z0-9_-]*(?:#[A-Za-z0-9_.-]*[A-Za-z0-9_])+)')


def _unquote(value: str) -> str:
    """A scalar as YAML reads it, for the quoting the resolver writes: double quotes with JSON-like
    escapes, single quotes with '' for a quote, or bare."""
    v = value.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def _element_fields(block: str) -> list[tuple[str, str, object]]:
    """The fields an element holds itself — not those of anything nested in it — as
    (key, kind, value): kind 'scalar' with the text, 'strings' with a list of texts, 'entries'
    with a list of field lists (one per named entry), or 'mapping' with the raw lines. `block` is
    a whole term file, a `- id:` list item or a `key:` block as resolve_path narrows it. Written
    for the subset of YAML term files use, without a YAML parser (see @DuckTools's guidelines)."""
    lines = block.splitlines()
    if not lines:
        return []
    first = lines[0]
    stripped = first.lstrip()
    if stripped.startswith('- '):
        col = len(first) - len(stripped) + 2
        lines = [' ' * col + stripped[2:]] + lines[1:]
    nonblank = [l for l in lines if l.strip()]
    if not nonblank:
        return []
    col = min(len(l) - len(l.lstrip()) for l in nonblank)
    out: list[tuple[str, str, object]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or len(line) - len(line.lstrip()) != col:
            i += 1
            continue
        m = re.match(r'^\s*([A-Za-z_][\w-]*):(?:\s+(.*))?$', line)
        if not m:
            i += 1
            continue
        key, inline = m.group(1), (m.group(2) or '').strip()
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > col):
            j += 1
        body = lines[i + 1:j]
        i = j
        if inline and inline not in ('|', '>', '|-', '>-'):
            text = inline
            if body:   # a plain or quoted scalar continued on the following lines
                text = ' '.join([inline] + [b.strip() for b in body if b.strip()])
            out.append((key, 'scalar', _unquote(text)))
            continue
        if inline in ('|', '>', '|-', '>-'):
            rest = [b for b in body]
            ind = min((len(b) - len(b.lstrip()) for b in rest if b.strip()), default=0)
            joiner = '\n' if inline.startswith('|') else ' '
            out.append((key, 'scalar', joiner.join(b[ind:] for b in rest).strip()))
            continue
        items = [b for b in body if b.strip()]
        if not items:
            out.append((key, 'scalar', ''))
            continue
        if not items[0].lstrip().startswith('- '):
            out.append((key, 'mapping', body))
            continue
        icol = len(items[0]) - len(items[0].lstrip())
        chunks: list[list[str]] = []
        for b in body:
            if b.strip() and len(b) - len(b.lstrip()) == icol and b.lstrip().startswith('- '):
                chunks.append([b])
            elif chunks:
                chunks[-1].append(b)
        if all(re.match(r'^\s*-\s+(?:id|name):', c[0]) for c in chunks):
            out.append((key, 'entries', [_element_fields('\n'.join(c)) for c in chunks]))
        else:
            texts = []
            for c in chunks:
                head = c[0].lstrip()[2:]
                more = [x.strip() for x in c[1:] if x.strip()]
                texts.append(_unquote(' '.join([head] + more)) if more else _unquote(head))
            out.append((key, 'strings', texts))
    return out


def _field(fields: list[tuple[str, str, object]], key: str, default=None):
    for k, _kind, v in fields:
        if k == key:
            return v
    return default


def _description(content: str) -> str:
    """A term's `description` as YAML reads it — unquoted, and whole when written as a block
    scalar (`|`, `>`) or continued over several lines."""
    m = _DESCRIPTION.search(content)
    if not m:
        return ''
    head = m.group(1).strip()
    if head not in ('|', '>', '|-', '>-') and not content[m.end():m.end() + 2].startswith(('\n ', '\n\t')):
        return _unquote(head)
    return _field(_element_fields(content), 'description', '') or ''


def _term_edges(contents: dict[str, str], known: set[str], skip: set[str],
                chains: dict[str, set[str]]) -> dict[str, dict[str, list[str]]]:
    """refers_to / referred_by for the given terms, from their `@<TermName>` mentions."""
    edges = {n: {'refers_to': [], 'referred_by': []} for n in contents}
    for name, content in contents.items():
        seen = []
        for ref in _TERM_REF.findall(content):
            if ref in known and ref != name and ref not in skip and ref not in chains.get(name, set()) \
                    and ref not in seen:
                seen.append(ref)
        edges[name]['refers_to'] = seen
    for name in contents:
        edges[name]['referred_by'] = [other for other in contents
                                      if name in edges[other]['refers_to']]
    return edges


_DATE = re.compile(r'\b20\d\d-\d\d-\d\d\b')


def _git_root(path: Path) -> Path | None:
    """The nearest directory at or above `path` holding `.git` — the repository a file is in."""
    for d in [path.resolve()] + list(path.resolve().parents):
        if (d / '.git').exists():
            return d
    return None


def _term_files(folder: Path) -> list[Path]:
    """Every `.yaml` in a terms folder and its subfolders, at any depth: a project groups its
    terms in subfolders of one `terms_folder` rather than splitting into sub-projects."""
    return sorted(folder.rglob('*.yaml'))


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
                'description': _unquote(desc_m.group(1)) if desc_m else '',
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
    """The entries with this id at the shallowest depth any of them has. `#`-paths skip levels, so
    the search reaches every depth, but an entry nested below another is no rival of it; counting
    what is left is what tells an unambiguous path from one that resolved to whichever came first."""
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
        blocks.append((item_indent, '\n'.join(lines[i:end]).rstrip()))
    # an entry deep inside another's subtree is no rival of one higher up: keep the shallowest
    if not blocks:
        return []
    top = min(indent for indent, _ in blocks)
    return [block for indent, block in blocks if indent == top]


def _child(block: str, segment: str, root: bool = False) -> tuple[str | None, str | None]:
    """One step of a full `#`-path: the child of `block` named `segment` among its direct children
    only, as (child, None), or (None, why). `block` is a whole term file (`root`), a `- id:` entry,
    or a `key:` block. The children of a term or an entry are its own fields, found at its field
    column and nowhere deeper; those of a key holding a list are its items, by `id`/`name` or by
    position for a digits-only segment; those of a key holding a mapping are that mapping's keys.
    Nothing below the next level is looked at, so a name used again elsewhere in the term is never
    a rival, and two items of one list under one id are refused as ambiguous."""
    lines = block.splitlines()
    if not lines:
        return None, None

    def _indent(s: str) -> int:
        return len(s) - len(s.lstrip())

    def _until_dedent(i: int, col: int) -> str:
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if lines[j].strip() and _indent(lines[j]) <= col:
                end = j
                break
        return '\n'.join(lines[i:end]).rstrip()

    def _own_key(col: int, first: int) -> tuple[str | None, str | None]:
        target = re.compile(rf'^ {{{col}}}{re.escape(segment)}:(\s|$)')
        for i in range(first, len(lines)):
            if lines[i].strip() and _indent(lines[i]) < col:
                break
            if target.match(lines[i]):
                return _until_dedent(i, col), None
        return None, None

    if root:
        return _own_key(0, 0)
    head = lines[0]
    if head.lstrip().startswith('- '):
        col = _indent(head) + 2
        inline = re.match(rf'^\s*-\s+{re.escape(segment)}:(\s|$)', head)
        if inline:
            # the field written on the entry's own first line, `- id: x`, as a line of its own
            return ' ' * col + head.lstrip()[2:], None
        return _own_key(col, 1)
    key = re.match(r'^(\s*)[\w.-]+:\s*(.*)$', head)
    if key is None or (key.group(2) and key.group(2) not in ('|', '>', '|-', '>-')):
        return None, None  # a scalar has no children
    body = [i for i in range(1, len(lines)) if lines[i].strip()]
    if not body:
        return None, None
    col = _indent(lines[body[0]])
    if not lines[body[0]].lstrip().startswith('- '):
        return _own_key(col, body[0])
    items = [i for i in body if _indent(lines[i]) == col and lines[i].lstrip().startswith('- ')]
    if segment.isdigit():
        n = int(segment)
        return (_until_dedent(items[n], col), None) if n < len(items) else (None, None)
    named = re.compile(r'^-\s+(?:id|name):\s*[\'"]?' + re.escape(segment) + r'[\'"]?\s*$')
    hits = [i for i in items if named.match(lines[i].strip())]
    if len(hits) > 1:
        return None, f"ambiguous: {len(hits)} entries of one list are called '{segment}' — ids must be unique among siblings"
    return (_until_dedent(hits[0], col), None) if hits else (None, None)


def _all_named(content: str, name: str) -> list[str]:
    """Every entry and every block key called `name` at any depth of `content` — the elements a
    path that is not a full one may have meant, for the hint a refusal gives."""
    lines = content.splitlines()
    pattern = re.compile(r'^(\s*)(?:-\s+(?:id|name):\s*[\'"]?' + re.escape(name) + r'[\'"]?\s*|'
                         + re.escape(name) + r':(?:\s*|\s+[|>]-?\s*))$')
    found = []
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        col = len(m.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= col:
                end = j
                break
        found.append('\n'.join(lines[i:end]).rstrip())
    return found


def _candidate_paths(file_text: str, window: str, blocks: list[str], term_name: str) -> list[str]:
    """The full `#`-path of each of `blocks` — entries _find_named_blocks found in `window`, a
    stretch of the term file `file_text` — every slot and parent named, so a reader refused for
    an ambiguous segment is shown the paths that each reach one of them."""
    lines = file_text.splitlines()
    base = max(file_text.find(window), 0)
    paths = []
    for block in blocks:
        offset = file_text.find(block, base)
        if offset < 0:
            continue
        base = offset + 1
        n = file_text.count('\n', 0, offset)
        indent = len(lines[n]) - len(lines[n].lstrip())
        first = lines[n].strip()
        chain = [first[2:].split(':', 1)[1].strip().strip('\'"') if first.startswith('- ')
                 else first.split(':', 1)[0]]
        for k in range(n - 1, -1, -1):
            s = lines[k]
            if not s.strip() or len(s) - len(s.lstrip()) >= indent:
                continue
            indent = len(s) - len(s.lstrip())
            m = re.match(r'\s*-\s+(?:id|name):\s*[\'"]?([^\'"\s]+)', s) or re.match(r'\s*([\w.-]+):', s)
            if m:
                chain.append(m.group(1))
            if indent == 0:
                break
        paths.append('#'.join([term_name] + chain[::-1]))
    return paths


def _element_owners(content: str) -> list[list[str]]:
    """For each line of a term, the full-path segments of the innermost named element it belongs
    to — the entry whose own text it is, slots included, or [] for the term's own lines. A key
    block inside an entry (`rejected:`, `instructions:`) is part of that entry, not an element."""
    stack: list[tuple[int, str, bool]] = []
    owners = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            indent = len(line) - len(line.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            entry = re.match(r'^-\s+(?:id|name):\s*[\'"]?([^\'"]+?)[\'"]?\s*$', stripped)
            header = re.match(r'^([\w.-]+):\s*$', stripped)
            if entry:
                stack.append((indent, entry.group(1), True))
            elif header:
                stack.append((indent, header.group(1), False))
        last = max((i for i, s in enumerate(stack) if s[2]), default=-1)
        owners.append([s[1] for s in stack[:last + 1]])
    return owners


def _with_elements(findings: list[dict]) -> list[dict]:
    """Each finding that has a line gets `ref`: the full path of the element that line belongs
    to, so the finding can be opened and fixed by path rather than by counting lines."""
    owners: dict[str, list[list[str]]] = {}
    for f in findings:
        if 'line' not in f:
            continue
        if f['path'] not in owners:
            try:
                owners[f['path']] = _element_owners(Path(f['path']).read_text(encoding='utf-8'))
            except OSError:
                owners[f['path']] = []
        lines = owners[f['path']]
        if 0 < f['line'] <= len(lines):
            f['ref'] = '#'.join([Path(f['path']).stem, *lines[f['line'] - 1]])
    return findings


_LINE_LEAD = re.compile(r'^\s*(?:-\s+)?(?:[\w.-]+:(?:\s+|$))?')


def _duplicate_ids(content: str) -> list[tuple[int, str]]:
    """(line index, id) of every entry whose id an earlier entry of the same list already has —
    the one thing that keeps a full `#`-path from naming exactly one element. Lists are told apart
    by their item column and by what encloses them: a line less indented than a list closes it."""
    seen: dict[int, set[str]] = {}
    out = []
    for i, line in enumerate(content.splitlines()):
        if not line.strip():
            continue
        col = len(line) - len(line.lstrip())
        for c in [c for c in seen if c > col]:
            del seen[c]
        m = re.match(r'^-\s+(?:id|name):\s*[\'"]?([^\'"]+?)[\'"]?\s*$', line.strip())
        if not line.lstrip().startswith('- '):
            seen.pop(col, None)
            continue
        ids = seen.setdefault(col, set())
        if m:
            if m.group(1) in ids:
                out.append((i, m.group(1)))
            ids.add(m.group(1))
    return out


def _fit_paths(file_text: str, window: str, segments: list[str], term_name: str,
               root: bool = False) -> list[str]:
    """The full paths a path that may skip levels can mean, carried through to its last segment:
    each segment is taken as a direct child where there is one and as any element so named below
    otherwise (_all_named), and the ends are named by _candidate_paths."""
    def _ends(block: str, rest: list[str], at_root: bool) -> list[str]:
        if not rest:
            return [block]
        child, _why = _child(block, rest[0], root=at_root)
        found = [child] if child is not None else \
            [b for b in _all_named(block, rest[0]) if b != block]
        out: list[str] = []
        for b in found:
            out += _ends(b, rest[1:], False)
        return out
    return list(dict.fromkeys(_candidate_paths(file_text, window, _ends(window, segments, root), term_name)))


def _path_hint(file_text: str, window: str, segments: list[str], term_name: str,
               root: bool = False) -> str:
    """For a path that skipped levels, the refusal's pointer to the full paths that fit, so one
    refusal gives the caller the path to ask for, not just the next level."""
    paths = _fit_paths(file_text, window, segments, term_name, root)
    if not paths:
        return ''
    return 'a path names every level; the full paths that fit: ' + ', '.join(paths[:6])


def _extract_key_block(content: str, name: str) -> str | None:
    """The counterpart to _find_named_blocks for plain mapping keys — `functions:`, `properties:`,
    `guidelines:` — which are not list items and so have no `- name:` to match. Captures the key
    line plus every following line indented deeper than it. A scalar key yields just its own line."""
    lines = content.splitlines()
    target = re.compile(r'^(\s*)' + re.escape(name) + r':(\s|$)')
    # the shallowest match is the element's own key; one nested in an entry below it comes later
    found = [(len(m.group(1)), i) for i, line in enumerate(lines) if (m := target.match(line))]
    for key_indent, i in sorted(found)[:1]:
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if not lines[j].strip():
                continue
            if len(lines[j]) - len(lines[j].lstrip()) <= key_indent:
                end = j
                break
        return '\n'.join(lines[i:end]).rstrip()
    return None


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


def _quote_close(lines: list[str], i: int, value: str) -> int | None:
    """The line a quoted value opening on line `i` closes on: `i` itself when `value` is complete
    there, a later line when it is continued, None when it never closes. Read the way
    _unquoted_scalars reads a continued value, so _field_span and _stray_text agree with it."""
    quote = value[0]
    if (_DOUBLE_QUOTED if quote == '"' else _SINGLE_QUOTED).match(value):
        return i
    for j in range(i + 1, len(lines)):
        body = lines[j].strip()
        if quote == '"' and re.search(r'(?<!\\)(?:\\\\)*"\s*$', body):
            return j
        if quote == "'" and body.endswith("'") and not body.endswith("''"):
            return j
    return None


def _stray_text(lines: list[str]) -> list[int]:
    """Indexes of the lines that belong to no field: text indented under a field whose quoted
    value has already closed, being neither a field nor a list item. YAML reads nothing after a
    closing quote but a comment, while the resolver's regexes read past such a line silently."""
    stray: list[int] = []
    closed_at = None          # indentation of the line whose quoted value has closed
    skip_deeper_than = None   # inside a block scalar: every line indented past this is its text
    i = 0
    while i < len(lines):
        line = lines[i].rstrip('\n')
        if not line.strip() or line.lstrip().startswith('#'):
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if skip_deeper_than is not None:
            if indent > skip_deeper_than:
                i += 1
                continue
            skip_deeper_than = None
        if closed_at is not None:
            if (indent > closed_at and not line.lstrip().startswith('-')
                    and not re.match(r'^\s*[A-Za-z_][\w.-]*:(\s|$)', line)):
                stray.append(i)
                i += 1
                continue
            closed_at = None
        m = _KEY_VALUE.match(line) or _BARE_ITEM.match(line)
        if m is not None:
            value = m.group(2)
            if _BLOCK_SCALAR.fullmatch(value):
                skip_deeper_than = indent
            elif value[0] in '"\'':
                end = _quote_close(lines, i, value)
                if end is None:
                    break   # never closes: past this point the file cannot be read at all
                closed_at = indent
                i = end + 1
                continue
        i += 1
    return stray


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
        settings = json.loads(_SETTINGS_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'active_workspace': None, 'workspaces': {}, 'ducktools': {}}
    settings.setdefault('active_workspace', None)
    settings.setdefault('workspaces', {})
    settings.setdefault('ducktools', {})
    return settings


def _save_settings(settings: dict) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8', newline='')


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


def _register_framework(workspace_name: str | None = None) -> tuple[str, str, bool] | None:
    """Registers the framework clone at `ducktools.src` in workspace_name (or the active one)
    unless the workspace already has an entry for its repository. Every project names the
    framework in `uses:` by URL, so a workspace without it loses the framework's terms; an
    existing entry is kept, since on a developer's machine it points at their working copy."""
    settings = _load_settings()
    src = settings['ducktools'].get('src')
    if not src:
        return None
    framework = (Path(src).parent / 'Duckspec.yaml').resolve()
    try:
        m = _REPOSITORY.search(framework.read_text(encoding='utf-8'))
    except Exception:
        return None
    workspace = settings['workspaces'].get(workspace_name or settings.get('active_workspace'))
    if m is None or workspace is None:
        return None
    projects = workspace.setdefault('projects', {})
    if m.group(1) in projects:
        return m.group(1), projects[m.group(1)], False
    projects[m.group(1)] = str(framework)
    _save_settings(settings)
    return m.group(1), str(framework), True


def _resolve_entry(sub: str, base: Path, workspace: dict[str, Path]) -> Path | None:
    if sub.startswith('https://') or sub.startswith('http://'):
        path = workspace.get(sub)
        if path is None:
            print(f'warning: no workspace entry for "{sub}" — skipping', file=sys.stderr)
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
                text = path.read_text(encoding='utf-8')
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
            for f in _term_files(folder):
                name = f.stem
                if name in term_map and term_map[name] != f:
                    print(f'warning: duplicate term "{name}" at {f} (already at {term_map[name]})', file=sys.stderr)
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
                content = path.read_text(encoding='utf-8')
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
                content = term_path.read_text(encoding='utf-8')
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
            content = term_path.read_text(encoding='utf-8')
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
                    use_content = sub_path.read_text(encoding='utf-8')
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
                content = p.read_text(encoding='utf-8')
            except Exception:
                content = ''
            yield name, p, content

    def load_project(self, project_path: str) -> dict:
        """The top level of a project and nothing below it — see @DuckToolsApp#resolver#load_project.
        Own terms are split into `parts` (named by the root's `software:`) and `terms`, each with
        its description and its edges; other projects appear once each in `projects`; the
        framework's terms are the `vocabulary`; recipes are this project's own and those of the
        framework terms it extends."""
        project_path = _resolve_project(project_path)
        root = Path(project_path).resolve()
        root_content = root.read_text(encoding='utf-8')
        term_map = self._build_term_map(root)
        root_dir = root.parent
        framework = term_map.get('Duckspec')
        fw_dir = framework.resolve().parent if framework else None

        def is_framework(p: Path) -> bool:
            return fw_dir is not None and p.resolve().is_relative_to(fw_dir) and not root.is_relative_to(fw_dir)

        own: dict[str, Path] = {}
        vocabulary = []
        contents: dict[str, str] = {}
        for name, p, content in self._walk_terms(project_path):
            resolved = p.resolve()
            if resolved == root:
                continue
            if resolved.is_relative_to(root_dir):
                own[name] = p
                contents[name] = content
            elif is_framework(p):
                vocabulary.append({'name': name, 'path': str(p), 'description': _description(content)})

        fw_names = {n for n, p in term_map.items() if is_framework(p)}
        chains = {n: {a for a, _ in self._extends_chain_from(n, c, term_map)} for n, c in contents.items()}
        edges = _term_edges(contents, set(term_map), fw_names, chains)

        software = [s.strip().strip('\'"').lstrip('@')
                    for s in _parse_list_block(root_content, _SOFTWARE_BLOCK)]

        def row(name):
            return {'name': name, 'path': str(own[name]),
                    'description': _description(contents[name]), **edges[name]}

        parts = [row(n) for n in software if n in own]
        terms = [row(n) for n in sorted(own) if n not in software]

        # other projects, one node each
        projects, seen_projects = [], set()
        workspace = _active_projects(_load_settings())
        um = _USES_BLOCK.search(root_content)
        for entry in (_LIST_ITEM.findall(um.group(1)) if um else []):
            entry = entry.strip('\'"')
            target = _resolve_entry(entry, root_dir, workspace)
            if target is None or not target.is_file():
                continue
            target = target.resolve()
            if target.is_relative_to(root_dir) or (fw_dir and target.is_relative_to(fw_dir)) \
                    or target in seen_projects:
                continue
            seen_projects.add(target)
            projects.append({'name': target.stem, 'repository': entry,
                             'connection': f'uses — its terms resolve here; open it with load_project({target.stem})',
                             'description': _description(target.read_text(encoding='utf-8'))})
        refs = _parse_references(root_content) + [r for n in sorted(contents) for r in _parse_references(contents[n])]
        for r in refs:
            repo = r.get('repository', '')
            target = workspace.get(repo)
            name = Path(target).stem if target else repo
            if target and Path(target).resolve() in seen_projects:
                continue
            if target:
                seen_projects.add(Path(target).resolve())
            desc = ''
            if target and Path(target).is_file():
                desc = _description(Path(target).read_text(encoding='utf-8'))
            projects.append({'name': name, 'repository': repo,
                             'connection': 'reference — ' + r.get('description', ''),
                             'description': desc})

        # recipes: own, root's, and those of the framework terms own terms extend
        extended = set()
        for n, c in list(contents.items()) + [(root.stem, root_content)]:
            extended |= {a for a, _ in self._extends_chain_from(n, c, term_map)} & fw_names
        recipes = [{'term': root.stem, **r} for r in _parse_recipes(root_content)]
        for n in sorted(contents):
            recipes += [{'term': n, **r} for r in _parse_recipes(contents[n])]
        for n in sorted(extended):
            try:
                recipes += [{'term': n, **r} for r in _parse_recipes(term_map[n].read_text(encoding='utf-8'))]
            except Exception:
                pass

        return {
            'root_content': root_content,
            'workflow': self._workflow_hint(root_content),
            'parts': parts,
            'terms': terms,
            'projects': projects,
            'vocabulary': sorted(vocabulary, key=lambda v: v['name']),
            'recipes': recipes,
            'rules': self._project_wide_rules(root, root_content, term_map),
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
        root_content = root.read_text(encoding='utf-8')
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
            result.append({'name': name, 'path': str(p), 'description': _description(content)})
        return result

    def load_terms(self, project_path: str, term_names: list[str]) -> list[dict]:
        """Loads the named terms, each followed by its `extends` chain. Only the explicitly
        requested terms (not the ancestors inlined with them) get a `rules` tree attached — own
        guidelines plus each `extends` ancestor's own, kept separate so scope
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
                own = term_map[n].read_text(encoding='utf-8')
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
        """The elements whose own text holds every word of `query` — see
        @DuckToolsApp#components#resolver#functions#grep_terms."""
        project_path = _resolve_project(project_path)
        words = [w for w in query.lower().split() if w]
        if not words:
            return []
        results = []
        for name, p, content in self._walk_terms(project_path, include_all):
            lines = content.splitlines()
            groups: dict[tuple, list[tuple[int, str]]] = {}
            for i, owner in enumerate(_element_owners(content)):
                text = _LINE_LEAD.sub('', lines[i]).strip().strip('"\'')
                if text:
                    groups.setdefault(tuple(owner), []).append((i, text))
            for owner, parts in groups.items():
                joined = ' '.join(text for _, text in parts)
                low = joined.lower()
                if not all(w in low for w in words):
                    continue
                at = min(low.find(w) for w in words)
                first = next(i for i, text in parts if any(w in text.lower() for w in words))
                snippet = joined[max(0, at - 70):at + 110].strip()
                snippet = ('…' if at > 70 else '') + snippet + ('…' if at + 110 < len(joined) else '')
                results.append({'ref': '#'.join([name, *owner]), 'term': name, 'path': str(p),
                                'line': first + 1, 'matches': sum(low.count(w) for w in words),
                                'snippet': snippet})
        results.sort(key=lambda r: (-r['matches'], r['ref']))
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
            content = term_path.read_text(encoding='utf-8')
        except Exception:
            return None
        whole = content
        # A full path only: each segment is a direct child of the element before it (_child), so
        # a name used again elsewhere in the term is never mistaken for the one meant — the old
        # search at any depth silently took the shallowest match, which for the Workspace
        # Manager's `indicator-model` was its test, not the module.
        for n, segment in enumerate(segments):
            child, why = _child(content, segment, root=(n == 0))
            if why:
                return {'name': term_name, 'path': str(term_path), 'ref': ref, 'content': '',
                        'error': f"{why}, in {'#'.join([term_name] + segments[:n])}"}
            if child is None:
                hint = _path_hint(whole, content, segments[n:], term_name, root=(n == 0))
                if hint:
                    return {'name': term_name, 'path': str(term_path), 'ref': ref, 'content': '',
                            'error': f"not found: {ref} — {hint}"}
                return None
            content = child

        return {'name': term_name, 'path': str(term_path), 'ref': ref, 'content': content,
                **self._rules_for(project_path, ref)}

    def expand(self, project_path: str, ref: str) -> dict | None:
        """The children of one element with their descriptions and what they refer to, and the
        rules that apply to it — see @DuckToolsApp#resolver#expand."""
        found = self.resolve_path(project_path, ref)
        if found is None or found.get('error'):
            return found
        fields = _element_fields(found['content'])
        term_map = self._build_term_map(Path(_resolve_project(project_path)).resolve())
        files: dict[str, str] = {}
        out = {'name': found['name'], 'path': found['path'], 'ref': ref,
               'description': _field(fields, 'description', ''), 'fields': [], 'slots': [],
               'strings': [], 'rules': found['rules'], 'codestyle': found['codestyle'], 'goals': found['goals']}
        for key, kind, value in fields:
            if key in ('id', 'description'):
                continue
            if kind == 'scalar':
                out['fields'].append({'key': key, 'value': value})
            elif kind == 'mapping':
                out['fields'].append({'key': key, 'value': ' '.join(l.strip() for l in value if l.strip())})
            elif kind == 'strings':
                if key not in ('guidelines', 'codestyle'):
                    out['strings'].append({'slot': key, 'items': value})
                else:
                    out['strings'].append({'slot': key, 'items': [], 'count': len(value)})
            else:
                entries = []
                for entry in value:
                    text = ' '.join(str(v) for k, _kind, v in entry if _kind == 'scalar' and k != 'id')
                    nested = '\n'.join(str(v) for _k, _kind, v in entry if _kind != 'scalar')
                    refs = []
                    blob = text + nested
                    for m in _PATH_REF.finditer(blob):
                        full = self._full_ref(term_map, m.group(1), m.group(2).strip('#').split('#'), files)
                        refs.append(full or m.group(0))
                    blob = _PATH_REF.sub(' ', blob)
                    for r in _TERM_REF.findall(blob):
                        refs.append('@' + r)
                    for r in _LOCAL_REF.findall(blob):
                        if not r.startswith('decisions#'):
                            refs.append(self._full_ref(term_map, found['name'], r.split('#'), files) or r)
                    refs = list(dict.fromkeys(refs))
                    entries.append({
                        'id': _field(entry, 'id', _field(entry, 'name', '')),
                        'type': _field(entry, 'type', ''),
                        'description': _field(entry, 'description', ''),
                        'counts': {k: len(v) for k, _kind, v in entry if _kind in ('entries', 'strings')},
                        'refers_to': refs[:16],
                    })
                out['slots'].append({'slot': key, 'entries': entries})
        return out

    @staticmethod
    def _full_ref(term_map: dict, term_name: str, segments: list[str], files: dict) -> str | None:
        """The full path, as `@<TermName>#…`, of what a pointer written in prose names — itself
        when it already is one, the one path that fits when it skips levels — or None when it
        names nothing, or more than one thing."""
        if term_name not in term_map:
            return None
        if term_name not in files:
            try:
                files[term_name] = term_map[term_name].read_text(encoding='utf-8')
            except OSError:
                return None
        text = files[term_name]
        fits = _fit_paths(text, text, [s for s in segments if s], term_name, root=True)
        return '@' + fits[0] if len(fits) == 1 else None

    def _owning_project(self, term_path: Path) -> Path:
        """The project file whose `terms_folder` holds the term, at any depth; the term itself
        when none does (a root project file)."""
        for folder in term_path.resolve().parents:
            for candidate in sorted(folder.parent.glob('*.yaml')):
                try:
                    declared = re.search(r'(?m)^terms_folder:\s*(\S+)\s*$', candidate.read_text(encoding='utf-8'))
                except OSError:
                    continue
                if declared and (candidate.parent / declared.group(1).strip('\'"').rstrip('/')
                                 ).resolve() == folder:
                    return candidate
            if folder.parent == folder:
                break
        return term_path

    def _rules_for(self, project_path: str, ref: str) -> dict:
        """Guidelines, codestyle and the part's goals that apply to the element `ref` addresses —
        see @DuckToolsApp#resolver#_rules_for."""
        project_path = _resolve_project(project_path)
        root = Path(project_path).resolve()
        term_name, *segments = ref.split('#')
        term_name = term_name.lstrip('@')
        term_map = self._build_term_map(root)
        term_path = term_map.get(term_name)
        if term_path is None:
            return {'rules': [], 'codestyle': [], 'goals': []}
        content = term_path.read_text(encoding='utf-8')
        rules = [{'source': f'@{term_name}', 'text': r['text']} for r in _own_rules(content, term_name)]
        block, trail = content, [term_name]
        for n, segment in enumerate(segments):
            nxt, _why = _child(block, segment, root=(n == 0))
            if nxt is None:
                break
            block = nxt
            trail.append(segment)
            for g in _field(_element_fields(block), 'guidelines', []) or []:
                if isinstance(g, str):
                    rules.append({'source': '#'.join(trail), 'text': g})
        shown = {r['term'] for r in self._project_wide_rules(root, root.read_text(encoding='utf-8'), term_map)}
        tree = self._rules_tree(term_name, term_map)
        for group in tree['inherited']:
            if group['term'] in shown:
                continue
            rules += [{'source': f"@{group['term']} (inherited)", 'text': r['text']} for r in group['rules']]
        codestyle = []
        chain = {a for a, _ in self._extends_chain_from(term_name, content, term_map)}
        if 'Software' in chain and not any(s in _SOFTWARE_NON_CODE_SLOTS for s in segments):
            codestyle += [{'source': f'@{term_name}', 'text': c}
                          for c in _field(_element_fields(content), 'codestyle', []) or []]
            project = self._owning_project(term_path)
            if project.resolve() != term_path.resolve():
                pc = project.read_text(encoding='utf-8')
                codestyle += [{'source': f'@{project.stem}', 'text': c}
                              for c in _field(_element_fields(pc), 'codestyle', []) or []]
        # the goals of the part an element belongs to: what decides between two ways of doing the
        # work is opened with it; the part itself carries them in its own content already
        goals = []
        if 'Software' in chain and segments:
            goals = [{'source': f'@{term_name}', 'text': g}
                     for g in _field(_element_fields(content), 'goals', []) or []]
        return {'rules': rules, 'codestyle': codestyle, 'goals': goals}

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
            for f in _term_files(folder):
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
                    content = term_map[current].read_text(encoding='utf-8')
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
                    cache[name] = term_map[name].read_text(encoding='utf-8')
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
                for i in _stray_text(content.splitlines(keepends=True)):
                    text = content.splitlines()[i].strip()
                    shown = text if len(text) <= 60 else text[:57] + '...'
                    add('error', 'stray-text', name, path,
                        f"'{shown}' belongs to no field — it follows a quoted value that has "
                        f"already closed, so no YAML parser can read the file. Usually what an edit "
                        f"left of an older value: fold it into the value it belongs to, or delete it", i + 1)
                for i, _prefix, value in _unquoted_scalars(content.splitlines(keepends=True)):
                    shown = value if len(value) <= 60 else value[:57] + '...'
                    add('error', 'unquoted-scalar', name, path,
                        f"'{shown}' is written bare, and YAML would not read it as text — quote "
                        f"it, or run normalize on the project", i + 1)
                # A full path names one element only while no two siblings share an id.
                paths = _element_paths(content)
                for i, dup in _duplicate_ids(content):
                    add('error', 'duplicate-id', name, path,
                        f"'{dup}' is the id of another entry of the same list too, so "
                        f"{'#'.join([name] + paths[i])} names two elements — give one of them "
                        f"another id", i + 1)
                # A choice, a ruling or evidence written inline belongs in a @Decision.
                keys: list[tuple[int, str]] = []   # the keys enclosing each line, by indent
                for i, line in enumerate(content.splitlines()):
                    if line.strip():
                        ind = len(line) - len(line.lstrip())
                        while keys and keys[-1][0] >= ind:
                            keys.pop()
                        km = re.match(r'^\s*(?:-\s+)?([\w-]+):', line)
                        if km:
                            keys.append((ind, km.group(1)))
                    if any(k == 'decisions' for _, k in keys):
                        continue   # a decision is where dates and rulings belong
                    dm = re.match(r'^\s*(?:-\s+)?description:\s*(.*)$', line)
                    if dm and (_DATE.search(dm.group(1)) or '(the author' in dm.group(1).lower()):
                        what = 'a date' if _DATE.search(dm.group(1)) else 'an attribution, "(the author"'
                        add('warning', 'dated-prose', name, path,
                            f"this description holds {what}: a choice, a ruling or evidence written "
                            f"inline — move it to a @Decision of the element (`by`, `date`, `rejected`)", i + 1)
                # The project's own workflow is one of its repository too, not one it inherits from
                # another project — a clone of this repository alone would have none.
                wm = re.search(r'(?m)^workflow:\s*\n((?:[ \t]+.*\n?)*)', content)
                tm = re.search(r'(?m)^\s+type:\s*["\']?@(\w+)', wm.group(1)) if wm else None
                if tm and tm.group(1) in term_map:
                    wf_path = term_map[tm.group(1)].resolve()
                    fw = term_map.get('Duckspec')
                    if not (fw is not None and wf_path.is_relative_to(fw.resolve().parent)) \
                            and _git_root(wf_path) != _git_root(Path(path)):
                        add('error', 'foreign-workflow-ref', name, path,
                            f"the project's workflow @{tm.group(1)} is a term of another repository: "
                            f"a workflow is self-contained within its own (see @DuckWorkflow) — give "
                            f"this project a workflow of its own")
                # A workflow is self-contained within its repository (see @DuckWorkflow).
                chain_names = {a for a, _ in self._extends_chain_from(name, content, term_map)}
                if 'DuckWorkflow' in chain_names and name != 'DuckWorkflow':
                    here = _git_root(Path(path))
                    fw = term_map.get('Duckspec')
                    fw_dir = fw.resolve().parent if fw else None
                    for i, line in enumerate(content.splitlines()):
                        for ref in _TERM_REF.findall(line):
                            target = term_map.get(ref)
                            if target is None:
                                continue
                            target = target.resolve()
                            if fw_dir is not None and target.is_relative_to(fw_dir):
                                continue
                            if _git_root(target) != here:
                                add('error', 'foreign-workflow-ref', name, path,
                                    f"@{ref} is a term of another repository: a workflow is "
                                    f"self-contained within its own (see @DuckWorkflow) — describe "
                                    f"that project in words and list it in `references`", i + 1)

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
                                                       term_map[target].read_text(encoding='utf-8'), term_map):
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
        return _with_elements(findings)

    def _source_root(self, term_path: Path) -> Path:
        """The directory a term's `src:` values resolve against: the `settings.src` (default `..`)
        of the project whose `terms_folder:` names the folder the term sits in. Matched on that
        declaration, not on the folder's name, which need not match the project's. A term no
        project claims — a root project file itself — is read as its own project."""
        project = self._owning_project(term_path)
        try:
            content = project.read_text(encoding='utf-8')
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
        cache = {name: path.read_text(encoding='utf-8') for name, path in term_map.items()}
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
                            text = source_file.read_text(encoding='utf-8')
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
        return _with_elements(findings)

    def term_uses(self, project_path: str, term_name: str) -> dict:
        """Reverse index: the terms that extend this one, and the elements — by full path — that
        name it as their `type:` or mention it."""
        project_path = _resolve_project(project_path)
        term_map = self._build_term_map(Path(project_path).resolve())
        out = {'extended_by': [], 'referenced_by': [], 'typed_by': []}
        typed = re.compile(rf'^\s*(?:-\s+)?type:\s*["\']?@{re.escape(term_name)}\b')
        for name, path in sorted(term_map.items()):
            if name == term_name:
                continue
            try:
                content = path.read_text(encoding='utf-8')
            except Exception:
                continue
            if _parse_extends(content) == term_name:
                out['extended_by'].append(name)
            owners = _element_paths(content)
            for i, line in enumerate(content.splitlines()):
                where = '#'.join([name, *owners[i]])
                if typed.match(line):
                    if where not in out['typed_by']:
                        out['typed_by'].append(where)
                elif re.match(r'^\s*extends:', line):
                    continue
                elif term_name in _TERM_REF.findall(line) and where not in out['referenced_by']:
                    out['referenced_by'].append(where)
        out['referenced_by'] = [r for r in out['referenced_by'] if r not in out['typed_by']]
        return out

    def term_schema(self, project_path: str, term_name: str) -> list[dict]:
        """Every member the term effectively has, tagged with the ancestor that declared it."""
        project_path = _resolve_project(project_path)
        term_map = self._build_term_map(Path(project_path).resolve())
        if term_name not in term_map:
            return []
        chain = self._extends_chain_from(term_name, term_map[term_name].read_text(encoding='utf-8'), term_map)
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
                content = path.read_text(encoding='utf-8')
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
        content = term_map[term_name].read_text(encoding='utf-8')
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
        lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
        start, end = 0, len(lines)
        for n, segment in enumerate(segments):
            window = ''.join(lines[start:end])
            child, why = _child(window, segment, root=(n == 0))
            if why:
                return f"{why}, in {'#'.join([term_name] + segments[:n])}"
            if child is None:
                hint = _path_hint(''.join(lines), window, segments[n:], term_name, root=(n == 0))
                return f"not found: '{segment}' in {ref}" + (f" — {hint}" if hint else '')
            # Found by its whole run of lines, not its first line alone: two list items can open
            # with the same line, and a digits-only segment picks one of them by position.
            wanted = [l.rstrip() for l in child.splitlines()]
            have = [l.rstrip() for l in lines[start:end]]
            offset = next((i for i in range(len(have)) if have[i:i + len(wanted)] == wanted), None)
            if offset is None:
                return f"not found: '{segment}' in {ref}"
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
            old = head.split(':', 1)[1].strip()
            close = _quote_close(lines, start, old) if old[:1] in ('"', "'") else None
            return start, (close if close is not None and close < end else start) + 1, column
        pattern = re.compile(rf'^{" " * column}{re.escape(field)}:')
        for i in range(start, end):
            if pattern.match(lines[i]):
                old = lines[i].split(':', 1)[1].strip()
                # A quoted value runs to its closing quote, paragraphs and all; stopping at its
                # first blank line stranded the rest of it after the new value.
                if old[:1] in ('"', "'"):
                    close = _quote_close(lines, i, old)
                    if close is not None and close < end:
                        return i, close + 1, column
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
            path.write_text(''.join(lines), encoding='utf-8', newline='')
            return f'{path}: added {field}'
        i, j, column = span
        on_head = re.match(rf'^(\s*-\s+){re.escape(field)}:', lines[i])
        # A value continued on the lines below belongs to this field too, and replacing only the
        # first line of it would leave the rest stranded under the new value.
        lines[i:j] = [f'{on_head.group(1) if on_head else " " * column}{field}: {value}\n']
        path.write_text(''.join(lines), encoding='utf-8', newline='')
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

    def _entry_not_slot(self, project_path: str, ref: str, parent_ref: str,
                        slot_name: str) -> str | None:
        """Why a missing slot must not be created, or None. A full path's last segment that is
        not a field of the element before it is created there as a new slot — unless an entry of
        that name lies below the element: then the path most likely skipped a level, and creating
        the slot would put a second, empty thing under that name instead of reaching the entry.
        A slot the term's type declares is always created; a same-named key further down — a
        nested component's own `decisions:` — is another element's slot and no rival."""
        if '#' not in parent_ref:
            declared = {m['member'] for m in self.term_schema(project_path, parent_ref.lstrip('@'))}
            if slot_name in declared:
                return None
        parent = self._locate(project_path, parent_ref)
        if isinstance(parent, str):
            return None
        path, lines, start, end = parent
        window = ''.join(lines[start:end])
        entries = [b for b in _all_named(window, slot_name) if b.lstrip().startswith('- ')]
        if not entries:
            return None
        term_name = parent_ref.split('#')[0].lstrip('@')
        paths = _candidate_paths(''.join(lines), window, entries, term_name)
        return (f"refused: '{slot_name}' is not a field of {parent_ref}, and creating it there "
                f"would add a new slot beside the entries already called '{slot_name}' below — "
                f"a path names every level: " + ', '.join(paths))

    def _own_slot(self, project_path: str, ref: str):
        """The slot `ref` names, looked for only among the fields of the element the rest of the
        path addresses — never in an element nested below it, whose slot of the same name (a
        nested component's `decisions:`) is not the one meant. Falls back to _locate when the
        parent cannot be located or the last segment is a position."""
        parent_ref, _, slot_name = ref.rpartition('#')
        if not parent_ref or slot_name.isdigit():
            return self._locate(project_path, ref, slot=True)
        parent = self._locate(project_path, parent_ref)
        if isinstance(parent, str):
            return self._locate(project_path, ref, slot=True)
        path, lines, p_start, p_end = parent
        if '#' not in parent_ref:
            own_col, p_start, p_end = 0, 0, len(lines)
        else:
            head = lines[p_start]
            if not head.lstrip().startswith('- '):
                return self._locate(project_path, ref, slot=True)
            own_col = len(head) - len(head.lstrip()) + 2
        header = re.compile(rf'^ {{{own_col}}}{re.escape(slot_name)}:\s*$')
        for i in range(p_start, p_end):
            if header.match(lines[i].rstrip('\n')):
                end = len(lines)
                for j in range(i + 1, len(lines)):
                    if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= own_col:
                        end = j
                        break
                return path, lines, i, end
        return f"not found: '{slot_name}' in {ref}"

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
        located = self._own_slot(project_path, ref)
        if isinstance(located, str):
            if anchor is not None and located.startswith('not found'):
                return f"refused: '{anchor}' is not an entry of {ref}, which does not exist yet"
            parent_ref, _, slot_name = ref.rpartition('#')
            if not parent_ref or not located.startswith('not found') or slot_name.isdigit():
                return located
            parent = self._locate(project_path, parent_ref)
            if isinstance(parent, str):
                return located
            refused = self._entry_not_slot(project_path, ref, parent_ref, slot_name)
            if refused:
                return refused
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
            path.write_text(''.join(lines), encoding='utf-8', newline='')
            located = self._own_slot(project_path, ref)
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

        # only the slot's own entries: an argument or nested entry deeper down carrying the same id
        # belongs to one of them and is reached by a different #-path
        for line in existing:
            if len(line) - len(line.lstrip()) != column:
                continue
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
        path.write_text(''.join(lines), encoding='utf-8', newline='')
        return f'{path}: added {entry_id} to {ref}'

    def remove_element(self, project_path: str, ref: str) -> str:
        if '#' not in ref:
            return 'refused: a bare term name removes a whole file, which this does not do'
        located = self._locate(project_path, ref)
        if isinstance(located, str):
            return located
        path, lines, start, end = located
        del lines[start:end]
        path.write_text(''.join(lines), encoding='utf-8', newline='')
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
            if not located.startswith('not found') or block.isdigit():
                return located
            refused = self._entry_not_slot(project_path, ref, head, block)
            if refused:
                return refused
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
            path.write_text("".join(lines), encoding='utf-8', newline='')
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
        path.write_text("".join(lines), encoding='utf-8', newline='')
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
        path.write_text("".join(lines), encoding='utf-8', newline='')
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
        path.write_text("".join(lines), encoding='utf-8', newline='')
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
        m = _TERMS_FOLDER.search(root.read_text(encoding='utf-8'))
        if not m:
            return 'project declares no terms_folder'
        folder = root.parent / m.group(1)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{term_name}.yaml'
        if path.exists():
            return f'already exists: {path}'
        path.write_text(f'description: {_quoted(description)}\n'
                        f'extends: {_quoted("@" + extends.lstrip("@"))}\n',
                        encoding='utf-8', newline='')
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

    _BATCH_OPS = {
        'set_field': 'set_field', 'set': 'set_field', 'edit_field': 'edit_field', 'edit': 'edit_field',
        'add_entry': 'add_entry', 'add': 'add_entry', 'remove_element': 'remove_element', 'remove': 'remove_element',
        'add_item': 'add_item', 'add-item': 'add_item', 'set_item': 'set_item', 'set-item': 'set_item',
        'remove_item': 'remove_item', 'remove-item': 'remove_item', 'move_item': 'move_item', 'move-item': 'move_item',
        'create_term': 'create_term', 'create-term': 'create_term', 'remove_term': 'remove_term',
        'remove-term': 'remove_term', 'rename_term': 'rename_term', 'rename-term': 'rename_term',
    }
    _REFUSALS = ('refused', 'not found', 'unknown term', 'ambiguous', 'error')

    def batch(self, project_path: str, operations: list[dict]) -> dict:
        """Several edits as one, all or nothing — see @DuckToolsApp#resolver#batch."""
        project_path = _resolve_project(project_path)
        root = Path(project_path).resolve()
        folders, _ = self._collect(root)
        def snapshot() -> dict[Path, str]:
            files = {root} | set(self._build_term_map(root).values())
            for folder in folders:
                files |= set(_term_files(folder))
            return {f.resolve(): f.read_text(encoding='utf-8') for f in files if f.is_file()}
        before = snapshot()
        results: list[str] = []
        for n, op in enumerate(operations):
            args = dict(op)
            name = self._BATCH_OPS.get(str(args.pop('op', '')))
            try:
                if name is None:
                    raise ValueError(f"unknown operation {op.get('op')!r}")
                out = getattr(self, name)(project_path, **args)
                out = '' if out is None else str(out)
            except Exception as e:   # a bad argument is a refusal like any other
                out = f'refused: {type(e).__name__}: {e}'
            if out.startswith(self._REFUSALS):
                after = snapshot()
                for f in set(after) - set(before):
                    f.unlink()
                for f, content in before.items():
                    if after.get(f) != content:
                        f.write_text(content, encoding='utf-8', newline='')
                results.append(out)
                return {'ok': False, 'results': results, 'failed': n}
            results.append(out)
        return {'ok': True, 'results': results, 'failed': None}

    def normalize(self, project_path: str) -> list[dict]:
        """Rewrites every term file of the project so each value YAML would misread is quoted.
        Returns [{path, quoted}] for the files it changed."""
        root = Path(_resolve_project(project_path)).resolve()
        changed = []
        for path in self._own_files(root):
            try:
                text = path.read_text(encoding='utf-8')
            except Exception:
                continue
            new, count = self._normalized(text)
            if count:
                path.write_text(new, encoding='utf-8', newline='')
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
        path.write_text(new, encoding='utf-8', newline='')
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
                content = path.read_text(encoding='utf-8')
            except Exception:
                continue
            updated = re.sub(rf'@{re.escape(old_name)}\b', f'@{new_name}', content)
            if updated != content:
                path.write_text(updated, encoding='utf-8', newline='')
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
        _register_framework(name)

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
                    content = Path(path).read_text(encoding='utf-8')
                    name = Path(path).stem
                    description = _description(content)
                except Exception:
                    pass
                projects.append({'repository': repository, 'path': path, 'name': name, 'description': description})
            workspaces[wname] = {'projects': projects}
        return {'active_workspace': settings.get('active_workspace'), 'workspaces': workspaces}

    def add_project(self, project_path: str, workspace_name: str | None = None) -> str | None:
        """Registers project_path under its own `repository` in workspace_name (or the active
        workspace) — or under its file's file:// URI while it has none, so a project is registered
        from the moment it is created. Any other key pointing at the same file is dropped: that is
        the file:// entry left from before the repository was set. Returns the key used, or None
        if no workspace resolves."""
        path = Path(project_path).resolve()
        try:
            content = path.read_text(encoding='utf-8')
        except Exception:
            content = ''
        m = _REPOSITORY.search(content)
        key = m.group(1) if m else path.as_uri()

        settings = _load_settings()
        name = workspace_name or settings.get('active_workspace')
        if name is None:
            return None
        projects = settings['workspaces'].setdefault(name, {'projects': {}}).setdefault('projects', {})
        for stale in [k for k, p in projects.items() if k != key and Path(p).resolve() == path]:
            del projects[stale]
        projects[key] = str(path)
        _save_settings(settings)
        return key

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
