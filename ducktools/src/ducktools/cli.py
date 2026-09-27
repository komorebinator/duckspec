import argparse
import json

from .resolver import resolver


def _print_terms_table(terms: list[dict]) -> None:
    print('| Term | File | Description |')
    print('|------|------|-------------|')
    for t in terms:
        print(f"| @{t['name']} | {t['path']} | {t.get('description', '')} |")


def _print_vocabulary(vocabulary: list[dict]) -> None:
    """One `Terms from @<project>` table per used project; prints nothing when there are none."""
    for v in vocabulary:
        print(f"\n## Terms from @{v['project']}\n")
        _print_terms_table(v['terms'])


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


NOTE = 'References (mentioned, not loaded — load them if the question is about one): '


def _print_term_blocks(terms: list[dict]) -> None:
    for t in terms:
        print(f"--- @{t['name']} [{t['path']}] ---")
        print(t['content'])
        if 'rules' in t:
            print()
            print(_format_rules_tree(t['name'], t['rules']))
        if t.get('references'):
            print()
            print(NOTE + ', '.join('@' + n for n in t['references']))


def _print_recipes_table(recipes: list[dict]) -> None:
    print('| Recipe | Term | Description |')
    print('|--------|------|-------------|')
    for r in recipes:
        print(f"| {r['name']} | @{r['term']} | {r.get('description', '')} |")


def _print_rules_table(rules: list[dict]) -> None:
    print('| Source | Type | Rule |')
    print('|--------|------|------|')
    for r in rules:
        print(f"| @{r['term']} | {r['type']} | {r['text']} |")


def _print_references_table(references: list[dict]) -> None:
    print('| Term | Repository | Description |')
    print('|------|------------|-------------|')
    for r in references:
        print(f"| @{r['term']} | {r['repository']} | {r.get('description', '')} |")


_PROJECT_ARG = 'a .yaml file path, or the identifier of a project registered in the active workspace'
_REF_ARG = 'a `TermName#segment#segment...` path; refused when it matches more than one element'

