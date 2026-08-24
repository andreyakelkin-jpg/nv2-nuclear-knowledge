---
name: query-nuclear-knowledge
description: Search and answer questions from the local nuclear-industry normative knowledge base with traceable evidence. Use for applicability checks, requirements lookup, cross-document comparison, material or process questions, missing-document analysis, and context packages for other projects.
---

# Query nuclear knowledge

Treat the directory two levels above this skill directory as `PLUGIN_ROOT`. Resolve the external
knowledge base by running `powershell -NoProfile -ExecutionPolicy Bypass -File
PLUGIN_ROOT/scripts/run.ps1 kb root` and use the returned absolute path as `KB_ROOT`. Never infer
`KB_ROOT` from the current project.

Before substantive work, follow [the shared routing protocol](../../references/model-routing.md). Exact,
single-document lookups may qualify for Luna/low; applicability, conflicts, or incomplete evidence are
high-criticality and must route to Sol/high.

## Workflow

1. Run `powershell -NoProfile -ExecutionPolicy Bypass -File PLUGIN_ROOT/scripts/run.ps1 kb search
   "<query>" --limit 6 --max-chars 12000` to identify candidates. Do not open
   `meta/documents.yaml`, `meta/cross-references.yaml`, or another full registry for discovery.
2. Check status, lifecycle, replacements, and applicability in the compact search result. Fetch evidence
   with `... run.ps1 kb fetch <document-id> --clauses <clause-list> --max-chars 12000`. Use
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
