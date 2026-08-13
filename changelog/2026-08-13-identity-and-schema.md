# Identity and schema rework

Planned 2026-08-13. Not yet applied. Touches four repositories that must migrate in one pass:
`duckspec`, `borshevik`, `borshevik-app-search`, `borshevik-workspace-manager` — the extensions
reference duckspec by URL and break against a changed format.

## Target structure

1. **`id`** — the identifier of an object. Unique **among siblings** (within one list block under
   one parent), not within the file. The only thing `#`-paths address. For entries in `functions:`
   and `fields:` it must match the identifier in the source verbatim.
2. **`name`** — a short human-readable name. Not an identifier, never used for addressing.
3. **`description`** — the prose description.
4. **Nothing is required.** No `required:` on any field, anywhere.
5. **One declaration block: `properties:`.** `contains:` disappears. The *meaning* is inherited
   from `contains:` ("named slots that things of this type fill"); only the name comes from
   `properties:`.
6. **A term's identity is its filename.** The `name:` field in a term file is not identity — the
   resolver already builds its term map from `f.stem`.
7. **Every term except `@Term` declares `extends:` explicitly**; `@Term` is the root. `id`, `name`
   and `description` are declared once on `@Term` and inherited; element types declare only what
   they add.
8. **Slot declarations name their element type explicitly** via `type: @X`, replacing the current
   inference from the slot's prose description.

## Checker principle

> `verify_project` checks that what is written is true and consistent with itself. It does not
> check that everything was written.

Completeness is the author's business. Truth is the tool's.

## Why sibling uniqueness, not file uniqueness

`run` appears 7 times in `ImageBuild.yaml` — one per script. `project_path` appears 27 times in
`DuckToolsApp.yaml` — one per function that takes it. `on_click` appears 6 times in
`AppManagerApp.yaml` — one per button. These are correctly named; they simply live under different
parents. Measured: 825 names across 341 blocks, **zero** sibling-uniqueness violations; 11 files
violate file-wide uniqueness.

`#`-paths resolve by narrowing step by step: `@ImageBuild#cleanup#run` first narrows to the
`cleanup` block, then searches `run` inside it only. Sibling uniqueness is exactly the condition
that each step resolves to one entry.

## Transformations

| # | What | Scale |
|---|------|-------|
| T1 | `- name:` → `- id:` in every list entry | **990** (461 duckspec, 373 borshevik, 156 extensions) |
| T2 | Top-level `name:` in term files — **open, see below** | 145 files |
| T3 | Merge `contains:` into `properties:` | 21 blocks, 89 entries, 13 terms carry both |
| T4 | Delete duplicate `id`/`description` declarations from the nine element types | 16 entries |
| T5 | Move three descriptions that carry content into `guidelines:` | `@Function#description`, `@SoftwareComponent#name`, `@SoftwareComponent#description` |
| T6 | Remove `required:` everywhere | 20 declarations + the `required` property on `@Property` |
| T7 | Rewrite `@Term`: declare `id`, `name`, `description`; drop `contains` from its own slot list | 1 |
| T8 | `@Duckspec` CamelCase guideline — restate it about the **filename**, not `name:` | 1 |
| T9 | `@Term` path-reference guideline — `- name: <segment>` → `- id: <segment>` | 1 |
| T10 | Recipes and prose that mention `name` textually | `create_term`, `create_project`, references to `lists` entries |
| T11 | Resolver — delete | checks `missing-required-property`, `missing-required-field`; functions `_parse_required`, `_inherited_required` |
| T12 | Resolver — rewrite for `id:` and one block | `_NAME`, `_extract_named_block`, `_member_names`, `_PROPERTIES_BLOCK`/`_CONTAINS_BLOCK`, checks `unparsed-term`, `redeclared-member`; delete `name-mismatch` |
| T13 | Extensions: move `terms_folder` out of `settings:` | 2 files |
| T14 | Regenerate AGENTS.md and README | 4 AGENTS.md + project READMEs |
| T15 | `_find_named_blocks` + new check `ambiguous-path-ref` | resolver + `@DuckToolsApp` |
| T16 | `list_projects` reads the project name from `name:` (resolver line ~758) — switch to the filename stem | 1 |
| T17 | Explicit `type: @X` on slot declarations; new **entry-conformance** check | 21 slot declarations |
| T18 | `@ElementState` → `when` + `property_changes` mapping; rewrite 11 states in borshevik | 1 term + 11 sites |
| T19 | `@Key extends @File`; rename `@Key#format` → `encoding` | 2 terms |