# group -> (name, usage, purpose, [(argument, description), ...])
_COMMAND_GROUPS = [
    ('reading', [
        ('load-project', 'load-project <project>',
         'Root file, workflow pointer, term list, recipes, and project-wide rules — start here',
         [('project', _PROJECT_ARG)]),
        ('load-workflow', 'load-workflow <project>',
         'Print the project\'s workflow in full — steps, rules, and the overrides the project sets',
         [('project', _PROJECT_ARG)]),
        ('list-terms', 'list-terms <project> [--all]',
         'Every reachable term with its description',
         [('project', _PROJECT_ARG),
          ('--all', 'include terms in the map that nothing reaches')]),
        ('load-terms', 'load-terms <project> <Term> [Term ...]',
         'Named terms with their `extends` chain and applicable rules; other terms they mention are named, not loaded',
         [('project', _PROJECT_ARG),
          ('Term', 'one or more term names, written without the leading @')]),
        ('resolve-path', 'resolve-path <project> <ref>',
         'One nested element, without its term or that term\'s dependencies',
         [('project', _PROJECT_ARG), ('ref', _REF_ARG)]),
        ('grep', 'grep <project> <query> [--all]',
         'Search across term content by keyword',
         [('project', _PROJECT_ARG),
          ('query', 'substring to search for, case-insensitive'),
          ('--all', 'search the whole term map, not only reachable terms')]),
    ]),
    ('structure', [
        ('schema', 'schema <project> <Term>',
         'Every member of a term, tagged with the ancestor declaring it',
         [('project', _PROJECT_ARG), ('Term', 'the term whose effective schema to resolve')]),
        ('uses', 'uses <project> <Term>',
         'What extends it, types by it, and references it — check before changing a term',
         [('project', _PROJECT_ARG), ('Term', 'the term to find references to')]),
        ('query', 'query <project> [--rootless] [--extending T] [--declaring M] [--folder F]',
         'Terms matching structural filters',
         [('project', _PROJECT_ARG),
          ('--rootless', 'only terms declaring no `extends`'),
          ('--extending', 'only terms whose `extends` chain includes this term'),
          ('--declaring', 'only terms declaring a member of this id'),
          ('--folder', 'only terms whose path contains this fragment')]),
        ('entries', 'entries <project> <Term>#<slot>',
         'The ids and fields of one slot\'s entries',
         [('project', _PROJECT_ARG), ('ref', 'a reference of the form `TermName#slot`')]),
    ]),
    ('checking', [
        ('verify-project', 'verify-project <project> [--unreachable] [--untyped]',
         'Report dangling refs, bad `extends`, shadowed members; exits 1 on any error',
         [('project', _PROJECT_ARG),
          ('--unreachable', 'also report terms nothing reaches'),
          ('--untyped', 'also report slots whose contents no check reads')]),
        ('verify-source', 'verify-source <project>',
         'Report declared paths that do not exist and functions absent from the source',
         [('project', _PROJECT_ARG)]),
    ]),
    ('editing', [
        ('set', 'set <project> <ref> <field> <value>',
         'Set a field on the addressed element, replacing it if already present',
         [('project', _PROJECT_ARG), ('ref', _REF_ARG),
          ('field', 'the field to set'), ('value', 'the value to set it to')]),
        ('edit', 'edit <project> <ref> <field> <old> <new>',
         'Replace one piece of a field\'s text, leaving the rest; the piece must occur exactly once',
         [('project', _PROJECT_ARG), ('ref', _REF_ARG), ('field', 'the field to edit'),
          ('old', 'the exact text to replace'), ('new', 'the text to put in its place')]),
        ('add', 'add <project> <ref> <id> [field=value ...]',
         'Append a named entry to the addressed slot',
         [('project', _PROJECT_ARG), ('ref', 'a reference of the form `TermName#slot`'),
          ('id', 'id for the new entry'), ('field=value', 'fields to write under it')]),
        ('remove', 'remove <project> <ref>',
         'Remove the addressed element and everything nested under it',
         [('project', _PROJECT_ARG), ('ref', _REF_ARG)]),
        ('add-item', 'add-item <project> <Term#path> <text> [--after M]',
         'Append a bare string to the addressed list — guidelines, instructions, goals, software',
         [('project', _PROJECT_ARG), ('Term#path', 'path ending at the list to append to'),
          ('text', 'the text to add'), ('--after', 'insert after the item matching this substring')]),
        ('set-item', 'set-item <project> <Term#path> <match> <text>',
         'Replace one item in place; remove plus add would move it to the end of its block',
         [('project', _PROJECT_ARG), ('Term#path', 'path ending at the list'),
          ('match', 'substring identifying the item'), ('text', 'the replacement text')]),
        ('remove-item', 'remove-item <project> <Term#path> <match>',
         'Remove one item from the addressed list; refuses when the match is ambiguous',
         [('project', _PROJECT_ARG), ('Term#path', 'path ending at the list'),
          ('match', 'substring identifying the item')]),
        ('move-item', 'move-item <project> <from#path> <to#path> <match>',
         'Move an item verbatim between lists — e.g. an ai_instruction that is really a guideline',
         [('project', _PROJECT_ARG), ('from#path', 'list the item sits in'),
          ('to#path', 'list to move it to'), ('match', 'substring identifying the item')]),
        ('create-term', 'create-term <project> <Term> <description> [--extends T]',
         'Create a term file; the filename is derived from the name, since it is the identity',
         [('project', _PROJECT_ARG), ('Term', 'CamelCase name, becoming the filename'),
          ('description', 'what the term represents'), ('--extends', 'parent term; defaults to Term')]),
        ('rename-term', 'rename-term <project> <Old> <New>',
         'Rename a term file and rewrite every reference to it across the project',
         [('project', _PROJECT_ARG), ('Old', 'current term name'), ('New', 'new term name')]),
        ('remove-term', 'remove-term <project> <Term>',
         'Delete a term file, refusing while anything still references it',
         [('project', _PROJECT_ARG), ('Term', 'the term to delete')]),
        ('replace-term', 'replace-term <project> <Term> <file>',
         "Replace a term file's whole content, normalized on the way in; `-` reads standard input",
         [('project', _PROJECT_ARG), ('Term', 'the term to replace'),
          ('file', 'where to read the new content from; - for standard input')]),
        ('normalize', 'normalize <project>',
         'Quote every value YAML would misread, so each term file is valid YAML',
         [('project', _PROJECT_ARG)]),
    ]),
    ('workspace', [
        ('list-projects', 'list-projects',
         'Every registered project across all workspaces, marking the active one', []),
        ('add-project', 'add-project <project_path> [--workspace NAME]',
         'Register a project, keyed by its own `repository` URL',
         [('project_path', 'path to the project .yaml file to register'),
          ('--workspace', 'workspace to add to; defaults to the active one')]),
        ('remove-project', 'remove-project <repository> [--workspace NAME]',
         'Unregister a project by its repository URL',
         [('repository', 'repository URL of the project to remove'),
          ('--workspace', 'workspace to remove from; defaults to the active one')]),
        ('create-workspace', 'create-workspace <name>',
         'Create a new, empty workspace and make it active',
         [('name', 'unique name for the new workspace')]),
        ('use-workspace', 'use-workspace <name>',
         'Switch the active workspace — the one URL references resolve against',
         [('name', 'name of an existing workspace to activate')]),
    ]),
    ('other', [
        ('serve', 'serve',
         'Start the MCP server on stdio; each request carries its own project', []),
        ('help', 'help [command]',
         'This reference, or one command in detail',
         [('command', 'optional command to describe; omitted prints the full reference')]),
    ]),
]


