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
_RECIPES_BLOCK = re.compile(r'^recipes:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_GUIDELINES_BLOCK = re.compile(r'^guidelines:\n((?:[ \t].+\n?)*)', re.MULTILINE)
_AI_INSTRUCTIONS_BLOCK = re.compile(r'^ai_instructions:\n((?:[ \t].+\n?)*)', re.MULTILINE)

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


def _parse_recipes(content: str) -> list[dict]:
    m = _RECIPES_BLOCK.search(content)
    if not m:
        return []
    block = m.group(0)
    results = []
    for item in re.split(r'\n(?=  - )', block)[1:]:
        name_m = re.search(r'^\s*-\s+name:\s*(.+)$', item, re.MULTILINE)
        desc_m = re.search(r'^[ \t]+description:\s*(.+)$', item, re.MULTILINE)
        if name_m:
            results.append({
                'name': name_m.group(1).strip(),
                'description': desc_m.group(1).strip() if desc_m else '',
            })
    return results


def _extract_named_block(content: str, name: str) -> str | None:
    lines = content.splitlines()
    target = re.compile(r'^-\s+name:\s*[\'"]?' + re.escape(name) + r'[\'"]?\s*$')
    start = None
    item_indent = None
    for i, line in enumerate(lines):
        if target.match(line.strip()):
            start = i
            item_indent = len(line) - len(line.lstrip())
            break
    if start is None:
        return None

    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= item_indent:
            end = j
            break
    return '\n'.join(lines[start:end]).rstrip()


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


def _resolve_entry(sub: str, base: Path, workspace: dict[str, Path]) -> Path | None:
    if sub.startswith('https://') or sub.startswith('http://'):
        path = workspace.get(sub)
        if path is None:
            print(f'warning: no workspace entry for "{sub}" — skipping')
        return path
    return base / sub


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
        term_map: dict[str, Path] = {}
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
        """Returns root file content + term list + aggregated recipe and rules lists."""
        root = Path(project_path).resolve()
        root_content = root.read_text()

        recipes = [{'term': root.stem, **r} for r in _parse_recipes(root_content)]
        rules = (
            [{'term': root.stem, 'type': 'Guideline', 'text': g}
             for g in _parse_list_block(root_content, _GUIDELINES_BLOCK)] +
            [{'term': root.stem, 'type': 'AI Instruction', 'text': a}
             for a in _parse_list_block(root_content, _AI_INSTRUCTIONS_BLOCK)]
        )
        terms = []

        for name, p, content in self._walk_terms(project_path):
            if p == root:
                continue
            m = _DESCRIPTION.search(content)
            terms.append({'name': name, 'path': str(p), 'description': m.group(1).strip() if m else ''})
            recipes += [{'term': name, **r} for r in _parse_recipes(content)]
            rules += [{'term': name, 'type': 'Guideline', 'text': g}
                      for g in _parse_list_block(content, _GUIDELINES_BLOCK)]
            rules += [{'term': name, 'type': 'AI Instruction', 'text': a}
                      for a in _parse_list_block(content, _AI_INSTRUCTIONS_BLOCK)]

        return {
            'root_content': root_content,
            'terms': terms,  # already sorted by _walk_terms
            'recipes': recipes,
            'rules': rules,
        }

    def list_terms(self, project_path: str, include_all: bool = False) -> list[dict]:
        result = []
        for name, p, content in self._walk_terms(project_path, include_all):
            m = _DESCRIPTION.search(content)
            result.append({'name': name, 'path': str(p), 'description': m.group(1).strip() if m else ''})
        return result

    def load_terms(self, project_path: str, term_names: list[str]) -> list[dict]:
        path = Path(project_path).resolve()
        term_map = self._build_term_map(path)
        seeds = [term_map[n] for n in term_names if n in term_map]
        reachable = self._reachable_from(seeds, term_map)
        for n in term_names:
            if n in term_map and n not in reachable:
                reachable[n] = term_map[n]
        result = []
        for name, p in sorted(reachable.items()):
            try:
                content = p.read_text()
            except Exception:
                content = ''
            result.append({'name': name, 'path': str(p), 'content': content})
        return result

    def grep_terms(self, project_path: str, query: str, include_all: bool = False) -> list[dict]:
        q = query.lower()
        results = []
        for name, p, content in self._walk_terms(project_path, include_all):
            matching = [line.strip() for line in content.splitlines() if q in line.lower()]
            if matching:
                results.append({'name': name, 'path': str(p), 'lines': matching})
        return results

    def resolve_path(self, project_path: str, ref: str) -> dict | None:
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
            block = _extract_named_block(content, segment)
            if block is None:
                return None
            content = block

        return {'name': term_name, 'path': str(term_path), 'ref': ref, 'content': content}

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

    def list_workspaces(self) -> dict:
        settings = _load_settings()
        return {'active_workspace': settings.get('active_workspace'), 'workspaces': settings.get('workspaces', {})}

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
