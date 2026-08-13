import re, pathlib, sys
d = pathlib.Path('ducktools/src/ducktools')
pub = set(re.findall(r'^    def ([a-z][a-z_0-9]*)', (d/'resolver.py').read_text(), re.M))
orphans = {}
for f in ('cli.py', 'mcp_server.py'):
    wired = set(re.findall(r'resolver\.([a-z_]+)\(', (d/f).read_text()))
    missing = sorted(pub - wired)
    if missing:
        orphans[f] = missing
print('no orphans' if not orphans else '\n'.join(f'{f}: {", ".join(m)}' for f, m in orphans.items()))
sys.exit(1 if orphans else 0)
