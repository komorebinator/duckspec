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
    {
        'name': 'verify_project',
        'description': 'Mechanical consistency pass over the whole spec — dangling and ambiguous @TermName / Term#path references, unknown or cyclic extends, duplicate or unparseable terms, members that shadow an inherited one, and entries setting fields no type declares. Returns findings as a markdown table, or "no findings" when the spec is internally consistent',
        'inputSchema': {
            'type': 'object',
            'properties': {
                **_PROJECT_PATH_PROP,
                'unreachable': {
                    'type': 'boolean',
                    'description': 'also report terms no reachable term mentions; noisy for framework/library projects whose terms are meant for consumers, so off by default',
                },
                'untyped': {
                    'type': 'boolean',
                    'description': 'also report slots holding entries or a mapping that no type describes, so unknown-field read nothing in them — use it to tell "verified correct" apart from "never looked at"; off by default because a project mid-migration lights up everywhere',
                },
            },
            'required': ['project_path'],
        },
    },
    {
        'name': 'verify_source',
        'description': 'Mechanical spec-versus-source pass: src paths that do not exist, and function entries whose id appears nowhere in the file their component points at. Returns findings as a markdown table, or "no findings". Answers only what a regex can settle — whether a description is actually true of the code still means reading it',
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP},
                        'required': ['project_path']},
    },
    {
        'name': 'term_uses',
        'description': 'Reverse index for one term: which terms extend it, which name it as a type:, which merely reference it. Use before changing a term to see what depends on it',
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP,
            'term_name': {'type': 'string', 'description': 'term to find references to'}},
            'required': ['project_path', 'term_name']},
    },
    {
        'name': 'term_schema',
        'description': "Every member a term effectively has, each tagged with the ancestor that declared it — the term's own properties plus the whole extends chain",
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP,
            'term_name': {'type': 'string', 'description': 'term whose effective schema to resolve'}},
            'required': ['project_path', 'term_name']},
    },
    {
        'name': 'query_terms',
        'description': 'Filter terms by structure rather than text: no extends, extending a given term, declaring a given member, or living under a given folder',
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP,
            'rootless': {'type': 'boolean', 'description': 'only terms declaring no extends'},
            'extending': {'type': 'string', 'description': 'only terms whose extends chain includes this term'},
            'declaring': {'type': 'string', 'description': 'only terms declaring a member of this id'},
            'folder': {'type': 'string', 'description': 'only terms whose path contains this fragment'}},
            'required': ['project_path']},
    },
    {
        'name': 'slot_entries',
        'description': 'List the entries of one slot in one term as id + the fields set on each entry itself',
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP,
            'term_name': {'type': 'string', 'description': 'term holding the slot'},
            'slot': {'type': 'string', 'description': 'slot whose entries to list'}},
            'required': ['project_path', 'term_name', 'slot']},
    },
    {
        'name': 'set_field',
        'description': 'Set a field on the element a Term#path addresses, replacing it if present and inserting it at the element\'s own column if not. Refuses an ambiguous path instead of editing whichever element came first',
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP,
            'ref': {'type': 'string', 'description': 'TermName or TermName#segment#segment...'},
            'field': {'type': 'string', 'description': 'field to set'},
            'value': {'type': 'string', 'description': 'value to set it to'}},
            'required': ['project_path', 'ref', 'field', 'value']},
    },
    {
        'name': 'add_entry',
        'description': 'Append a named entry to the slot a Term#path addresses (e.g. MyTerm#properties). Derives the item column from the entries already there instead of guessing indentation, and refuses an id the slot already has',
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP,
            'ref': {'type': 'string', 'description': 'TermName#segment... ending at the slot to append to'},
            'entry_id': {'type': 'string', 'description': 'id for the new entry'},
            'fields': {'type': 'object', 'description': 'field name to value, written under the new entry'}},
            'required': ['project_path', 'ref', 'entry_id']},
    },
    {
        'name': 'remove_element',
        'description': 'Remove the element a Term#path addresses together with everything nested under it, using indentation for boundaries. Refuses a bare term name and an ambiguous path',
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP,
            'ref': {'type': 'string', 'description': 'TermName#segment#segment...'}},
            'required': ['project_path', 'ref']},
    },
    {
        'name': "add_rule",
        'description': "Append a rule to a term's guidelines, ai_instructions or goals; these hold bare strings that set_field cannot reach",
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP, 'term_name': {'type': 'string'}, 'block': {'type': 'string'}, 'text': {'type': 'string'}},
            'required': ['project_path', 'term_name', 'block', 'text']},
    },
    {
        'name': "remove_rule",
        'description': "Remove the rule matching a substring from a term's rule block; refuses when more than one matches",
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP, 'term_name': {'type': 'string'}, 'block': {'type': 'string'}, 'match': {'type': 'string'}},
            'required': ['project_path', 'term_name', 'block', 'match']},
    },
    {
        'name': "move_rule",
        'description': "Move a rule verbatim between a term's rule blocks, e.g. reclassifying an ai_instruction as a guideline",
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP, 'term_name': {'type': 'string'}, 'from_block': {'type': 'string'}, 'to_block': {'type': 'string'}, 'match': {'type': 'string'}},
            'required': ['project_path', 'term_name', 'from_block', 'to_block', 'match']},
    },
    {
        'name': "create_term",
        'description': "Create a term file in the project terms folder; the filename is derived from the name, since it is the identity",
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP, 'term_name': {'type': 'string'}, 'description': {'type': 'string'}, 'extends': {'type': 'string'}},
            'required': ['project_path', 'term_name', 'description']},
    },
    {
        'name': "rename_term",
        'description': "Rename a term file and rewrite every reference to it across the project",
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP, 'old_name': {'type': 'string'}, 'new_name': {'type': 'string'}},
            'required': ['project_path', 'old_name', 'new_name']},
    },
    {
        'name': "remove_term",
        'description': "Delete a term file, refusing while anything still references it",
        'inputSchema': {'type': 'object', 'properties': {**_PROJECT_PATH_PROP, 'term_name': {'type': 'string'}},
            'required': ['project_path', 'term_name']},
    },
    {
        'name': 'create_workspace',
        'description': 'Add a new named, empty workspace to the shared ~/.duckspec/settings.json registry',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'unique name for the new workspace'},
            },
            'required': ['name'],
        },
    },
    {
        'name': 'use_workspace',
        'description': 'Switch the active workspace — the one whose projects are used to resolve repository URL references',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'name of an existing workspace to activate'},
            },
            'required': ['name'],
        },
    },
    {
        'name': 'list_projects',
        'description': 'List every project registered across all workspaces (not just the active one), as a markdown table with Project/Repository/Path/Description columns',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'add_project',
        'description': "Register a project (by its own `repository` field) in a workspace",
        'inputSchema': {
            'type': 'object',
            'properties': {
                **_PROJECT_PATH_PROP,
                'workspace': {'type': 'string', 'description': 'workspace to add to; defaults to the active workspace'},
            },
            'required': ['project_path'],
        },
    },
    {
        'name': 'remove_project',
        'description': 'Remove a project from a workspace by its repository URL',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'repository': {'type': 'string', 'description': 'repository URL of the project to remove'},
                'workspace': {'type': 'string', 'description': 'workspace to remove from; defaults to the active workspace'},
            },
            'required': ['repository'],
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