### Explicitly kept (do not delete)

`_slot_element_types`, `_slot_items`, `_item_name`, `_SLOT_DECL`, `_has_field`. They lose their
only current caller when `missing-required-field` goes, but they are the input to
`missing-identifier` in the source-checking pass, and `_slot_element_types` is the only thing that
links a slot to its element type.

## Entry-conformance check (T17)

An entry may only use fields declared by its element type. The schema is composed:

1. base — the slot's declared element type, plus its ancestors;
2. plus the schema of the term named in the entry's own `type:` (this is what makes `type:`
   meaningful — a `components:` entry with `type: @ConfigFile` may carry `format:`).

Measured on borshevik: **266 findings without composition, 12 with it**. Of those 12, eleven were
`states:` entries carrying `disabled`/`visible`/`graphic`, resolved by T18. The twelfth is a real
defect — see T19.

## Decisions and their reasons

- **Nothing is required.** `required:` fired four times during design and every time the discussion
  was "is this actually required?" rather than "let's fill it in" — base terms and `goals`,
  `terms_folder` on a project with no terms, `description` on a button labelled "Retry". A rule that
  keeps needing exceptions is the wrong rule. Seven filler button descriptions were the cost.
- **No `overrides` mechanism.** A state assigning `disabled: true` is a *value* assignment, not a
  schema re-declaration — but rather than teach the checker about owner fields, states now carry a
  `property_changes:` mapping, following the precedent of `settings:`. No new term, no new concept.
- **Slot element types declared, not inferred.** Inference read the first `@TermName` out of the
  slot's prose, which already misfired once (`@Term`'s `lists` slot was read as holding `@Term`s)
  and needed a self-reference workaround. An explicit `type:` removes the heuristic.
- **`@Key extends @File`.** `cosign-key` (`@PublicKey`) carries `path: /etc/pki/containers/cosign.pub`
  alongside `src: cosign.pub`, but `@Key` extends `@DesignPattern` and declares no `path`. `@File`
  declares exactly that. `@Key#format` ("storage format, PEM/DER") then collides with `@File#format`
  ("file format or MIME type") and becomes `encoding`, which is more accurate anyway.

## Order of work

0. **Commit the current state first.** Nothing is committed in either repository (~50 files, two
   new terms, three code files). T1 rewrites 990 entries by script; without a clean point to return
   to there is no way back. `borshevik` also has an unresolved submodule (`D duckspec`,
   `MD .gitmodules`) to untangle before committing.
1. T13 — start from a green run.
2. T11 — drop the completeness checks; the tree stays green with less code.
3. T12, first half — the resolver accepts both `id:` and `name:`.
4. T1–T10, T17–T19 — the specs, verifying after each step.
5. T12, second half — drop `name:` support for entries.
6. T15, T16, T14.

## Still open

**T2** — the top-level `name:` in term files. In all 145 files it repeats the filename verbatim, so
it currently carries no information. Either delete it (recommended — an author adds a short human
name where one is actually useful) or write 145 human-readable names.

## Verification

Run after every step; all four projects must stay green:

```sh
PYTHONPATH=ducktools/src python3 -m ducktools list-projects \
  | grep -oE '/[^ |]*\.yaml' | sort -u \
  | while read f; do printf '%-32s ' "$(basename $f)"; \
      PYTHONPATH=ducktools/src python3 -m ducktools verify-project "$f" | tail -1; done
```

---

# Execution log — duckspec, 2026-08-13

