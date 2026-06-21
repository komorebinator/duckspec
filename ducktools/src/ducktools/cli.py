import argparse
import sys

from .resolver import resolver


def load(project_path: str, include_all: bool = False) -> None:
    for term in resolver.load_project(project_path, include_all=include_all):
        print(f"--- @{term['name']} [{term['path']}] ---")
        print(term['content'])


def list_terms(project_path: str, include_all: bool = False) -> None:
    terms = resolver.list_project(project_path, include_all=include_all)
    print('| Term | File |')
    print('|------|------|')
    for t in terms:
        print(f"| @{t['name']} | {t['path']} |")


def serve(project_path: str) -> None:
    from .mcp_server import run_server
    run_server()


def main() -> None:
    parser = argparse.ArgumentParser(prog='ducktools')
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('load', 'list'):
        p = sub.add_parser(cmd)
        p.add_argument('project_path')
        p.add_argument('--all', action='store_true', dest='include_all')
    sub.add_parser('serve').add_argument('project_path')
    args = parser.parse_args()
    if args.command == 'load':
        load(args.project_path, include_all=args.include_all)
    elif args.command == 'list':
        list_terms(args.project_path, include_all=args.include_all)
    elif args.command == 'serve':
        serve(args.project_path)
