# SnG_Consulter — Agent Instructions

These instructions apply to the entire repository.

## 1. Core principle

SnG_Consulter is document-agnostic.

Production logic must depend only on observable document evidence and explicit domain rules.

Never add production behavior based on:

- client names;
- project names;
- filenames;
- known page numbers;
- known document layouts;
- fixed positions observed in fixtures;
- benchmark cases;
- HARAS, Julieta, Bracamonte, Casa PS, or any other development example.

Real documents used during development are fixtures and validation cases only.

A solution is not acceptable if it works because it recognizes a known document.

---

## 2. General architecture

Keep one canonical pipeline.

Do not create parallel, backup, legacy, temporary, or alternative implementations such as:

- `old_*`
- `backup_*`
- `*_v2_old`
- unused fallback pipelines
- duplicated services
- abandoned experimental code

When an implementation is replaced, remove the obsolete one.

Do not introduce speculative infrastructure for possible future needs.

Prefer small, explicit components with clear responsibilities.

---

## 3. Phase boundaries

Respect the responsibilities of each phase.

Completed phases should not be modified unless:

1. a reproducible bug is demonstrated; or
2. the current task explicitly requires a contract change.

Do not pull responsibilities from future phases into the current phase.

Conceptual pipeline:

1. Document Preparation
2. Discovery + Coverage Audit
3. Identity and Evidence Resolution
4. Technical Extraction
5. Verification
6. Final Resolution

Do not perform downstream interpretation earlier just because the information appears available.

---

## 4. Evidence and uncertainty

Never invent missing information.

Prefer:

- `null`
- `UNKNOWN`
- explicit uncertainty
- additional evidence

over unsupported assumptions.

Preserve original evidence whenever possible.

Derived or normalized data must not silently replace RAW evidence.

Any normalization should be reproducible and non-destructive unless the contract explicitly says otherwise.

---

## 5. Domain invariants

Keep these concepts separate:

- quantity of complete structures;
- functional modules;
- physical glass panes;
- decorative divisions or grids.

They are not interchangeable.

A visible rectangle or line is not automatically:

- a module;
- a glass pane;
- a functional division.

Use `module` for an internal functional subsystem.

Reserve `system` for commercial/product systems such as K40, K50, K70, S35, etc.

Do not infer a commercial system unless the appropriate phase has enough evidence.

If quantity is genuinely absent and the active domain contract requires a default, use the documented default rather than inventing evidence.

---

## 6. Input formats

The source format determines how evidence is acquired, not what the evidence means.

New formats must be integrated through the preparation/input boundary while preserving downstream semantics.

Do not add format-specific business interpretation.

Only claim support for formats that are actually implemented and tested.

---

## 7. Repository hygiene

Before finishing a task, inspect all touched files and remove:

- unused imports;
- dead helpers;
- duplicated logic;
- obsolete comments;
- temporary debugging code;
- superseded implementations;
- generated artifacts accidentally tracked by Git.

Generated runtime/debug artifacts must stay outside tracked source.

Do not keep unused code "just in case".

---

## 8. Testing

For every change:

1. run focused tests for the affected behavior;
2. then run the complete test suite.

Prefer synthetic/general tests over tests that encode one real document's layout.

Real documents may be used for regression validation, but production rules must not be derived from their identity.

Do not run heavy benchmarks unless the task explicitly requires them.

A passing test suite does not replace manual validation when the phase requires visual/document review.

---

## 9. Changes and scope

Make the smallest correct change that satisfies the current task.

Do not refactor unrelated areas.

Do not add:

- OCR;
- AI providers;
- databases;
- queues;
- background processing;
- Docker;
- external infrastructure;

unless the current task explicitly requires them.

Reuse existing contracts and helpers when appropriate instead of duplicating them.

---

## 10. Git

Do not commit or push unless explicitly requested.

Do not rewrite Git history.

Do not modify unrelated user changes.

Before finishing, use `git diff` / `git diff --check` when appropriate.

---

## 11. Documentation

Documentation must describe the code that actually exists.

Do not document planned functionality as implemented.

When behavior or contracts change, update the relevant documentation.

Keep detailed phase-specific decisions in `docs/`; keep this file limited to repository-wide rules.

---

## 12. Task completion report

At the end of an implementation task, report concisely:

- files created or modified;
- behavior implemented;
- tests executed and results;
- important architectural decisions;
- remaining real technical debt or unresolved questions.

Do not claim something was verified if it was not actually tested or inspected.