def _format_references_table(references: list[dict]) -> str:
    rows = ['| Term | Repository | Description |', '|------|------------|-------------|']
    rows += [f"| @{r['term']} | {r['repository']} | {r.get('description', '')} |" for r in references]
    return '\n'.join(rows)


def _format_rules_tree(term_name: str, tree: dict) -> str:
    lines = [f'## Rules for @{term_name}']
    if not tree['own'] and not tree['inherited']:
        lines.append('(none)')
        return '\n'.join(lines)
    if tree['own']:
        lines.append('own:')
        lines += [f"  - {r['type']}: {r['text']}" for r in tree['own']]
    for group in tree['inherited']:
        lines.append(f"inherited from @{group['term']}:")
        lines += [f"  - {r['type']}: {r['text']}" for r in group['rules']]
    return '\n'.join(lines)


def _format_term_blocks(terms: list[dict]) -> str:
    blocks = []
    for t in terms:
        block = f"--- @{t['name']} [{t['path']}] ---\n{t['content']}"
        if 'rules' in t:
            block += '\n\n' + _format_rules_tree(t['name'], t['rules'])
        blocks.append(block)
    return '\n\n'.join(blocks)


def _format_projects(result: dict) -> str:
    blocks = []
    for wname, workspace in result['workspaces'].items():
        marker = ' (active)' if wname == result['active_workspace'] else ''
        projects = workspace.get('projects', [])
        if not projects:
            blocks.append(f"## {wname}{marker}\n\n(empty)")
            continue
        rows = ['| Project | Repository | Path | Description |', '|---------|------------|------|-------------|']
        for proj in projects:
            label = f"@{proj['name']}" if proj['name'] else '(unreadable — stale path?)'
            rows.append(f"| {label} | {proj['repository']} | {proj['path']} | {proj.get('description', '')} |")
        blocks.append(f"## {wname}{marker}\n\n" + '\n'.join(rows))
    return '\n\n'.join(blocks) if blocks else '(no workspaces registered)'