def _find_command(name: str) -> tuple | None:
    for _, commands in _COMMAND_GROUPS:
        for command in commands:
            if command[0] == name:
                return command
    return None


def cmd_help(command: str | None = None) -> None:
    if command:
        found = _find_command(command)
        if found is not None:
            _, usage, purpose, arguments = found
            print(f'usage: ducktools {usage}\n')
            print(f'{purpose}\n')
            if arguments:
                print('| Argument | Description |')
                print('|----------|-------------|')
                for argument, description in arguments:
                    print(f'| {argument} | {description} |')
            return
        print(f'no such command: {command}\n')

    print('usage: ducktools <command> [arguments]\n')
    print(f'<project> is {_PROJECT_ARG}.')
    print('Run `ducktools help <command>` for one command\'s arguments.')
    for group, commands in _COMMAND_GROUPS:
        print(f'\n## {group}\n')
        print('| Command | Purpose |')
        print('|---------|---------|')
        for _, usage, purpose, _args in commands:
            print(f'| {usage} | {purpose} |')


def cmd_load_project(project_path: str) -> None:
    result = resolver.load_project(project_path)
    print(result['root_content'])
    print('\n## Workflow\n')
    print(result['workflow']['note'])
    print('\n## Terms\n')
    _print_terms_table(result['terms'])
    _print_vocabulary(result['vocabulary'])
    print('\n## Recipes\n')
    _print_recipes_table(result['recipes'])
    print('\n## References\n')
    _print_references_table(result['references'])
    print('\n## Rules (project-wide)\n')
    _print_rules_table(result['rules'])


def cmd_load_workflow(project_path: str) -> None:
    result = resolver.load_workflow(project_path)
    if 'error' in result:
        print(result['error'])
        return
    print(f"# @{result['name']}\n")
    print('## As this project configures it\n')
    print(result['configuration'].rstrip() + '\n')
    _print_term_blocks(result['terms'])


def cmd_list_terms(project_path: str, include_all: bool = False) -> None:
    _print_terms_table(resolver.list_terms(project_path, include_all=include_all))


def cmd_load_terms(project_path: str, term_names: list[str]) -> None:
    _print_term_blocks(resolver.load_terms(project_path, term_names))


def cmd_grep(project_path: str, query: str, include_all: bool = False) -> None:
    results = resolver.grep_terms(project_path, query, include_all=include_all)
    print('| Reference | Line | Match |')
    print('|-----------|------|-------|')
    for r in results:
        for h in r['hits'][:3]:
            print(f"| @{h['ref']} | {h['line']} | {h['text'][:100]} |")


def cmd_resolve_path(project_path: str, ref: str) -> None:
    result = resolver.resolve_path(project_path, ref)
    if result is None:
        print(f'not found: {ref}')
        return
    if result.get('error'):
        print(result['error'])
        return
    print(f"--- {ref} [{result['path']}] ---")
    print(result['content'])


def cmd_verify_source(project_path: str) -> int:
    return _print_findings(resolver.verify_source(project_path))


def cmd_verify_project(project_path: str, unreachable: bool = False,
                       untyped: bool = False) -> int:
    return _print_findings(
        resolver.verify_project(project_path, unreachable=unreachable, untyped=untyped))


