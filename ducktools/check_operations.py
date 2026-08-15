"""Every public resolver method, against a throwaway project in a temp directory.

check_wiring proves a method can be reached; this proves it does what it says. The
settings path is redirected before anything runs, so the workspace methods write to a
temp file and never to the real registry.
"""
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from ducktools import resolver as R  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix='ducktools-check-'))
R._SETTINGS_PATH = TMP / 'settings.json'

failures: list[str] = []
checked: list[str] = []


def check(method: str, condition: bool, detail: str = '') -> None:
    checked.append(method)
    mark = 'ok' if condition else 'FAIL'
    print(f'  {method:22} {mark}{"  " + detail if detail and not condition else ""}')
    if not condition:
        failures.append(f'{method}: {detail}')


def fixture() -> Path:
    """A minimal project: a root, two terms, and a source file to point at."""
    root = TMP / 'project'
    (root / 'Fixture').mkdir(parents=True)
    (root / 'src').mkdir()
    (root / 'src' / 'widget.py').write_text('def spin():\n    return 1\n')
    # `uses:` the real framework so @Term and @DuckspecProject resolve — a fixture that
    # cannot pass verify_project would test the checker against a broken project.
    duckspec = Path(__file__).resolve().parent.parent / 'Duckspec.yaml'
    (root / 'Fixture.yaml').write_text(
        'description: A fixture project.\n'
        'extends: @DuckspecProject\n'
        'terms_folder: Fixture\n'
        'repository: https://example.invalid/fixture\n'
        f'uses:\n'
        f'  - {duckspec}\n'
        'settings:\n'
        '  src: .\n'
        'software:\n'
        '  - @Widget\n'
        'guidelines:\n'
        '  - Keep the fixture small.\n'
    )
    (root / 'Fixture' / 'Widget.yaml').write_text(
        'description: A widget, described for the fixture.\n'
        'extends: @Term\n'
        'src: src/widget.py\n'
        'properties:\n'
        '  - id: colour\n'
        '    description: what colour the widget is\n'
        'functions:\n'
        '  - id: spin\n'
        '    description: turns the widget once\n'
    )
    (root / 'Fixture' / 'Gadget.yaml').write_text(
        'description: A gadget, built on @Widget.\n'
        'extends: @Widget\n'
    )
    return root / 'Fixture.yaml'


project = str(fixture())
r = R.Resolver()

# --- reading -----------------------------------------------------------------
loaded = r.load_project(project)
check('load_project', 'A fixture project.' in str(loaded) and 'Widget' in str(loaded))

terms = r.list_terms(project)
check('list_terms', any(t['name'] == 'Widget' for t in terms), f'got {terms}')

blocks = r.load_terms(project, ['Widget'])
check('load_terms', 'turns the widget once' in str(blocks))

hits = r.grep_terms(project, 'colour')
check('grep_terms', any('Widget' == h['name'] for h in hits), f'got {hits}')

# every hit names the element it sits in, and that name has to be one resolve_path accepts —
# a ref that only looks right is worth no more than the bare line it replaced
_refs = [h['ref'] for res in hits for h in res['hits']]
check('grep_terms/ref', 'Widget#properties#colour' in _refs, f'got {_refs}')
check('grep_terms/resolvable',
      all(r.resolve_path(project, ref) is not None for ref in _refs), f'got {_refs}')

resolved = r.resolve_path(project, 'Widget#spin')
check('resolve_path', resolved is not None and 'turns the widget once' in resolved['content'])

# two entries under one id is a defect in the spec; returning whichever came first hides it,
# and hid a stale duplicate of a whole recipe for five releases
_twin = Path(project).parent / 'Fixture' / 'Twin.yaml'
_twin.write_text('description: Two entries share an id.\nextends: @Term\n'
                 'properties:\n  - id: dup\n    description: first\n'
                 'functions:\n  - id: dup\n    description: second\n')
_amb = r.resolve_path(project, 'Twin#dup')
check('resolve_path/ambiguous', _amb is not None and 'ambiguous' in _amb.get('error', ''),
      f'got {_amb}')
_twin.unlink()

check('verify_project', r.verify_project(project) == [], f'got {r.verify_project(project)}')
check('verify_source', r.verify_source(project) == [], f'got {r.verify_source(project)}')


