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
        # @Software, not @Term: the fixture lists it in `software:` and gives it `src:` and
        # `functions:`, which @DesignPattern declares and @Term does not.
        'extends: @Software\n'
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
# The fixture's own reachable term is listed with its description; the framework it uses is not, and
# comes back once, by name, under the project it belongs to.
_own = {t['name'] for t in loaded['terms']}
_vocab = {v['project']: v['terms'] for v in loaded['vocabulary']}
check('load_project/vocabulary',
      _own == {'Widget'} and list(_vocab) == ['Duckspec'] and 'Term' in _vocab['Duckspec'],
      f'own={sorted(_own)} vocabulary={ {k: len(v) for k, v in _vocab.items()} }')

terms = r.list_terms(project)
check('list_terms', any(t['name'] == 'Widget' for t in terms), f'got {terms}')

blocks = r.load_terms(project, ['Widget'])
check('load_terms', 'turns the widget once' in str(blocks))

# the chain is inlined because a term is incomplete without it — its inherited members are its
# own. Everything else it mentions is a neighbour, named rather than loaded: following mentions
# returned the whole corpus on every call, because a guideline citing @Key as an example of a
# name taken on GitHub is not a dependency on @Key
_names = [b['name'] for b in blocks]
check('load_terms/chain-only',
      _names == ['Widget', 'Software', 'DesignPattern', 'Term'], f'got {_names}')
_w = [b for b in blocks if b['name'] == 'Widget'][0]
check('load_terms/references-named',
      'references' in _w and 'Gadget' not in _names, f'got {_w.get("references")}')

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

# the workflow is not carried by load_project — nothing is in hand when a project is opened — so
# the overview names it and load_workflow fetches it. The chain, not the reference graph: following
# @TermName mentions out of a workflow's prose reached 57 terms and three times the load_project
# output, which is the opposite of a targeted call
_lp = r.load_project(project)
check('load_project/workflow-hint',
      _lp['workflow']['name'] is None and 'no `workflow:`' in _lp['workflow']['note'],
      f"got {_lp['workflow']}")
check('load_workflow/unconfigured', 'error' in r.load_workflow(project),
      f'got {r.load_workflow(project)}')

_root = Path(project)
_before = _root.read_text()
_root.write_text(_before + 'workflow:\n  type: @GitHubWorkflow\n  main_branch: trunk\n')
_wf = r.load_workflow(project)
check('load_workflow',
      _wf.get('name') == 'GitHubWorkflow'
      and 'main_branch: trunk' in _wf['configuration']
      and [t['name'] for t in _wf['terms']] == ['GitHubWorkflow', 'DuckWorkflow', 'DesignPattern', 'Term']
      and 'rules' in _wf['terms'][0],
      f"got {_wf.get('name')} / {[t['name'] for t in _wf.get('terms', [])]}")
check('load_project/workflow-hint-set',
      r.load_project(project)['workflow']['name'] == 'GitHubWorkflow')
_root.write_text(_before)

check('verify_project', r.verify_project(project) == [], f'got {r.verify_project(project)}')
check('verify_source', r.verify_source(project) == [], f'got {r.verify_source(project)}')

# a term's own top-level fields went unchecked against its extends chain, so `extends:` bought
# nothing at that level: a term could invent a field — or a whole slot no type describes, holding
# any number of unread entries — and verify_project still reported an empty list
_w = Path(project).parent / 'Fixture' / 'Widget.yaml'
_orig = _w.read_text()
_w.write_text(_orig + 'invented_field: nothing declares this\n')
_top = [f for f in r.verify_project(project)
        if f['check'] == 'unknown-field' and 'invented_field' in f['message']]
check('verify_project/top-level-field', len(_top) == 1, f'got {r.verify_project(project)}')
_w.write_text(_orig)

# a `uses:` entry that resolved to nothing was skipped in silence, so the project it named simply
# left the term map and the report blamed the dangling references that followed. The two ways it
# fails are different in kind: a wrong relative path is a defect in the spec, while a URL missing
# from the workspace means the spec is right and this machine has not cloned that project
_root = Path(project)
_saved = _root.read_text()
_root.write_text(_saved.replace('uses:\n', 'uses:\n  - ./nowhere.yaml\n'
                                '  - https://example.invalid/never-registered\n', 1))
_bu = [f for f in r.verify_project(project) if f['check'] == 'broken-uses']
check('verify_project/broken-uses',
      sorted((f['severity'], 'nowhere' in f['message']) for f in _bu)
      == [('error', True), ('warning', False)], f'got {_bu}')
_root.write_text(_saved)

