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

    sub.add_parser('serve').add_argument('project_path')

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