Everything below is applied to the `duckspec` repository only. `borshevik` and the two extension
repositories are untouched and still write `- name:`; the resolver accepts both keys for the
duration of the migration, so all four projects verify clean throughout.

## Applied, in the order it happened

**T5 first, before T4.** The three descriptions that carried content had to move before the
declarations holding them were deleted. `@Function` gained a guideline saying a description carries
the algorithm — logic, edge cases, expected behaviour — and that a restatement of the id is not a
description. `@SoftwareComponent` gained two: one on component ids being local path identifiers
addressable with `component#element`, one on declarative components (`@ConfigFile`, `@Patch`) being
fully described by their description alone while components with real behaviour add detail through
their other slots.

**T4 — 16 duplicate declarations removed.** `name` and `description` deleted from the `properties:`
of `@Recipe`, `@Test`, `@Property`, `@Function`, `@Field`, `@Signal`, `@SoftwareComponent` (2 each),
and `@Workspace`, `@ElementState` (1 each — they declared only `name`). All nine now inherit both
from `@Term` instead of restating them.

**T6 — 20 `required:` declarations removed**, plus the `required` property on `@Property` itself.
Nothing in the framework is required any more.

**T3 — 20 `contains:` blocks merged into `properties:`.** Entries were appended to an existing
`properties:` block where one existed (13 terms carried both) and the block renamed in place where
it did not. No `contains:` block remains anywhere in duckspec.

**T7 — `contains` dropped from `@Term`'s own slot list.** There is no block by that name to declare.

**T11 — completeness checks removed.** `missing-required-property` and `missing-required-field`
deleted from `verify_project`, together with `_parse_required` and `_inherited_required`. Kept, as
planned: `_slot_element_types`, `_slot_items`, `_item_name`, `_SLOT_DECL`, `_has_field`.

> Went wrong: the regex-based removal left two orphaned loop headers with no bodies, and the module
> stopped importing with an `IndentationError`. Fixed with a targeted edit over the exact region.
> Lesson for the remaining repositories — remove code blocks by explicit boundaries, not by regex.

**T12, first half — one block instead of two.** `_CONTAINS_BLOCK` deleted; `redeclared-member` now
compares a single flat member list per term rather than `property` and `slot` separately;
`_slot_element_types` reads `properties:`.

**`@DuckToolsApp` re-synchronised.** Descriptions of the two removed checks and two removed
functions deleted; `redeclared-member` and `_slot_element_types` rewritten to match what the code
now does.

**T19 — `@Key extends @File`,** gaining `path` and `permissions`; `@Key#format` renamed to
`encoding` so it no longer collides with `@File#format`. This is what `cosign-key` needed: it is a
public key *and* an installed file, with `src: cosign.pub` in the repository and
`path: /etc/pki/containers/cosign.pub` in the image.

**T18 — `@ElementState` rebuilt around `property_changes`.** It now declares `when` and
`property_changes` (a mapping of property name to value, following the precedent of `settings:`).
Its description and first guideline were rewritten: a state *assigns values*, it does not declare
fields — the property must already be declared by the owning element.

**T17 — explicit slot types.** `type: @X` added to 15 slots across 8 terms, and
`_slot_element_types` rewritten to read the declaration instead of parsing the slot's prose.

> Worth recording why: the old inference read the first `@TermName` out of a slot's description and
> could not tell a list slot from a scalar field whose value references a term. It produced
> `readme -> @Project`, `src -> @DuckspecProject`, `when -> @Field`, `graphic -> @Graphic`,
> `type -> @DesignPattern` — junk in every case where the field holds a value rather than entries.
> After the change the map contains exactly the 8 terms that really own list slots.

**T1 — 438 entries renamed to `- id:`.** No `- name:` entry remains in duckspec.