# a file with a `uses:` block is still a term: when the broken-uses check was added it ended in a
# `continue`, and every project file — the ones every other term hangs off — went unchecked for
# dangling references from then on while the report stayed empty
_root.write_text(_saved + 'goals:\n  - Mention @NoSuchTermAnywhere\n')
_dr = [f for f in r.verify_project(project) if f['check'] == 'dangling-ref']
check('verify_project/refs-in-project-file',
      any('NoSuchTermAnywhere' in f['message'] for f in _dr), f'got {_dr}')
_root.write_text(_saved)

# a step's operation has to be something that can be run. A reference that merely resolves is not
# enough: a function or a component describing a file resolves fine, and a step pointing at one
# looks implemented while nothing could perform it
_flow = Path(project).parent / 'Fixture' / 'Flow.yaml'
_flow.write_text('description: A workflow for the operation checks.\nextends: @DuckWorkflow\nsteps:\n'
                 '  - id: good\n    when: always\n    operation: "@Git#push(branch=main)"\n'
                 '  - id: not_a_recipe\n    when: always\n    operation: "@Widget#spin"\n'
                 '  - id: bad_argument\n    when: always\n    operation: "@Git#push(bogus=1)"\n'
                 '  - id: bad_form\n    when: always\n    operation: "@Git#push#deeper"\n')
_ops = [f for f in r.verify_project(project) if f['check'] == 'broken-operation']
_hit = sorted(f["message"].split("'")[1] for f in _ops)
check('verify_project/broken-operation',
      _hit == ['bad_argument', 'bad_form', 'not_a_recipe'], f'got {_hit}')
_flow.unlink()

# add_entry once wrote `type: "@X"` and every parser of `type:` expected it bare, so the slot read
# as untyped and unknown-field skipped every entry in it — a workflow's steps went unchecked. A term
# reference is now written bare, and a quoted one is still recognised
r.add_entry(project, 'Widget#properties', 'parts', {'type': '@Gadget', 'description': 'its parts'})
check('add_entry/bare-term-ref', '    type: @Gadget\n' in _w.read_text(), _w.read_text())
_w.write_text(_w.read_text().replace('    type: @Gadget\n', '    type: "@Gadget"\n')
              + 'parts:\n  - id: cog\n    invented: nothing declares this\n')
_q = [f for f in r.verify_project(project) if f['check'] == 'unknown-field' and 'invented' in f['message']]
check('verify_project/quoted-type', len(_q) == 1, f'got {r.verify_project(project)}')
_w.write_text(_orig)

# when the chain cannot be resolved, _schema saw only part of the vocabulary; unknown-extends
# reports that, and the top-level check must stay quiet rather than bury it under one finding per
# inherited field the term legitimately carries
_orphan = Path(project).parent / 'Fixture' / 'Orphan.yaml'
_orphan.write_text('description: Extends a term that is not in the map.\n'
                   'extends: @NotAThing\nplatform: python\n')
_of = r.verify_project(project)
check('verify_project/broken-chain-quiet',
      any(f['check'] == 'unknown-extends' for f in _of)
      and not any(f['check'] == 'unknown-field' and f['term'] == 'Orphan' for f in _of),
      f'got {_of}')
_orphan.unlink()


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

# a function with arguments could not be added at all: fields were written as scalars only, so a
# nested block — `arguments:` with entries of its own — had to be written by hand after the call
r.add_entry(project, 'Widget#functions', 'wobble', {
    'description': 'tilts the widget',
    'arguments': [{'id': 'angle', 'description': 'how far: in degrees'}, {'id': 'axis'}],
    'notes': ['first', 'second'],
})
_arg = r.resolve_path(project, 'Widget#wobble#arguments#angle')
check('add_entry/nested',
      _arg is not None and '"how far: in degrees"' in _arg['content']
      and r.resolve_path(project, 'Widget#wobble#axis') is not None
      and '    notes:\n      - first\n      - second\n' in widget.read_text(),
      widget.read_text())
r.remove_element(project, 'Widget#functions#wobble')

r.remove_element(project, 'Widget#weight')
check('remove_element', 'weight' not in widget.read_text())

r.add_item(project, 'Widget#guidelines', 'A widget spins clockwise.')
check('add_item', 'clockwise' in widget.read_text())

# the list is addressed by path, not by term plus block name, so the same operation reaches a
# recipe's instructions two levels down — which nothing could edit before
r.add_item(project, 'Widget#spin#instructions', 'Turn it once and stop.')
check('add_item/nested', 'Turn it once and stop.' in widget.read_text(), widget.read_text())

# `after` puts an item where it belongs rather than at the end
r.add_item(project, 'Widget#guidelines', 'A widget is round.', after='clockwise')
check('add_item/after',
      re.search(r'clockwise\.\n  - A widget is round\.', widget.read_text()) is not None,
      widget.read_text())

