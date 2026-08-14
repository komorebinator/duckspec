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

resolved = r.resolve_path(project, 'Widget#spin')
check('resolve_path', resolved is not None and 'turns the widget once' in resolved['content'])

check('verify_project', r.verify_project(project) == [], f'got {r.verify_project(project)}')
check('verify_source', r.verify_source(project) == [], f'got {r.verify_source(project)}')

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