# --- absent-function: which file the name has to be in ------------------------
# The name search itself is deliberately loose — no tool is going to enumerate every way a
# function can be declared in every language, and a check that guesses wrong shouts at
# working code. What it must not do is search the wrong bytes: a sibling module's symbol,
# or compiled bytecode that still remembers a name the sources dropped.
def absent_project() -> tuple[str, dict]:
    root = TMP / 'absent'
    (root / 'Absent').mkdir(parents=True)
    pkg = root / 'pkg'
    pkg.mkdir()
    (pkg / 'real.py').write_text(
        'def declared_py():\n'
        '    return 1\n\n\n'
        'def caller():\n'
        '    # mentions only_a_comment in passing\n'
        '    print("only_a_string")\n'
        '    return only_a_call()\n'
        'CLI = ["command-name"]\n'
    )
    (pkg / 'real.js').write_text(
        'import { imported_only } from "./other.js";\n'
        'export function declared_js(a) { return a; }\n'
        'export class Thing {\n'
        '  declared_method() { return 2; }\n'
        '}\n'
    )
    (pkg / 'real.sh').write_text(
        '#!/bin/sh\n'
        'declared_sh() {\n'
        '  echo hi\n'
        '}\n'
    )
    # bytecode: unreadable as text, and stale by construction — must never satisfy anything
    (pkg / '__pycache__').mkdir()
    (pkg / '__pycache__' / 'real.cpython-313.pyc').write_bytes(
        b'\xcb\r\r\n\x00\x00\x00\x00' + b'deleted_long_ago\x00' * 4)

    duckspec = Path(__file__).resolve().parent.parent / 'Duckspec.yaml'
    (root / 'Absent.yaml').write_text(
        'description: A project for the absent-function cases.\n'
        'extends: @DuckspecProject\n'
        'terms_folder: Absent\n'
        'repository: https://example.invalid/absent\n'
        f'uses:\n  - {duckspec}\n'
        'settings:\n  src: .\n'
    )
    (root / 'Absent' / 'Cases.yaml').write_text(
        'description: Function entries checked against a whole package.\n'
        'extends: @Term\n'
        'src: pkg/\n'
        'functions:\n'
        '  - id: declared_py\n    description: a real Python definition\n'
        '  - id: declared_js\n    description: a real JavaScript definition\n'
        '  - id: declared_method\n    description: a real JavaScript class method\n'
        '  - id: declared_sh\n    description: a real shell definition\n'
        '  - id: command-name\n    description: a command name, present only in quotes\n'
        '  - id: deleted_long_ago\n    description: survives only inside stale bytecode\n'
    )
    # a list-form `src:` — the form that used to read as no path at all, skipping every
    # function under it without a word
    (root / 'Absent' / 'Split.yaml').write_text(
        'description: One term whose code is split across two files.\n'
        'extends: @Term\n'
        'src:\n'
        '  - pkg/real.js\n'
        '  - pkg/real.sh\n'
        'functions:\n'
        '  - id: declared_js\n    description: lives in the first file\n'
        '  - id: declared_sh\n    description: lives in the second\n'
        '  - id: in_neither\n    description: lives in neither, and must be reported\n'
    )
    findings = R.Resolver().verify_source(str(root / 'Absent.yaml'))
    return str(root / 'Absent.yaml'), {
        f['message'].split("'")[1]: (f['check'], f['severity']) for f in findings}


_, verdicts = absent_project()

# 'command-name' is there only inside quotes — that is how a CLI command exists, and it counts
for present in ('declared_py', 'declared_js', 'declared_method', 'declared_sh', 'command-name'):
    check(f'verify_source/{present}', present not in verdicts,
          f'a name that is in the sources was reported: {verdicts.get(present)}')

check('verify_source/bytecode', verdicts.get('deleted_long_ago') == ('absent-function', 'error'),
      f'compiled bytecode must not satisfy the check: got {verdicts.get("deleted_long_ago")}')

check('verify_source/src-list', verdicts.get('in_neither') == ('absent-function', 'error'),
      f'a list-form src must be read, not treated as no path at all: '
      f'got {verdicts.get("in_neither")}')

uses = r.term_uses(project, 'Widget')
check('term_uses', 'Gadget' in str(uses), f'got {uses}')

