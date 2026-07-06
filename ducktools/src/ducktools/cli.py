import argparse

from .resolver import resolver


def _print_terms_table(terms: list[dict]) -> None:
    print('| Term | File | Description |')
    print('|------|------|-------------|')
    for t in terms:
        print(f"| @{t['name']} | {t['path']} | {t.get('description', '')} |")


def _print_term_blocks(terms: list[dict]) -> None:
    for t in terms:
        print(f"--- @{t['name']} [{t['path']}] ---")
        print(t['content'])


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


def cmd_load_project(project_path: str) -> None:
    result = resolver.load_project(project_path)
    print(result['root_content'])
    print('\n## Terms\n')
    _print_terms_table(result['terms'])
    print('\n## Recipes\n')
    _print_recipes_table(result['recipes'])
    print('\n## Rules\n')
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


def cmd_create_workspace(name: str) -> None:
    resolver.create_workspace(name)
    print(f'created workspace "{name}"')


def cmd_use_workspace(name: str) -> None:
    if resolver.use_workspace(name):
        print(f'active workspace: {name}')
    else:
        print(f'no such workspace: {name}')


def cmd_list_workspaces() -> None:
    result = resolver.list_workspaces()
    active = result['active_workspace']
    for name, workspace in result['workspaces'].items():
        marker = '*' if name == active else ' '
        print(f"{marker} {name}")
        for repository, path in workspace.get('projects', {}).items():
            print(f"    {repository} -> {path}")


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

    sub.add_parser('serve').add_argument('project_path', nargs='?')

    sub.add_parser('create-workspace').add_argument('name')

    sub.add_parser('use-workspace').add_argument('name')

    sub.add_parser('list-workspaces')

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
    elif args.command == 'serve':
        from .mcp_server import run_server
        run_server()
    elif args.command == 'create-workspace':
        cmd_create_workspace(args.name)
    elif args.command == 'use-workspace':
        cmd_use_workspace(args.name)
    elif args.command == 'list-workspaces':
        cmd_list_workspaces()
    elif args.command == 'add-project':
        cmd_add_project(args.project_path, args.workspace_name)
    elif args.command == 'remove-project':
        cmd_remove_project(args.repository, args.workspace_name)