> Went wrong twice. First sweep walked `duckspec/**` only and silently skipped the root
> `Duckspec.yaml` and everything under `ducktools/` — 218 renamed instead of 438; caught by noticing
> `lists:` and `recipes:` still on `name:` in the root file. Then `_parse_recipes` was switched to
> key `id` while the CLI and MCP formatters still read `r['name']`, so `load-project` crashed with a
> `KeyError`; fixed by giving `_parse_dict_list_block` a separate `key_pattern` argument, so recipes
> are matched on either key and returned under `name` for callers.

**T8 — the CamelCase guideline restated about the filename.** A term is identified by its filename;
that filename is what `@<TermName>` resolves against, and the `name:` field inside the file is a
short human-readable label, not the identity.

**T9 — `@Term`'s path-reference guideline** now reads `- id: <segment>`, and the resolver function
descriptions in `@DuckToolsApp` follow.

## State

All four projects report `no findings`. Every CLI command passes: `load-project`, `list-terms`,
`load-terms`, `resolve-path`, `grep`, `list-projects`, `verify-project`. The resolver accepts `id:`
and `name:` on entries; duckspec writes `id:` exclusively, the other three still write `name:`.

## Noticed, not acted on

`@DuckTools` has recipe arguments literally called `name` (`create_workspace`, `use_workspace`) —
the id of the argument is `name` and its value is a workspace name. Correct but ambiguous to read;
`workspace_name` would be clearer.

## Remaining

T1 for borshevik (373 entries) and the two extensions (156); T2 (the open question — the top-level
`name:` in 145 term files); T12 second half (drop `name:` acceptance once the others have migrated);
T13, T14, T15, T16; and the entry-conformance check itself — `type:` is now declared and read, but
nothing verifies entries against it yet.

## Second pass — T2, T16 and the conformance check

**T2 — the top-level `name:` deleted from all 103 duckspec files.** It repeated the filename
verbatim in every one of them, so it carried nothing: identity was already the filename, and
`@<TermName>` references have always resolved through `f.stem`. `@Term` still declares `name`, but
redescribed as what it actually is — a short human-readable label, never an identifier, worth
setting only where a display name differs usefully from the id (a project, a piece of software) and
pointless where it would restate it.

Consequences handled in the resolver:

- `unparsed-term` narrowed to `description:` alone. It stays as a guard against regex-based parsing
  silently reading a malformed file as empty — verified by deleting `@Goal`'s description and
  watching it fire.
- `name-mismatch` deleted; with identity in the filename there is nothing left to compare. The
  `_NAME` regex went with it.
- **T16 done in the same pass** — `list_projects` used to read the project's display name from the
  `name:` field and would have shown nothing for every project. It now uses the filename stem.
  Term listings were never affected: `_build_term_map` and `_walk_terms` have always keyed on
  `f.stem`.

**Entry-conformance check implemented** — `unknown-field`. For every slot that declares a `type:`,
each entry may only set fields that some type it has declares. The schema is composed exactly as
planned: the slot's element type plus its ancestors, plus the type named in the entry's own `type:`
plus its ancestors. A new private method `_schema` walks the `extends` chain collecting
`properties:` ids; it returns an empty set for an unknown term, which makes the caller skip that
slot rather than report every field in it.

Results: duckspec clean; borshevik reports **11**, and they are exactly the `states:` entries still
written the old way — 7 × `disabled`, 3 × `visible`, 1 × `graphic`. Not new defects: this is T18
waiting for borshevik. The count matches the measurement taken during design, which is the outcome
the check was built to produce.

Negative test: adding `bogus_key:` to a `properties:` entry in `@Term` is reported as
`unknown-field` naming both composed types; removing it returns the run to clean.

## State after the second pass

Duckspec is fully migrated. No `- name:` entries, no top-level `name:`, one declaration block,
nothing required, every term rooted at `@Term`, slot types declared rather than inferred, and the
conformance check live. All six CLI commands pass. Borshevik and both extensions still write
`name:` and verify clean apart from the 11 pending states.

## Remaining after the second pass

For borshevik and the two extensions: T1 (373 + 156 entries), T2, T13, T18 (11 states). Then T12's
second half — drop `name:` acceptance from the resolver once nothing writes it — followed by T15
(`ambiguous-path-ref`) and T14 (AGENTS.md, READMEs).

