---
name: query-nuclear-knowledge
description: Search and answer questions from the local nuclear-industry normative knowledge base with traceable evidence. Use for applicability checks, requirements lookup, cross-document comparison, material or process questions, missing-document analysis, and context packages for other projects.
---

# Query nuclear knowledge

Treat the directory two levels above this skill directory as `PLUGIN_ROOT`. Resolve the external
knowledge base by running `powershell -NoProfile -ExecutionPolicy Bypass -File
PLUGIN_ROOT/scripts/run.ps1 kb root` and use the returned absolute path as `KB_ROOT`. Never infer
`KB_ROOT` from the current project.

## Workflow

1. Read `KB_ROOT/meta/documents.yaml` to identify candidate documents.
2. Check lifecycle, status, legal evidence, replacements, conflicts, and cross-references before using a document.
3. Read only relevant Markdown cards and, when exact wording matters, the corresponding section in `normalized/` or the original PDF.
4. Answer using [references/evidence-contract.md](references/evidence-contract.md).
5. Mark conclusions from non-approved cards as preliminary.
6. If evidence is incomplete, list the missing document or project parameter instead of guessing.

## Retrieval order

Use exact designation and clause search first, then metadata/category search, then semantic interpretation. Prefer approved documents. Do not treat frequency of citation as legal priority.

For another project, return a compact context package containing the question, scope assumptions, selected sources, exact clauses/pages, open conflicts, missing documents, and corpus cut-off date.
