# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout

This is a single-context repo.

Read:

- `CONTEXT.md` at the repo root for domain vocabulary.
- `docs/adr/` for accepted architectural decisions.

If either location does not exist in a future checkout, proceed silently. The `/domain-modeling` skill creates domain docs lazily when terms or decisions get resolved.

## Use the glossary's vocabulary

When output names a domain concept in an issue title, refactor proposal, hypothesis, or test name, use the term as defined in `CONTEXT.md`.

If the concept is missing from the glossary, either avoid inventing new language or note the gap for `/domain-modeling`.

## Flag ADR conflicts

If output contradicts an existing ADR, surface it explicitly rather than silently overriding it.
