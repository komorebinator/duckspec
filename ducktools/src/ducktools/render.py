"""Text both front ends print for the navigation operations — load_project, expand and
resolve_path — written once, so the CLI and the MCP server cannot drift apart."""


def _cell(text) -> str:
    return str(text or '').replace('\n', ' ').replace('|', '\\|')


def _refs(names: list[str]) -> str:
    return ', '.join('@' + n for n in names)


def _terms_table(rows: list[dict]) -> str:
    out = ['| Term | Description | Refers to | Referred to by |',
           '|------|-------------|-----------|----------------|']
    out += [f"| @{r['name']} | {_cell(r['description'])} | {_refs(r['refers_to'])} | {_refs(r['referred_by'])} |"
            for r in rows]
    return '\n'.join(out)


def load_project(result: dict) -> str:
    parts = [result['root_content'].rstrip(), '## Workflow', result['workflow']['note']]
    if result.get('tasks'):
        parts += ['## Tasks', result['tasks']['note']]
    parts += ['## Parts',
              "The project's @Software, each with its description in full. Go further with "
              "`expand <TermName>`, or `expand <TermName>#<path>` for an element inside it; "
              "`resolve-path` opens one element whole.",
              _terms_table(result['parts']) if result['parts'] else '(none listed in `software:`)']
    if result['terms']:
        parts += ['## Other terms of this project', _terms_table(result['terms'])]
    if result['projects']:
        rows = ['| Project | Repository | Connection | Description |',
                '|---------|------------|------------|-------------|']
        rows += [f"| @{p['name']} | {p['repository']} | {_cell(p['connection'])} | {_cell(p['description'])} |"
                 for p in result['projects']]
        parts += ['## Connected projects',
                  'Other projects, one node each; their content is not loaded.', '\n'.join(rows)]
    rows = ['| Recipe | Term | Description |', '|--------|------|-------------|']
    rows += [f"| {r['name']} | @{r['term']} | {_cell(r.get('description', ''))} |" for r in result['recipes']]
    parts += ['## Recipes', '\n'.join(rows)]
    if result['vocabulary']:
        rows = ['| Term | Description |', '|------|-------------|']
        rows += [f"| @{v['name']} | {_cell(v['description'])} |" for v in result['vocabulary']]
        parts += ['## Vocabulary from @Duckspec', '\n'.join(rows)]
    rows = ['| Source | Rule |', '|--------|------|']
    rows += [f"| @{r['term']} | {_cell(r['text'])} |" for r in result['rules']]
    parts += ['## Rules (project-wide)', '\n'.join(rows)]
    return '\n\n'.join(parts) + '\n'


def _rules(result: dict) -> str:
    out = ['## Rules that apply here', '']
    if result.get('rules'):
        out += [f"- [{r['source']}] {r['text']}" for r in result['rules']]
    else:
        out.append('(none beyond the project-wide rules load-project shows)')
    if result.get('codestyle'):
        out += ['', '## Codestyle', '']
        out += [f"- [{r['source']}] {r['text']}" for r in result['codestyle']]
    if result.get('goals'):
        out += ['', '## Goals of the part', '']
        out += [f"- [{r['source']}] {r['text']}" for r in result['goals']]
    return '\n'.join(out)


def resolve_path(result: dict | None, ref: str) -> str:
    if result is None:
        return f'not found: {ref}\n'
    if result.get('error'):
        return result['error'] + '\n'
    return f"--- {ref} [{result['path']}] ---\n{result['content']}\n\n{_rules(result)}\n"


def expand(result: dict | None, ref: str) -> str:
    if result is None:
        return f'not found: {ref}\n'
    if result.get('error'):
        return result['error'] + '\n'
    out = [f"--- expand {ref} [{result['path']}] ---"]
    if result['description']:
        out += ['', result['description']]
    if result['fields']:
        out += [''] + [f"{f['key']}: {f['value']}" for f in result['fields']]
    for s in result['strings']:
        if 'count' in s:
            out += ['', f"## {s['slot']} ({s['count']}) — under the sections below"]
        else:
            out += ['', f"## {s['slot']} ({len(s['items'])})", ''] + [f'- {_cell(i)}' for i in s['items']]
    for s in result['slots']:
        out += ['', f"## {s['slot']} ({len(s['entries'])}) — each opens as {s.get('opens', result['ref'] + '#' + s['slot'])}#<id>", '']
        for e in s['entries']:
            line = f"- **{e['id']}**" + (f" [{e['type']}]" if e['type'] else '') + f" — {_cell(e['description'])}"
            if e['counts']:
                line += '\n  contains: ' + ', '.join(f'{k} {n}' for k, n in e['counts'].items())
            if e['refers_to']:
                line += '\n  refers to: ' + ', '.join(e['refers_to'])
            out.append(line)
    out += ['', _rules(result)]
    return '\n'.join(out) + '\n'


def search(results: list[dict], limit: int = 30) -> str:
    """The elements a word search found, one row each, with the full path that opens it."""
    if not results:
        return 'no element holds every word of the query'
    rows = ['| Element | Matches | Context |', '|---------|---------|---------|']
    rows += [f"| @{r['ref']} | {r['matches']} | {_cell(r['snippet'])} |" for r in results[:limit]]
    if len(results) > limit:
        rows.append(f"\n{len(results) - limit} more element(s) — add a word to narrow the query, or raise the limit")
    return '\n'.join(rows)


def findings(found: list[dict]) -> str:
    """verify_project's and verify_source's findings, each with the element it sits in."""
    if not found:
        return 'no findings'
    rows = ['| Severity | Check | Element | Location | Message |',
            '|----------|-------|---------|----------|---------|']
    for f in found:
        location = f"{f['path']}:{f['line']}" if 'line' in f else f['path']
        # a task is not a term, so it is not named as one
        element = f"task {f['term']}" if f['check'] == 'broken-task' else f"@{f.get('ref', f['term'])}"
        rows.append(f"| {f['severity']} | {f['check']} | {element} | {location} | {f['message']} |")
    errors = sum(1 for f in found if f['severity'] == 'error')
    rows.append(f"\n{errors} error(s), {len(found) - errors} warning(s)")
    return '\n'.join(rows)


def tasks(result) -> str:
    """list_tasks as a table, or the refusal it returned."""
    if isinstance(result, str):
        return result
    if not result:
        return 'no open tasks'
    out = ['| Task | Kind | Status | Title | Labels | Blocked by | Details |',
           '|------|------|--------|-------|--------|------------|---------|']
    for t in result:
        title = _cell(t['title']) + (' — **broken:** ' + _cell('; '.join(t['problems'])) if t['problems'] else '')
        out.append(f"| {t['id']} | {t['kind']} | {t['status']} | {title} | {', '.join(t['labels'])} | "
                   f"{', '.join(t['blocked_by'])} | {t['details'] or ''} |")
    return '\n'.join(out)


def task(result) -> str:
    """read_task: the task file under its path, or the refusal."""
    if isinstance(result, str):
        return result
    return f"--- task {result['id']} [{result['path']}] ---\n{result['content'].rstrip()}"