schema = r.term_schema(project, 'Gadget')
check('term_schema', any(m['member'] == 'colour' for m in schema), f'got {schema}')

queried = r.query_terms(project, extending='Widget')
check('query_terms', queried == ['Gadget'], f'got {queried}')

entries = r.slot_entries(project, 'Widget', 'properties')
check('slot_entries', [e['id'] for e in entries] == ['colour'], f'got {entries}')

# --- editing -----------------------------------------------------------------
r.set_field(project, 'Widget#colour', 'description', 'the colour, restated')
check('set_field', 'restated' in Path(project).parent.joinpath('Fixture/Widget.yaml').read_text())

# an entry's own `id` sits on the `- ` line, two columns left of its siblings; setting it must
# rewrite that line rather than append a second `id:` under it
r.set_field(project, 'Widget#colour', 'id', 'hue')
_widget = Path(project).parent / 'Fixture' / 'Widget.yaml'
check('set_field/id', _widget.read_text().count('id: hue') == 1
      and 'id: colour' not in _widget.read_text(), _widget.read_text())
r.set_field(project, 'Widget#hue', 'id', 'colour')

r.add_entry(project, 'Widget#properties', 'weight', {'description': 'how heavy: quite'})
widget = Path(project).parent / 'Fixture' / 'Widget.yaml'
check('add_entry', 'weight' in widget.read_text() and '"how heavy: quite"' in widget.read_text(),
      'a value with a colon must be quoted')
check('add_entry/duplicate', 'refused' in r.add_entry(project, 'Widget#properties', 'weight', {}))

r.remove_element(project, 'Widget#weight')
check('remove_element', 'weight' not in widget.read_text())

r.add_rule(project, 'Widget', 'guidelines', 'A widget spins clockwise.')
check('add_rule', 'clockwise' in widget.read_text())

r.move_rule(project, 'Widget', 'guidelines', 'ai_instructions', 'clockwise')
check('move_rule', re.search(r'ai_instructions:\n  - A widget spins clockwise\.', widget.read_text())
      is not None, widget.read_text())

r.remove_rule(project, 'Widget', 'ai_instructions', 'clockwise')
check('remove_rule', 'clockwise' not in widget.read_text())

r.create_term(project, 'Sprocket', 'A sprocket.', '@Term')
sprocket = Path(project).parent / 'Fixture' / 'Sprocket.yaml'
check('create_term', sprocket.is_file() and 'extends: @Term\n' in sprocket.read_text(),
      sprocket.read_text() if sprocket.is_file() else 'not created')

r.rename_term(project, 'Sprocket', 'Cog')
check('rename_term', (Path(project).parent / 'Fixture' / 'Cog.yaml').is_file() and not sprocket.exists())

r.remove_term(project, 'Cog')
check('remove_term', not (Path(project).parent / 'Fixture' / 'Cog.yaml').exists())

# --- workspace registry (redirected settings file) ---------------------------
r.create_workspace('fixture-ws')
check('create_workspace', 'fixture-ws' in R._load_settings()['workspaces'])

r.use_workspace('fixture-ws')
check('use_workspace', R._load_settings()['active_workspace'] == 'fixture-ws')

registered = r.add_project(project)   # reads `repository:` out of the project itself
check('add_project', registered == 'https://example.invalid/fixture'
      and registered in R._load_settings()['workspaces']['fixture-ws']['projects'],
      f'got {registered}')

check('list_projects', 'fixture-ws' in str(r.list_projects()))

r.remove_project('https://example.invalid/fixture')
check('remove_project', 'https://example.invalid/fixture'
      not in R._load_settings()['workspaces']['fixture-ws']['projects'])

# --- report ------------------------------------------------------------------
public = set(re.findall(r'^    def ([a-z][a-z_0-9]*)',
                        (Path(__file__).resolve().parent / 'src' / 'ducktools'
                         / 'resolver.py').read_text(), re.M))
untested = sorted(public - {c.split('/')[0] for c in checked})
shutil.rmtree(TMP, ignore_errors=True)

if untested:
    failures.append(f'no check for: {", ".join(untested)}')
    print(f'\nuncovered methods: {", ".join(untested)}')
print('\nall operations pass' if not failures else '\n' + '\n'.join(failures))
sys.exit(1 if failures else 0)