def _print_findings(findings: list[dict]) -> int:
    if not findings:
        print('no findings')
        return 0
    print('| Severity | Check | Term | Location | Message |')
    print('|----------|-------|------|----------|---------|')
    for f in findings:
        location = f"{f['path']}:{f['line']}" if 'line' in f else f['path']
        print(f"| {f['severity']} | {f['check']} | @{f['term']} | {location} | {f['message']} |")
    errors = sum(1 for f in findings if f['severity'] == 'error')
    print(f"\n{errors} error(s), {len(findings) - errors} warning(s)")
    return 1 if errors else 0


def cmd_uses(project_path: str, term_name: str) -> None:
    r = resolver.term_uses(project_path, term_name)
    for label, key in (('extended by', 'extended_by'), ('typed by', 'typed_by'), ('referenced by', 'referenced_by')):
        names = r[key]
        print(f"{label}: {', '.join('@' + n for n in names) if names else '(none)'}")


def cmd_schema(project_path: str, term_name: str) -> None:
    rows = resolver.term_schema(project_path, term_name)
    if not rows:
        print('(no members, or unknown term)')
        return
    print('| Member | Declared by |')
    print('|--------|-------------|')
    for r in rows:
        print(f"| {r['member']} | @{r['declared_by']} |")


def cmd_query(project_path: str, rootless: bool, extending: str | None,
              declaring: str | None, folder: str | None) -> None:
    names = resolver.query_terms(project_path, rootless=rootless, extending=extending,
                                 declaring=declaring, folder=folder)
    for n in names:
        print(f'@{n}')
    print(f'\n{len(names)} term(s)')


def cmd_entries(project_path: str, ref: str) -> None:
    term_name, _, slot = ref.partition('#')
    rows = resolver.slot_entries(project_path, term_name.lstrip('@'), slot)
    if not rows:
        print('(no entries)')
        return
    print('| Id | Fields |')
    print('|----|--------|')
    for r in rows:
        print(f"| {r['id']} | {', '.join(r['fields'])} |")


def cmd_set_field(project_path: str, ref: str, field: str, value: str) -> None:
    print(resolver.set_field(project_path, ref, field, value))


def cmd_edit_field(project_path: str, ref: str, field: str, old: str, new: str) -> None:
    print(resolver.edit_field(project_path, ref, field, old, new))


def cmd_add_entry(project_path: str, ref: str, entry_id: str, fields: list[str]) -> None:
    parsed = {}
    for pair in fields:
        if '=' not in pair:
            print(f"expected field=value, got: {pair}")
            return
        field, value = pair.split('=', 1)
        if value[:1] in '[{':
            # a list or mapping, written as JSON, becomes a nested block under the field
            try:
                value = json.loads(value)
            except ValueError as e:
                print(f"{field}: not valid JSON ({e})")
                return
        parsed[field] = value
    print(resolver.add_entry(project_path, ref, entry_id, parsed))


def cmd_remove_element(project_path: str, ref: str) -> None:
    print(resolver.remove_element(project_path, ref))


def cmd_add_item(project_path: str, ref: str, text: str, after: str | None = None) -> None:
    print(resolver.add_item(project_path, ref, text, after))


def cmd_set_item(project_path: str, ref: str, match: str, text: str) -> None:
    print(resolver.set_item(project_path, ref, match, text))


def cmd_remove_item(project_path: str, ref: str, match: str) -> None:
    print(resolver.remove_item(project_path, ref, match))


def cmd_move_item(project_path: str, from_ref: str, to_ref: str, match: str) -> None:
    print(resolver.move_item(project_path, from_ref, to_ref, match))


def cmd_create_term(project_path: str, term_name: str, description: str, extends: str) -> None:
    print(resolver.create_term(project_path, term_name, description, extends))


def cmd_rename_term(project_path: str, old_name: str, new_name: str) -> None:
    print(resolver.rename_term(project_path, old_name, new_name))


def cmd_remove_term(project_path: str, term_name: str) -> None:
    print(resolver.remove_term(project_path, term_name))


def cmd_replace_term(project_path: str, term_name: str, file: str) -> None:
    import sys
    content = sys.stdin.read() if file == '-' else open(file, encoding='utf-8').read()
    print(resolver.replace_term(project_path, term_name, content))


