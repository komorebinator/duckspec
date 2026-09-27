"""Reachability, both ways round: every public resolver method is called by both front ends, and
every CLI command and MCP tool has an entry in the spec and every entry names one that exists.

The second half exists because the mechanical source check only looks in one direction — that a
function the spec names appears in the code. A command added to the code without a spec entry is
invisible to it, and eight commands and eight tools went undescribed that way until the code was
read by hand."""
import re, pathlib, sys
d = pathlib.Path('ducktools/src/ducktools')
sys.path.insert(0, str(d.parent))

pub = set(re.findall(r'^    def ([a-z][a-z_0-9]*)', (d/'resolver.py').read_text(), re.M))
problems = []
for f in ('cli.py', 'mcp_server.py'):
    wired = set(re.findall(r'resolver\.([a-z_]+)\(', (d/f).read_text()))
    missing = sorted(pub - wired)
    if missing:
        problems.append(f'{f} calls no: {", ".join(missing)}')

from ducktools import cli, mcp_server  # noqa: E402

spec = pathlib.Path('ducktools/ducktools/DuckToolsApp.yaml').read_text()


def described(component: str) -> set[str]:
    """Ids of the function entries directly under one top-level component of the spec."""
    block = re.search(rf'^  - id: {component}\n(.*?)(?=^  - id: |\Z)', spec, re.M | re.S)
    return set(re.findall(r'^      - id: (\S+)', block.group(1), re.M)) if block else set()


for component, actual in (
        ('cli', {c[0] for _, group in cli._COMMAND_GROUPS for c in group}),
        ('mcp_server', {t['name'] for t in mcp_server._TOOLS})):
    spec_ids = described(component)
    if actual - spec_ids:
        problems.append(f'{component}: not in the spec: {", ".join(sorted(actual - spec_ids))}')
    if spec_ids - actual:
        problems.append(f'{component}: in the spec but not in the code: '
                        f'{", ".join(sorted(spec_ids - actual))}')

print('no orphans' if not problems else '\n'.join(problems))
sys.exit(1 if problems else 0)