# rewording must not relocate: remove-plus-add would have moved this to the end of the block, past
# the guideline it was deliberately written before
r.set_item(project, 'Widget#guidelines', 'clockwise', 'A widget spins the other way.')
check('set_item',
      re.search(r'other way\.\n  - A widget is round\.', widget.read_text()) is not None
      and 'clockwise' not in widget.read_text(), widget.read_text())

# a block that does not exist yet is created rather than refused
r.add_item(project, 'Widget#goals', 'Spin reliably.')
check('add_item/creates-block',
      re.search(r'^goals:\n  - Spin reliably\.', widget.read_text(), re.M) is not None,
      widget.read_text())

r.move_item(project, 'Widget#guidelines', 'Widget#ai_instructions', 'other way')
check('move_item', re.search(r'ai_instructions:\n  - A widget spins the other way\.',
                             widget.read_text()) is not None, widget.read_text())

r.remove_item(project, 'Widget#ai_instructions', 'other way')
check('remove_item', 'other way' not in widget.read_text())

# moving takes the item's text out and writes it back, so the quoting has to be undone on the way
# out: stripping only the outer quotes left the escapes inside, and writing escaped them again
_said = 'Say "spin": never "rotate"'
r.add_item(project, 'Widget#guidelines', _said)
r.move_item(project, 'Widget#guidelines', 'Widget#ai_instructions', 'never')
_moved = r.resolve_path(project, 'Widget#ai_instructions')['content']
check('move_item/quoted', R.Resolver._item_line(_said, 2).rstrip('\n') in _moved, _moved)
r.remove_item(project, 'Widget#ai_instructions', 'never')

r.create_term(project, 'Sprocket', 'A sprocket.', '@Term')
sprocket = Path(project).parent / 'Fixture' / 'Sprocket.yaml'
check('create_term', sprocket.is_file() and 'extends: @Term\n' in sprocket.read_text(),
      sprocket.read_text() if sprocket.is_file() else 'not created')

r.rename_term(project, 'Sprocket', 'Cog')
check('rename_term', (Path(project).parent / 'Fixture' / 'Cog.yaml').is_file() and not sprocket.exists())

r.remove_term(project, 'Cog')
check('remove_term', not (Path(project).parent / 'Fixture' / 'Cog.yaml').exists())

# --- front ends --------------------------------------------------------------
# The methods above can all be right while what a reader sees is wrong: load_project returned a
# pointer to the workflow that neither front end printed, and nothing here ran either of them.
# Every CLI command and every MCP tool runs once, in an order that leaves the fixture as it was,
# and a command or tool this section does not run is itself a failure.
import contextlib, io, json  # noqa: E401,E402
from ducktools import cli, mcp_server  # noqa: E402

_root_file = Path(project)
_before = _root_file.read_text()
_root_file.write_text(_before + 'workflow:\n  type: @GitHubWorkflow\n')

