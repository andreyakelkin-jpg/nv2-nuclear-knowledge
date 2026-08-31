---
name: query-nuclear-knowledge
description: Search and answer questions from the local nuclear-industry normative knowledge base with traceable evidence. Use for applicability checks, requirements lookup, cross-document comparison, material or process questions, missing-document analysis, and context packages for other projects.
---

# Query nuclear knowledge

Treat the directory two levels above this skill directory as `PLUGIN_ROOT`. Set `RUNNER` to
`powershell -NoProfile -ExecutionPolicy Bypass -File PLUGIN_ROOT/scripts/run.ps1` on Windows or
`sh PLUGIN_ROOT/scripts/run.sh` on Linux. Run `RUNNER kb root` and use the returned absolute path as
`KB_ROOT`; never infer it from the current project. For installation, transfer, or diagnostic failures,
read [the platform setup guide](../../references/platform-setup.md) and run `RUNNER doctor`.

Before substantive work, follow [the shared routing protocol](../../references/model-routing.md). Exact,
single-document lookups may qualify for Luna/low. Do not classify a question as high-criticality merely
because it concerns nuclear regulation. Generic definitions, lists, distinctions, and explanations of how
a class, group, category, or conformity form is determined are informational: use Luna/low when bounded
to one clear source, otherwise Terra/medium. Use Sol/high for applying rules to a concrete product or
project, making a safety/compliance decision, resolving conflicting evidence, or material uncertainty that
could change such a decision. Route independent questions separately; exclude routing, validation, and
logging calls from `tool_count`.

For an ordinary informational lookup, `confidence` measures confidence in this routing classification,
not whether the normative answer is already known. Do not reduce it because search/fetch has not run.
Use answer validation to detect missing evidence and escalate once only after a Luna or Terra draft fails.
If the user requests a model calibration or comparison, follow the calibration mode in the shared protocol
and actually run every requested tier; a table of production routing choices alone is not a model-quality
comparison.

## Workflow

1. Run `RUNNER kb search "<query>" --limit 6 --max-chars 12000` to identify candidates. Do not open
   `meta/documents.yaml`, `meta/cross-references.yaml`, or another full registry for discovery.
2. Check status, lifecycle, replacements, and applicability in the compact search result. Fetch evidence
   with `RUNNER kb fetch <document-id> --clauses <clause-list> --max-chars 12000`. Use
   `--pages <page-list>` or `--query "<phrase>" --context-lines 2` when clause numbers are unknown.
3. Read a full Markdown card only when a field absent from search/fetch is required. Never load an entire
   normalized document when bounded `kb fetch` can provide the evidence.
4. Compare the original PDF only when exact visual structure, a table, formula, scan, footnote, or OCR
   ambiguity matters.
5. Answer using [references/evidence-contract.md](references/evidence-contract.md).
6. Mark conclusions from non-approved cards as preliminary. If evidence is incomplete, list the missing
   document, clause, or project parameter instead of guessing.

## Retrieval order

Use exact designation and clause search first, then metadata/category search, then semantic interpretation. Prefer approved documents. Do not treat frequency of citation as legal priority.

Keep command output bounded with `--limit` and `--max-chars`; refine the query instead of widening the
context indiscriminately. For another project, return a compact context package containing the question,
scope assumptions, selected sources, exact clauses/pages, open conflicts, missing documents, and corpus
cut-off date.