def _call(name: str, arguments: dict) -> str:
    if name == 'create_workspace':
        resolver.create_workspace(arguments['name'])
        return f"created workspace \"{arguments['name']}\""

    if name == 'use_workspace':
        if resolver.use_workspace(arguments['name']):
            return f"active workspace: {arguments['name']}"
        return f"no such workspace: {arguments['name']}"

    if name == 'list_projects':
        return _format_projects(resolver.list_projects())

    if name == 'add_project':
        repository = resolver.add_project(arguments['project_path'], arguments.get('workspace'))
        if repository is None:
            return f"could not register {arguments['project_path']} — missing `repository` field or no active workspace"
        return f"registered {repository} -> {arguments['project_path']}"

    if name == 'remove_project':
        if resolver.remove_project(arguments['repository'], arguments.get('workspace')):
            return f"removed {arguments['repository']}"
        return f"not found: {arguments['repository']}"

    path = arguments['project_path']
    include_all = bool(arguments.get('all', False))

    if name == 'load_project':
        result = resolver.load_project(path)
        terms_table = _format_terms_table(result['terms'])
        recipes_table = _format_recipes_table(result['recipes'])
        references_table = _format_references_table(result['references'])
        rules_table = _format_rules_table(result['rules'])
        return (
            f"{result['root_content']}\n\n## Terms\n\n{terms_table}\n\n## Recipes\n\n{recipes_table}"
            f"\n\n## References\n\n{references_table}\n\n## Rules (project-wide)\n\n{rules_table}"
        )

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

    if name == 'set_field':
        return resolver.set_field(path, arguments['ref'], arguments['field'], arguments['value'])

    if name == 'add_entry':
        return resolver.add_entry(path, arguments['ref'], arguments['entry_id'],
                                  arguments.get('fields') or {})
    if name == 'remove_element':
        return resolver.remove_element(path, arguments['ref'])

    if name == "add_rule":
        return resolver.add_rule(path, arguments["term_name"], arguments["block"], arguments["text"])

    if name == "remove_rule":
        return resolver.remove_rule(path, arguments["term_name"], arguments["block"], arguments["match"])

    if name == "move_rule":
        return resolver.move_rule(path, arguments["term_name"], arguments["from_block"], arguments["to_block"], arguments["match"])

    if name == "create_term":
        return resolver.create_term(path, arguments["term_name"], arguments["description"], arguments.get('extends', 'Term'))

    if name == "rename_term":
        return resolver.rename_term(path, arguments["old_name"], arguments["new_name"])

    if name == "remove_term":
        return resolver.remove_term(path, arguments["term_name"])

    if name == 'term_uses':
        r = resolver.term_uses(path, arguments['term_name'])
        return '\n'.join(f"{k}: {', '.join('@' + n for n in v) if v else '(none)'}" for k, v in r.items())

    if name == 'term_schema':
        rows = resolver.term_schema(path, arguments['term_name'])
        if not rows:
            return '(no members, or unknown term)'
        return '\n'.join(['| Member | Declared by |', '|--------|-------------|']
                          + [f"| {r['member']} | @{r['declared_by']} |" for r in rows])

    if name == 'query_terms':
        names = resolver.query_terms(path, rootless=bool(arguments.get('rootless', False)),
                                     extending=arguments.get('extending'),
                                     declaring=arguments.get('declaring'),
                                     folder=arguments.get('folder'))
        return '\n'.join('@' + n for n in names) + f'\n\n{len(names)} term(s)'

    if name == 'slot_entries':
        rows = resolver.slot_entries(path, arguments['term_name'], arguments['slot'])
        if not rows:
            return '(no entries)'
        return '\n'.join(['| Id | Fields |', '|----|--------|']
                          + [f"| {r['id']} | {', '.join(r['fields'])} |" for r in rows])

    if name == 'resolve_path':
        ref = arguments.get('ref', '')
        result = resolver.resolve_path(path, ref)
        if result is None:
            return f'not found: {ref}'
        return f"--- {ref} [{result['path']}] ---\n{result['content']}"

    if name in ('verify_project', 'verify_source'):
        findings = (resolver.verify_source(path) if name == 'verify_source' else
                    resolver.verify_project(path, unreachable=bool(arguments.get('unreachable', False)),
                                            untyped=bool(arguments.get('untyped', False))))
        if not findings:
            return 'no findings'
        rows = ['| Severity | Check | Term | Location | Message |',
                '|----------|-------|------|----------|---------|']
        for f in findings:
            location = f"{f['path']}:{f['line']}" if 'line' in f else f['path']
            rows.append(f"| {f['severity']} | {f['check']} | @{f['term']} | {location} | {f['message']} |")
        errors = sum(1 for f in findings if f['severity'] == 'error')
        rows.append(f"\n{errors} error(s), {len(findings) - errors} warning(s)")
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
