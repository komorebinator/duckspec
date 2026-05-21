# aikata.md

Defines the rules for writing and organizing specs across the projects.

## Goals

- Standardize how requirements and specs are written across projects
- Reduce the noise models introduce into the development process
- Provide tools for describing and navigating project structure
- Simplify repetitive development tasks

## Spec
A spec is a document that describes one or more entities. A `.yaml` spec describes exactly one entity; a `.md` spec may describe multiple.

### Authoring Rules

- Specs are stored in the `aikata/` folder in the project root.
- The folder may contain subfolders. The folder tree represents the semantic structure of the project.
- Specs must be written in English.
- To reference another spec, use `[[spec:path/from/aikata/root]]` anywhere in any field.

### Types

- `.yaml` — describes a single entity. Must follow the entity spec format.
- `.md` — contains any supporting information. Can be referenced from other specs. Preferred structure: one top-level section per entity, with all properties and behavior of that entity described within its section.

## Entity

An entity is a concept, module, or object being described.

### Properties

- **name** `required` — human-readable identifier of the entity
- **description** `required` — what the entity is and does
- **extends** `optional` — reference to a parent entity spec using `[[spec:...]]`; all parent properties, functions, and recipes are inherited and can be overridden by name
- **properties** `optional` — list of properties the entity holds
- **recipes** `optional` — list of recipes the entity exposes
- **functions** `optional` — list of functions the entity exposes

## Property

A property is a named field on an entity that holds a value.

### Properties

- **name** `required` — property identifier, no spaces (code-style)
- **description** `optional` — what the property represents
- **type** `optional` — free-form description of the value type
- **default** `optional` — default value if not explicitly set
- **required** `optional` — whether the property must be set; defaults to false

## Inheritance

When an entity declares `extends`, the model resolves the parent entity first (recursively, if the parent also extends). The child inherits all parent properties, functions, and recipes. Any child member with the same name as a parent member fully replaces it. Members not redefined in the child are inherited as-is.

## Function

A function is a description of an algorithm that can be directly converted to code.

### Properties

- **name** `required` — function identifier, no spaces (code-style)
- **description** `required` — what the function does and how; include algorithm logic, edge cases, and expected behavior
- **arguments** `optional` — list of argument names the function accepts

## Recipe

A recipe is a named set of instructions executed by a model. When a recipe is invoked, the model resolves its arguments, then executes its instructions in order.

**Argument resolution** — if an argument value is not explicitly provided, the model must first attempt to infer it from context. If the value cannot be inferred and the model cannot proceed without it, the model must ask the user.

**Pre-execution validation** — before executing any instruction, the model must identify all spec objects the recipe will operate on and validate them against the rules in this document. If a required property is missing or a reference is broken, the model must resolve the issue first: ask the user for the missing value, or flag the broken reference and wait for a fix. Only once all objects are valid may the model proceed with the recipe's instructions.

**Pre-execution clarification** — before executing any instruction, if any step is ambiguous or lacks enough detail to execute unambiguously, the model must ask the user to clarify it, then update the recipe with the clarified wording before proceeding.

**Recipe call** — an instruction may invoke another recipe using the syntax `Invoke [[spec:path/from/aikata/root]]#recipe_name(arg=value, ...)`. The model executes the referenced recipe's instructions as a subroutine, passing the specified arguments; unspecified arguments follow normal argument resolution. After the subroutine completes, execution continues with the next instruction of the calling recipe. The result of the called recipe is available as context for subsequent instructions.

### Properties

- **name** `required` — recipe identifier, no spaces (code-style)
- **description** `optional` — human-readable description
- **arguments** `optional` — list of argument names the recipe accepts
- **instructions** `required` — ordered list of instructions for the model to execute