def cmd_normalize(project_path: str) -> None:
    changed = resolver.normalize(project_path)
    if not changed:
        print('nothing to change')
    for c in changed:
        print(f"{c['path']}: quoted {c['quoted']} value(s)")


def cmd_create_workspace(name: str) -> None:
    resolver.create_workspace(name)
    print(f'created workspace "{name}"')


def cmd_use_workspace(name: str) -> None:
    if resolver.use_workspace(name):
        print(f'active workspace: {name}')
    else:
        print(f'no such workspace: {name}')


def cmd_list_projects() -> None:
    result = resolver.list_projects()
    if not result['workspaces']:
        print('(no workspaces registered)')
        return
    active = result['active_workspace']
    for name, workspace in result['workspaces'].items():
        marker = ' (active)' if name == active else ''
        print(f"\n## {name}{marker}\n")
        projects = workspace.get('projects', [])
        if not projects:
            print('(empty)')
            continue
        print('| Project | Repository | Path | Description |')
        print('|---------|------------|------|-------------|')
        for proj in projects:
            label = f"@{proj['name']}" if proj['name'] else '(unreadable — stale path?)'
            print(f"| {label} | {proj['repository']} | {proj['path']} | {proj.get('description', '')} |")


def cmd_add_project(project_path: str, workspace_name: str | None = None) -> None:
    repository = resolver.add_project(project_path, workspace_name)
    if repository is None:
        print(f'could not register {project_path} — missing `repository` field or no active workspace')
    else:
        print(f'registered {repository} -> {project_path}')


def cmd_remove_project(repository: str, workspace_name: str | None = None) -> None:
    if resolver.remove_project(repository, workspace_name):
        print(f'removed {repository}')
    else:
        print(f'not found: {repository}')


