import json
import sys

from .resolver import resolver

_PATH_SCHEMA = {
    'type': 'object',
    'properties': {
        'project_path': {
            'type': 'string',
            'description': 'path to a DuckspecProject .yaml file',
        },
        'all': {
            'type': 'boolean',
            'description': 'if true, return all terms in the term map regardless of @TermName mentions',
        },
    },
    'required': ['project_path'],
}

_TOOLS = [
    {'name': 'load_project', 'description': 'Load all reachable terms for a Duckspec project', 'inputSchema': _PATH_SCHEMA},
    {'name': 'list_project', 'description': 'List reachable term names and file paths', 'inputSchema': _PATH_SCHEMA},
]


def _send(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def _respond(id, result: dict) -> None:
    _send({'jsonrpc': '2.0', 'id': id, 'result': result})


def _call(name: str, arguments: dict) -> str:
    path = arguments['project_path']
    include_all = bool(arguments.get('all', False))

    if name == 'load_project':
        terms = resolver.load_project(path, include_all=include_all)
        return '\n\n'.join(
            f"--- @{t['name']} [{t['path']}] ---\n{t['content']}"
            for t in terms
        )

    if name == 'list_project':
        terms = resolver.list_project(path, include_all=include_all)
        rows = ['| Term | File |', '|------|------|']
        rows += [f"| @{t['name']} | {t['path']} |" for t in terms]
        return '\n'.join(rows)

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
