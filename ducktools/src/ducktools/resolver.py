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
_EXTENDS = re.compile(r'^extends:\s*@?(\S+)', re.MULTILINE)
_RECIPES_BLOCK = re.compile(r'^recipes:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_REFERENCES_BLOCK = re.compile(r'^references:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_GUIDELINES_BLOCK = re.compile(r'^guidelines:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_AI_INSTRUCTIONS_BLOCK = re.compile(r'^ai_instructions:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_PROPERTIES_BLOCK = re.compile(r'^properties:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_PATH_REF = re.compile(r'@([A-Z][A-Za-z]+)((?:#[A-Za-z_][\w-]*)+)')
_SLOT_DECL = re.compile(r'^\s*-\s+(?:id|name):\s*(\w+)\s*\n\s+type:\s*@(\w+)\s*$', re.MULTILINE)

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


def _narrow(content: str, segment: str) -> str | None:
    """Resolves one `#`-path segment. Named list items win over mapping keys of the same name —
    that ordering is what keeps a component called `settings` addressable in a term that also
    carries a `settings:` key, and preserves the behaviour from before keys were addressable."""
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
    return {slot: element for slot, element in _SLOT_DECL.findall(m.group(0))}


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

    def _reachable_from(self, seeds: list[Path], term_map: dict[str, Path]) -> dict[str, Path]:
        """BFS from seed files; follows @TermName references."""
        reachable: dict[str, Path] = {}
        visited: set[Path] = set()
        queue = list(seeds)
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
            for name in _TERM_REF.findall(content):
                if name in term_map and name not in reachable:
                    reachable[name] = term_map[name]
                    queue.append(term_map[name])
        return reachable

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

        for name, p, content in self._walk_terms(project_path):
            if p.resolve() == root:
                continue
            m = _DESCRIPTION.search(content)
            terms.append({'name': name, 'path': str(p), 'description': m.group(1).strip() if m else ''})
            recipes += [{'term': name, **r} for r in _parse_recipes(content)]
            references += [{'term': name, **r} for r in _parse_references(content)]

        return {
            'root_content': root_content,
            'terms': terms,  # already sorted by _walk_terms
            'recipes': recipes,
            'references': references,
            'rules': rules,
        }

    def list_terms(self, project_path: str, include_all: bool = False) -> list[dict]:
        project_path = _resolve_project(project_path)
        result = []
        for name, p, content in self._walk_terms(project_path, include_all):
            m = _DESCRIPTION.search(content)
            result.append({'name': name, 'path': str(p), 'description': m.group(1).strip() if m else ''})
        return result

    def load_terms(self, project_path: str, term_names: list[str]) -> list[dict]:
        """Loads the named terms and their transitive @TermName dependencies. Only the
        explicitly requested terms (not every dependency pulled in for context) get a `rules`
        tree attached — own guidelines/ai_instructions plus each `extends` ancestor's own,
        kept separate so scope stays visible instead of flattened into one list."""
        project_path = _resolve_project(project_path)
        path = Path(project_path).resolve()
        term_map = self._build_term_map(path)
        seeds = [term_map[n] for n in term_names if n in term_map]
        reachable = self._reachable_from(seeds, term_map)
        for n in term_names:
            if n in term_map and n not in reachable:
                reachable[n] = term_map[n]
        requested = {n for n in term_names if n in term_map}
        result = []
        for name, p in sorted(reachable.items()):
            try:
                content = p.read_text()
            except Exception:
                content = ''
            entry = {'name': name, 'path': str(p), 'content': content}
            if name in requested:
                entry['rules'] = self._rules_tree(name, term_map)
            result.append(entry)
        return result

    def grep_terms(self, project_path: str, query: str, include_all: bool = False) -> list[dict]:
        project_path = _resolve_project(project_path)
        q = query.lower()
        results = []
        for name, p, content in self._walk_terms(project_path, include_all):
            matching = [line.strip() for line in content.splitlines() if q in line.lower()]
            if matching:
                results.append({'name': name, 'path': str(p), 'lines': matching})
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
            block = _narrow(content, segment)
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

    def verify_project(self, project_path: str, unreachable: bool = False) -> list[dict]:
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

        for name, path, content in self._walk_terms(project_path, include_all=True):
            cache[name] = content
            for slot, element in _slot_element_types(content).items():
                slot_types.setdefault(slot, element)
            own_members[name] = _member_names(content, _PROPERTIES_BLOCK)

            if not _DESCRIPTION.search(content):
                add('error', 'unparsed-term', name, path, 'no top-level description:')
                continue

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
            for slot, element in slot_types.items():
                allowed = self._schema(element, term_map, cache)
                if not allowed:
                    continue
                for item_id, item, indent in _slot_items(content, slot):
                    column = ' ' * (indent + 2)
                    own = re.search(rf'^{column}type:\s*@(\w+)', item, re.MULTILINE)
                    fields = allowed | (self._schema(own.group(1), term_map, cache) if own else set())
                    for key in re.findall(rf'^{column}([a-z_]+):', item, re.MULTILINE):
                        if key not in fields:
                            add('error', 'unknown-field', name, path,
                                f"{slot} entry '{item_id}' sets '{key}', which no type it has "
                                f"declares (@{element}"
                                + (f" + @{own.group(1)}" if own else "") + ")")

        if unreachable:
            for name, path in sorted(term_map.items()):
                if name not in reachable and path.resolve() != root:
                    add('warning', 'unreachable-term', name, path,
                        'no reachable term mentions this term')

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
            if re.search(rf'^\s*type:\s*@{re.escape(term_name)}\b', content, re.MULTILINE):
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

    def _locate(self, project_path: str, ref: str) -> tuple[Path, list[str], int, int] | str:
        """(file, lines, start, end) for the element `ref` addresses, or an error string. Narrows
        segment by segment like resolve_path, but tracking line numbers, and refuses an ambiguous
        step instead of taking the first match."""
        term_name, *segments = ref.split('#')
        term_name = term_name.lstrip('@')
        term_map = self._build_term_map(Path(_resolve_project(project_path)).resolve())
        if term_name not in term_map:
            return f'unknown term: @{term_name}'
        path = term_map[term_name]
        lines = path.read_text().splitlines(keepends=True)
        start, end = 0, len(lines)
        for segment in segments:
            window = ''.join(lines[start:end])
            if len(_find_named_blocks(window, segment)) > 1:
                return f"ambiguous: '{segment}' matches more than one element in {ref}"
            block = _narrow(window, segment)
            if block is None:
                return f"not found: '{segment}' in {ref}"
            first = block.splitlines()[0]
            offset = next(i for i, l in enumerate(lines[start:end]) if l.rstrip('\n') == first)
            start += offset
            end = start + len(block.splitlines())
        return path, lines, start, end

    def set_field(self, project_path: str, ref: str, field: str, value: str) -> str:
        located = self._locate(project_path, ref)
        if isinstance(located, str):
            return located
        path, lines, start, end = located
        head = lines[start]
        indent = len(head) - len(head.lstrip())
        if head.lstrip().startswith('- ') or re.match(r'^\s*[\w-]+:\s*$', head):
            column = indent + 2   # a list item or a key opening a block nests its fields
        else:
            column = indent       # the term itself: fields sit at the top level
        pattern = re.compile(rf'^{" " * column}{re.escape(field)}:')
        for i in range(start, end):
            if pattern.match(lines[i]):
                lines[i] = f'{" " * column}{field}: {value}\n'
                path.write_text(''.join(lines))
                return f'{path}: replaced {field}'
        lines.insert(start + 1, f'{" " * column}{field}: {value}\n')
        path.write_text(''.join(lines))
        return f'{path}: added {field}'

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