## Spec re-synchronised after the second pass

Caught by asking, not by a check — the same failure as the first pass. Four drifts:

- `_schema` existed in code and nowhere in the spec;
- `verify_project`'s description still listed `name-mismatch` and still said `unparsed-term`
  requires `name:`, and did not mention `unknown-field` at all;
- `_parse_dict_list_block` had gained a `key_pattern` argument, undocumented — with the reason it
  exists (recipes parse from either `id:` or `name:` during the migration while callers keep reading
  results under `name`);
- `list_projects` no longer reads a project's display name from the file.

All four fixed; the code/spec comparison for resolver functions, CLI commands and MCP tools now
comes back empty. This is the second time the same gap appeared in one session, and both times a
person noticed rather than a check — `missing-identifier` from the source pass is what would catch
it mechanically, and it is still unwritten.

## Third pass — T10, T15, T14: duckspec closed out

**T10 — four stale texts, found by re-reading the list rather than from memory.**
`@Duckspec#create_project` still told the model to create a term file "matching the `name:` field"
and to set `name` among the minimal fields. `@DuckspecProject#create_term` asked the user for a
`name` and created `<name>.yaml`. `@Term`'s inheritance guideline still said a child inherits
"contains slots", a block that no longer exists. And the sub-project naming rule referred to a file
"with `name: Systemd`".

> Went wrong: rewriting that last one I used `@Systemd` as an example — a term no project defines.
> `dangling-ref` reported it on the next run. The rule against unresolvable examples caught its own
> author within a minute of the edit, which is the strongest evidence so far that removing the
> exemption for examples was right.

Also went wrong: the working directory had drifted to the borshevik checkout between commands, so
the whole edit script failed on its first path and wrote nothing — visible only because the
assertion fired. Re-run with absolute paths. Worth carrying into the remaining repositories: never
rely on the inherited working directory in a migration script.

**T15 — `ambiguous-path-ref` implemented.** `_find_named_blocks` returns every entry with a given
id at any depth; `_extract_named_block` is now a thin wrapper taking its first result. The path
check counts candidates at each narrowing step: none is `broken-path-ref` as before, more than one
is the new finding. The search deliberately stays depth-unrestricted, because `#`-paths are allowed
to skip levels (`@DuckToolsApp#resolver#verify_project` omits `functions`) — counting, not
scoping, is what distinguishes a real ambiguity.

Silent on all four projects, as the design measurement predicted (161 references, zero ambiguous).
Negative test: a reference to `@ImageBuild#run` is reported as matching 7 entries — the exact case
that motivated the check, where the resolver would otherwise return whichever `run` came first.

**T14 — README.** The worked example was still in the old format: a top-level `name: WeatherWidget`
and `- name:` throughout. Rewritten to `- id:`, with the top-level name dropped and a line above the
block explaining that the filename is the identity. AGENTS.md needed nothing.

**Spec re-synchronised again** for `_find_named_blocks`, the new `_extract_named_block` wrapper and
`ambiguous-path-ref`. Function, CLI and MCP comparisons all come back empty.

## Duckspec is closed

Everything in the plan that applies to this repository is done: T1, T2, T3, T4, T5, T6, T7, T8, T9,
T10, T11, T12 (first half), T14, T15, T16, T17 with its `unknown-field` check, T18 (the term), T19.
All six CLI commands pass and the project verifies clean.

The only item left that touches duckspec is **T12's second half** — dropping `name:` acceptance
from the resolver — and it is blocked until borshevik and both extensions have migrated, since they
still write it. The 11 errors those three report are the `states:` entries awaiting T18.

## Follow-up: `create_project` asks for a name

Not required does not mean not asked. A project is precisely the case where a display name earns
its place over the identifier — `Borshevik Linux` against `Borshevik` — so `@Duckspec#create_project`
now asks for `name` as its own step, stating plainly that nothing breaks without it and that it
should be skipped only when the identifier already reads the way the user wants.

