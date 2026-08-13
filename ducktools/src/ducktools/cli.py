import argparse

from .resolver import resolver


def _print_terms_table(terms: list[dict]) -> None:
    print('| Term | File | Description |')
    print('|------|------|-------------|')
    for t in terms:
        print(f"| @{t['name']} | {t['path']} | {t.get('description', '')} |")


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


def _print_term_blocks(terms: list[dict]) -> None:
    for t in terms:
        print(f"--- @{t['name']} [{t['path']}] ---")
        print(t['content'])
        if 'rules' in t:
            print()
            print(_format_rules_tree(t['name'], t['rules']))


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


def cmd_load_project(project_path: str) -> None:
    result = resolver.load_project(project_path)
    print(result['root_content'])
    print('\n## Terms\n')
    _print_terms_table(result['terms'])
    print('\n## Recipes\n')
    _print_recipes_table(result['recipes'])
    print('\n## References\n')
    _print_references_table(result['references'])
    print('\n## Rules (project-wide)\n')
    _print_rules_table(result['rules'])


def cmd_list_terms(project_path: str, include_all: bool = False) -> None:
    _print_terms_table(resolver.list_terms(project_path, include_all=include_all))


def cmd_load_terms(project_path: str, term_names: list[str]) -> None:
    _print_term_blocks(resolver.load_terms(project_path, term_names))


def cmd_grep(project_path: str, query: str, include_all: bool = False) -> None:
    results = resolver.grep_terms(project_path, query, include_all=include_all)
    print('| Term | File | Matches |')
    print('|------|------|---------|')
    for r in results:
        matches = '; '.join(r['lines'][:3])
        print(f"| @{r['name']} | {r['path']} | {matches} |")


def cmd_resolve_path(project_path: str, ref: str) -> None:
    result = resolver.resolve_path(project_path, ref)
    if result is None:
        print(f'not found: {ref}')
        return
    print(f"--- {ref} [{result['path']}] ---")
    print(result['content'])


def cmd_verify_project(project_path: str, unreachable: bool = False) -> int:
    findings = resolver.verify_project(project_path, unreachable=unreachable)
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


def cmd_remove_element(project_path: str, ref: str) -> None:
    print(resolver.remove_element(project_path, ref))


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

    sub.add_parser('load-project').add_argument('project_path')

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

    p = sub.add_parser('remove')
    p.add_argument('project_path'); p.add_argument('ref')

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

    if args.command == 'load-project':
        cmd_load_project(args.project_path)
    elif args.command == 'list-terms':
        cmd_list_terms(args.project_path, include_all=args.include_all)
    elif args.command == 'load-terms':
        cmd_load_terms(args.project_path, args.term_names)
    elif args.command == 'grep':
        cmd_grep(args.project_path, args.query, include_all=args.include_all)
    elif args.command == 'resolve-path':
        cmd_resolve_path(args.project_path, args.ref)
    elif args.command == 'verify-project':
        raise SystemExit(cmd_verify_project(args.project_path, unreachable=args.unreachable))
    elif args.command == 'uses':
        cmd_uses(args.project_path, args.term_name)
    elif args.command == 'schema':
        cmd_schema(args.project_path, args.term_name)
    elif args.command == 'query':
        cmd_query(args.project_path, args.rootless, args.extending, args.declaring, args.folder)
    elif args.command == 'entries':
        cmd_entries(args.project_path, args.ref)
    elif args.command == 'set':
        cmd_set_field(args.project_path, args.ref, args.field, args.value)
    elif args.command == 'remove':
        cmd_remove_element(args.project_path, args.ref)
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