# (command, argv after the command, tool, arguments, text the output must contain — or a tuple
# of alternatives, for the checks whose verdict depends on what earlier sections left behind)
_P = {'project_path': project}
FRONT = [
    ('load-project', [project], 'load_project', _P, '## Workflow'),
    ('load-workflow', [project], 'load_workflow', _P, '# @GitHubWorkflow'),
    ('list-terms', [project], 'list_terms', _P, '@Widget'),
    ('load-terms', [project, 'Widget'], 'load_terms', {**_P, 'term_names': 'Widget'}, '--- @Software'),
    ('grep', [project, 'colour'], 'grep_terms', {**_P, 'query': 'colour'}, 'Widget#properties#colour'),
    ('resolve-path', [project, 'Widget#spin'], 'resolve_path', {**_P, 'ref': 'Widget#spin'}, 'turns the widget'),
    ('verify-project', [project], 'verify_project', _P, ('no findings', '| Severity |')),
    ('verify-source', [project], 'verify_source', _P, ('no findings', '| Severity |')),
    ('uses', [project, 'Widget'], 'term_uses', {**_P, 'term_name': 'Widget'}, '@Gadget'),
    ('schema', [project, 'Widget'], 'term_schema', {**_P, 'term_name': 'Widget'}, 'colour'),
    ('query', [project, '--extending', 'Widget'], 'query_terms', {**_P, 'extending': 'Widget'}, '@Gadget'),
    ('entries', [project, 'Widget#properties'], 'slot_entries',
     {**_P, 'term_name': 'Widget', 'slot': 'properties'}, 'colour'),
    ('create-term', [project, 'Gear', 'A gear.'], 'create_term',
     {**_P, 'term_name': 'Cam', 'description': 'A cam.'}, 'created'),
    ('set', [project, 'Gear', 'description', 'A toothed gear.'], 'set_field',
     {**_P, 'ref': 'Cam', 'field': 'description', 'value': 'A lobed cam.'}, 'replaced description'),
    ('add', [project, 'Widget#properties', 'size', 'description=how big'], 'add_entry',
     {**_P, 'ref': 'Widget#properties', 'entry_id': 'weight', 'fields': {'description': 'how heavy'}}, 'added'),
    ('remove', [project, 'Widget#properties#size'], 'remove_element',
     {**_P, 'ref': 'Widget#properties#weight'}, 'removed'),
    ('add-item', [project, 'Gear#guidelines', 'Mesh cleanly.'], 'add_item',
     {**_P, 'ref': 'Cam#guidelines', 'text': 'Lift smoothly.'}, 'added'),
    ('set-item', [project, 'Gear#guidelines', 'Mesh', 'Mesh quietly.'], 'set_item',
     {**_P, 'ref': 'Cam#guidelines', 'match': 'Lift', 'text': 'Lift gently.'}, 'replaced'),
    ('move-item', [project, 'Gear#guidelines', 'Gear#ai_instructions', 'Mesh'], 'move_item',
     {**_P, 'from_ref': 'Cam#guidelines', 'to_ref': 'Cam#ai_instructions', 'match': 'Lift'}, 'added'),
    ('remove-item', [project, 'Gear#ai_instructions', 'Mesh'], 'remove_item',
     {**_P, 'ref': 'Cam#ai_instructions', 'match': 'Lift'}, 'removed'),
    ('rename-term', [project, 'Gear', 'Cog'], 'rename_term',
     {**_P, 'old_name': 'Cam', 'new_name': 'Eccentric'}, '->'),
    ('remove-term', [project, 'Cog'], 'remove_term', {**_P, 'term_name': 'Eccentric'}, 'removed'),
    ('create-workspace', ['front-ws'], 'create_workspace', {'name': 'front-ws-mcp'}, 'created workspace'),
    ('use-workspace', ['front-ws'], 'use_workspace', {'name': 'front-ws'}, 'active workspace'),
    ('add-project', [project], 'add_project', _P, 'example.invalid/fixture'),
    ('list-projects', [], 'list_projects', {}, 'front-ws'),
    ('remove-project', ['https://example.invalid/fixture'], 'remove_project',
     {'repository': 'https://example.invalid/fixture'}, 'example.invalid/fixture'),
]


def _run_cli(argv: list[str]) -> str:
    out = io.StringIO()
    old = sys.argv
    sys.argv = ['ducktools'] + argv
    try:
        with contextlib.redirect_stdout(out):
            try:
                cli.main()
            except SystemExit:
                pass
    finally:
        sys.argv = old
    return out.getvalue()


def _shows(expect, text: str) -> bool:
    return any(e in text for e in (expect if isinstance(expect, tuple) else (expect,)))


for command, argv, tool, arguments, expect in FRONT:
    printed = _run_cli([command] + argv)
    check(f'cli/{command}', _shows(expect, printed), printed[:300])
    try:
        returned = mcp_server._call(tool, arguments)
    except Exception as e:  # noqa: BLE001 — any exception is the finding
        returned = f'raised {e!r}'
    check(f'mcp/{tool}', _shows(expect, returned), returned[:300])

check('cli/help', 'load-workflow' in _run_cli(['help']) and 'usage: ducktools add-item'
      in _run_cli(['help', 'add-item']))

# serve: the stdio loop itself, fed an initialize and a tools/list as a client would send them
_stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
                     '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n')
_out = io.StringIO()
_old_stdin = sys.stdin
sys.stdin = _stdin
try:
    with contextlib.redirect_stdout(_out):
        mcp_server.run_server()
finally:
    sys.stdin = _old_stdin
_replies = [json.loads(l) for l in _out.getvalue().splitlines() if l.strip()]
_listed = {t['name'] for t in _replies[1]['result']['tools']} if len(_replies) == 2 else set()
check('cli/serve', len(_replies) == 2 and _replies[0]['result']['serverInfo']['name'] == 'ducktools',
      _out.getvalue()[:300])

_commands = {c[0] for _, group in cli._COMMAND_GROUPS for c in group}
_ran_cli = {c.split('/')[1] for c in checked if c.startswith('cli/')}
_ran_mcp = {c.split('/')[1] for c in checked if c.startswith('mcp/')}
check('front-ends/all-commands', _commands <= _ran_cli, f'not run: {sorted(_commands - _ran_cli)}')
check('front-ends/all-tools', _listed <= _ran_mcp, f'not run: {sorted(_listed - _ran_mcp)}')
_root_file.write_text(_before)

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
