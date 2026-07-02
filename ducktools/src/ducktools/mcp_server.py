import json
import sys

from .resolver import resolver

_PROJECT_PATH_PROP = {
    'project_path': {
        'type': 'string',
        'description': 'path to a DuckspecProject .yaml file',
    },
}

_TOOLS = [
    {
        'name': 'load_project',
        'description': 'Load the root project file and list all reachable terms with descriptions. Call this first when starting work on a project.',
        'inputSchema': {
            'type': 'object',
            'properties': _PROJECT_PATH_PROP,
            'required': ['project_path'],
        },
    },
    {
        'name': 'list_terms',
        'description': 'List reachable term names, file paths, and descriptions',
        'inputSchema': {
            'type': 'object',
            'properties': {
                **_PROJECT_PATH_PROP,
                'all': {
                    'type': 'boolean',
                    'description': 'if true, return all terms in the term map regardless of @TermName mentions',
                },
            },
            'required': ['project_path'],
        },
    },
    {
        'name': 'load_terms',
        'description': 'Load specific terms and their transitive dependencies by name',
        'inputSchema': {
            'type': 'object',
            'properties': {
                **_PROJECT_PATH_PROP,
                'term_names': {
                    'type': 'string',
                    'description': 'space-separated list of term names (without @) to load',
                },
            },
            'required': ['project_path', 'term_names'],
        },
    },
    {
        'name': 'grep_terms',
        'description': 'Search across term content by keyword',
        'inputSchema': {
            'type': 'object',
            'properties': {
                **_PROJECT_PATH_PROP,
                'query': {
                    'type': 'string',
                    'description': 'substring to search for (case-insensitive)',
                },
                'all': {
                    'type': 'boolean',
                    'description': 'if true, search all terms in the term map instead of only reachable ones',
                },
            },
            'required': ['project_path', 'query'],
        },
    },
    {
        'name': 'resolve_path',
        'description': 'Resolve a Term#path reference to a single nested element (e.g. one recipe, function, or component) without loading the whole term or its transitive dependencies',
        'inputSchema': {
            'type': 'object',
            'properties': {
                **_PROJECT_PATH_PROP,
                'ref': {
                    'type': 'string',
                    'description': 'reference in the form TermName#segment#segment... (e.g. DuckspecProject#validate); leading @ on the term name is optional',
                },
            },
            'required': ['project_path', 'ref'],
        },
    },
]


def _send(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def _respond(id, result: dict) -> None:
    _send({'jsonrpc': '2.0', 'id': id, 'result': result})


def _format_terms_table(terms: list[dict]) -> str:
    rows = ['| Term | File | Description |', '|------|------|-------------|']
    rows += [f"| @{t['name']} | {t['path']} | {t.get('description', '')} |" for t in terms]
    return '\n'.join(rows)


def _format_recipes_table(recipes: list[dict]) -> str:
    rows = ['| Recipe | Term | Description |', '|--------|------|-------------|']
    rows += [f"| {r['name']} | @{r['term']} | {r.get('description', '')} |" for r in recipes]
    return '\n'.join(rows)


def _format_rules_table(rules: list[dict]) -> str:
    rows = ['| Source | Type | Rule |', '|--------|------|------|']
    rows += [f"| @{r['term']} | {r['type']} | {r['text']} |" for r in rules]
    return '\n'.join(rows)


def _format_term_blocks(terms: list[dict]) -> str:
    return '\n\n'.join(
        f"--- @{t['name']} [{t['path']}] ---\n{t['content']}"
        for t in terms
    )


def _call(name: str, arguments: dict) -> str:
    path = arguments['project_path']
    include_all = bool(arguments.get('all', False))

    if name == 'load_project':
        result = resolver.load_project(path)
        terms_table = _format_terms_table(result['terms'])
        recipes_table = _format_recipes_table(result['recipes'])
        rules_table = _format_rules_table(result['rules'])
        return f"{result['root_content']}\n\n## Terms\n\n{terms_table}\n\n## Recipes\n\n{recipes_table}\n\n## Rules\n\n{rules_table}"

    if name == 'list_terms':
        terms = resolver.list_terms(path, include_all=include_all)
        return _format_terms_table(terms)

    if name == 'load_terms':
        term_names = arguments.get('term_names', '').split()
        terms = resolver.load_terms(path, term_names)
        return _format_term_blocks(terms)

    if name == 'grep_terms':
        query = arguments.get('query', '')
        results = resolver.grep_terms(path, query, include_all=include_all)
        rows = ['| Term | File | Matches |', '|------|------|---------|']
        rows += [f"| @{r['name']} | {r['path']} | {'; '.join(r['lines'][:3])} |" for r in results]
        return '\n'.join(rows)

    if name == 'resolve_path':
        ref = arguments.get('ref', '')
        result = resolver.resolve_path(path, ref)
        if result is None:
            return f'not found: {ref}'
        return f"--- {ref} [{result['path']}] ---\n{result['content']}"

    raise ValueError(f'unknown tool: {name}')


def run_server() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if 'id' not in msg:
            continue  # notification — no response needed

        id = msg['id']
        method = msg.get('method', '')
        params = msg.get('params') or {}

        if method == 'initialize':
            _respond(id, {
                'protocolVersion': '2024-11-05',
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'ducktools', 'version': '0.1.0'},
            })
        elif method == 'tools/list':
            _respond(id, {'tools': _TOOLS})
        elif method == 'tools/call':
            name = params.get('name', '')
            arguments = params.get('arguments') or {}
            try:
                result = _call(name, arguments)
                _respond(id, {'content': [{'type': 'text', 'text': result}]})
            except Exception as e:
                _respond(id, {'content': [{'type': 'text', 'text': str(e)}], 'isError': True})
        else:
            _send({'jsonrpc': '2.0', 'id': id, 'error': {'code': -32601, 'message': f'method not found: {method}'}})