The same edit separated two things the recipe had been calling "name": the **identifier**, which is
CamelCase and becomes the filename and therefore the identity, and the **display name**, which is
prose. The instruction had inferred "the project name" from context and left it ambiguous which one
it meant.

Existing projects have no `name:` — it was deleted from all 145 files because it repeated the
filename. Adding real display names to `@Duckspec`, `@DuckTools`, `@Borshevik` and the two
extensions is a separate, optional pass.

## T20 — commands stop depending on the working directory

Prompted by two incidents during this migration: a script that edited nothing because the inherited
working directory had drifted to another checkout, and a `verify-project` run that reported a bogus
`unparsed-term` because it resolved its relative argument against the wrong tree. Both were
harmless, both were invisible until something asserted.

`_resolve_project` now sits in front of every command that takes a project. A value containing a
path separator or ending in `.yaml` is treated as a filesystem path exactly as before; anything else
is looked up in the active workspace by the stem of each registered project's path. So
`ducktools verify-project Duckspec` and `ducktools load-project Borshevik` work from any directory,
while paths keep working for projects not yet registered — which `create_project` needs, since it
creates the file before anything registers it. An identifier that matches nothing is returned
untouched, so the failure stays a plain missing-file error rather than a confusing lookup one.

Applied to `load_project`, `list_terms`, `load_terms`, `grep_terms`, `resolve_path` and
`verify_project`. Verified from `/tmp`: all six commands resolve by identifier, absolute paths still
work, and all four projects report the same results they do from their own directories.

## T21 — four read commands, so the tool answers what bash was answering

Every ad-hoc script written during this migration imported ducktools' own internals —
`_slot_items`, `_member_names`, `_parse_extends`, `_extract_named_block`. The logic already
existed; it just had no surface. Four commands close that, available on both the CLI and MCP:

- **`uses <Term>`** — reverse index: who extends it, who names it as a `type:`, who merely
  references it. The forward direction was always a file read; the reverse meant grepping the tree.
  Not knowing that nothing extends the nine element types is what made that question hard earlier.
- **`schema <Term>`** — every member the term effectively has, tagged with the ancestor that
  declared it. What a term actually carries, without reading the chain by hand.
- **`query [--rootless] [--extending X] [--declaring M] [--folder F]`** — filters by structure
  rather than text, and reports a count, which is what sizes a transformation before it is made.
- **`entries <Term>#<slot>`** — the entries of one slot with the fields each sets on itself.

Not done: structure-aware **write** operations (rename a member, remove one, move a block). That is
where all three mistakes in this migration happened — regex deletion leaving orphaned loop bodies, a
rename script walking the wrong subtree, a `replace` without a count hitting two places. Read
commands save keystrokes; write commands would have prevented defects. Worth doing next.

## T22 — structure-aware editing

Two write operations, on the CLI and MCP, sharing one locator:

- **`set <ref> <field> <value>`** — sets a field on the element a `#`-path addresses, replacing it
  in place when present and inserting it at the element's own field column when not.
- **`remove <ref>`** — removes the addressed element and everything nested under it, with
  boundaries taken from indentation.

`_locate` narrows segment by segment exactly as `resolve_path` does, but tracks line numbers so the
result can be edited, and **refuses an ambiguous path** rather than editing whichever element came
first. `remove` also refuses a bare term name, since deleting a whole file is not its job.

Each guard exists because of a specific failure in this migration: regex deletion left orphaned
loop bodies in the resolver (indentation boundaries cannot); a `replace` without a count inserted
`_schema` into two components (an ambiguous path is now refused); hand-written anchors put
descriptions on the wrong entries (a `#`-path cannot miss).

Verified: `set` reports added vs replaced, `remove` reports lines removed, `Term` alone is refused,
and `ImageBuild#run` — seven candidates — is refused as ambiguous.

Also caught while testing: `@Term` never declared `id` as a member. The whole migration introduced
it and the term defining terms did not know about it. Added.