def main() -> None:
    parser = argparse.ArgumentParser(prog='ducktools')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('help').add_argument('topic', nargs='?')

    sub.add_parser('load-project').add_argument('project_path')

    p = sub.add_parser('load-workflow'); p.add_argument('project_path')
    p = sub.add_parser('list-terms')
    p.add_argument('project_path')
    p.add_argument('--all', action='store_true', dest='include_all')

    p = sub.add_parser('load-terms')
    p.add_argument('project_path')
    p.add_argument('term_names', nargs='+')

    p = sub.add_parser('grep')
    p.add_argument('project_path')
    p.add_argument('query')
    p.add_argument('--all', action='store_true', dest='include_all')

    p = sub.add_parser('resolve-path')
    p.add_argument('project_path')
    p.add_argument('ref')

    p = sub.add_parser('verify-project')
    p.add_argument('project_path')
    p.add_argument('--unreachable', action='store_true')
    p.add_argument('--untyped', action='store_true')

    p = sub.add_parser('verify-source')
    p.add_argument('project_path')

    p = sub.add_parser('uses')
    p.add_argument('project_path')
    p.add_argument('term_name')

    p = sub.add_parser('schema')
    p.add_argument('project_path')
    p.add_argument('term_name')

    p = sub.add_parser('query')
    p.add_argument('project_path')
    p.add_argument('--rootless', action='store_true')
    p.add_argument('--extending')
    p.add_argument('--declaring')
    p.add_argument('--folder')

    p = sub.add_parser('entries')
    p.add_argument('project_path')
    p.add_argument('ref')

    p = sub.add_parser('set')
    p.add_argument('project_path'); p.add_argument('ref')
    p.add_argument('field'); p.add_argument('value')

    p = sub.add_parser('edit')
    p.add_argument('project_path'); p.add_argument('ref')
    p.add_argument('field'); p.add_argument('old'); p.add_argument('new')

    p = sub.add_parser('add')
    p.add_argument('project_path'); p.add_argument('ref'); p.add_argument('entry_id')
    p.add_argument('fields', nargs='*')

    p = sub.add_parser('remove')
    p.add_argument('project_path'); p.add_argument('ref')

    p = sub.add_parser('add-item'); [p.add_argument(a) for a in ('project_path','ref','text')]; p.add_argument('--after')
    p = sub.add_parser('set-item'); [p.add_argument(a) for a in ('project_path','ref','match','text')]
    p = sub.add_parser('remove-item'); [p.add_argument(a) for a in ('project_path','ref','match')]
    p = sub.add_parser('move-item'); [p.add_argument(a) for a in ('project_path','from_ref','to_ref','match')]
    p = sub.add_parser('create-term'); [p.add_argument(a) for a in ('project_path','term_name','description')]; p.add_argument('--extends', default='Term')
    p = sub.add_parser('rename-term'); [p.add_argument(a) for a in ('project_path','old_name','new_name')]
    p = sub.add_parser('remove-term'); [p.add_argument(a) for a in ('project_path','term_name')]
    p = sub.add_parser('replace-term'); [p.add_argument(a) for a in ('project_path','term_name','file')]
    p = sub.add_parser('normalize'); p.add_argument('project_path')

    sub.add_parser('serve').add_argument('project_path', nargs='?')

    sub.add_parser('create-workspace').add_argument('name')

    sub.add_parser('use-workspace').add_argument('name')

    sub.add_parser('list-projects')

    p = sub.add_parser('add-project')
    p.add_argument('project_path')
    p.add_argument('--workspace', dest='workspace_name')

    p = sub.add_parser('remove-project')
    p.add_argument('repository')
    p.add_argument('--workspace', dest='workspace_name')

    args = parser.parse_args()

    if args.command == 'help':
        cmd_help(args.topic)
    elif args.command == 'load-project':
        cmd_load_project(args.project_path)
    elif args.command == 'load-workflow':
        cmd_load_workflow(args.project_path)
    elif args.command == 'list-terms':
        cmd_list_terms(args.project_path, include_all=args.include_all)
    elif args.command == 'load-terms':
        cmd_load_terms(args.project_path, args.term_names)
    elif args.command == 'grep':
        cmd_grep(args.project_path, args.query, include_all=args.include_all)
    elif args.command == 'resolve-path':
        cmd_resolve_path(args.project_path, args.ref)
    elif args.command == 'verify-project':
        raise SystemExit(cmd_verify_project(args.project_path, unreachable=args.unreachable,
                                            untyped=args.untyped))
    elif args.command == 'verify-source':
        raise SystemExit(cmd_verify_source(args.project_path))
    elif args.command == 'uses':
        cmd_uses(args.project_path, args.term_name)
    elif args.command == 'schema':
        cmd_schema(args.project_path, args.term_name)
    elif args.command == 'query':
        cmd_query(args.project_path, args.rootless, args.extending, args.declaring, args.folder)
    elif args.command == 'entries':
        cmd_entries(args.project_path, args.ref)
    elif args.command == 'add':
        cmd_add_entry(args.project_path, args.ref, args.entry_id, args.fields)
    elif args.command == 'set':
        cmd_set_field(args.project_path, args.ref, args.field, args.value)
    elif args.command == 'edit':
        cmd_edit_field(args.project_path, args.ref, args.field, args.old, args.new)
    elif args.command == 'remove':
        cmd_remove_element(args.project_path, args.ref)
    elif args.command == 'add-item':
        cmd_add_item(args.project_path, args.ref, args.text, args.after)
    elif args.command == 'set-item':
        cmd_set_item(args.project_path, args.ref, args.match, args.text)
    elif args.command == 'remove-item':
        cmd_remove_item(args.project_path, args.ref, args.match)
    elif args.command == 'move-item':
        cmd_move_item(args.project_path, args.from_ref, args.to_ref, args.match)
    elif args.command == 'create-term':
        cmd_create_term(args.project_path, args.term_name, args.description, args.extends)
    elif args.command == 'rename-term':
        cmd_rename_term(args.project_path, args.old_name, args.new_name)
    elif args.command == 'remove-term':
        cmd_remove_term(args.project_path, args.term_name)
    elif args.command == 'replace-term':
        cmd_replace_term(args.project_path, args.term_name, args.file)
    elif args.command == 'normalize':
        cmd_normalize(args.project_path)
    elif args.command == 'serve':
        from .mcp_server import run_server
        run_server()
    elif args.command == 'create-workspace':
        cmd_create_workspace(args.name)
    elif args.command == 'use-workspace':
        cmd_use_workspace(args.name)
    elif args.command == 'list-projects':
        cmd_list_projects()
    elif args.command == 'add-project':
        cmd_add_project(args.project_path, args.workspace_name)
    elif args.command == 'remove-project':
        cmd_remove_project(args.repository, args.workspace_name)
