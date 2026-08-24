---
name: archive-nuclear-documents
description: Archive PDF, DOCX, text PDF, and scanned normative documents into the nuclear-industry knowledge base. Use when a user attaches or asks to add, update, replace, classify, index, or verify an НП, ГОСТ, ГОСТ Р, ТУ, ОТТ, СТО, РД, procurement standard, law, or regulatory act.
---

# Archive nuclear documents

Treat the directory two levels above this skill directory as `PLUGIN_ROOT`. Resolve the external
knowledge base by running `powershell -NoProfile -ExecutionPolicy Bypass -File
PLUGIN_ROOT/scripts/run.ps1 kb root` and use the returned absolute path as `KB_ROOT`. Never infer
`KB_ROOT` from the current project.

## Workflow

1. Run `powershell -NoProfile -ExecutionPolicy Bypass -File PLUGIN_ROOT/scripts/run.ps1 kb stage
   <source>` unless the attachment is already staged.
2. Run `... run.ps1 kb archive-context <stage-id> --max-chars 16000`. This is the default source for
   categories, duplicate candidates, corpus state, and resource paths. Do not read every file in
   `KB_ROOT/meta/`.
3. Read the archivist prompt and card template named in that compact context. Inspect extraction quality.
   For scans, tables, footnotes, appendices, or suspicious OCR, compare rendered pages with extracted text.
4. Extract every normative mention. Resolve them in bounded batches with repeated
   `... run.ps1 kb archive-context <stage-id> --reference "<designation>" --max-chars 16000` calls and
   follow [references/reference-contract.md](references/reference-contract.md). Do not load the full
   cross-reference or addition-queue registry.
5. Create the complete Markdown card and `decision.yaml` under `KB_ROOT/generated/<stage_id>/`.
6. Run `powershell -NoProfile -ExecutionPolicy Bypass -File PLUGIN_ROOT/scripts/run.ps1 kb apply
   <decision.yaml>`. This must synchronize prior references, rebuild indexes, prioritize the queue,
   build replacements, and validate integrity atomically.
7. Report the archived document, review state, resolved references, remaining high-priority gaps, and all items requiring expert analysis.

## Guardrails

- Preserve the original file and SHA-256.
- Never invent status, replacement, clause, page, or applicability.
- Keep `lifecycle.stage: requires_expert_review` unless an authorized expert explicitly approves operational use.
- Treat user confirmation and official-source verification as different evidence levels.
- Do not hide ambiguous OCR or conflicting revisions; set `requires_analysis: true`.
- Create a new category only when existing categories genuinely do not fit.
