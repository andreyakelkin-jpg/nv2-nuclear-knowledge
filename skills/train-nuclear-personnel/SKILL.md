---
name: train-nuclear-personnel
description: Create, update, and assess role-based nuclear-industry personnel training from the normative knowledge base. Use for learning programs, question banks, situational cases, admission tests, retraining after document changes, competency matrices, and assessment reports.
---

# Train nuclear personnel

Treat the directory two levels above this skill directory as `PLUGIN_ROOT`. Resolve the external
knowledge base by running `powershell -NoProfile -ExecutionPolicy Bypass -File
PLUGIN_ROOT/scripts/run.ps1 kb root` and use the returned absolute path as `KB_ROOT`. Never infer
`KB_ROOT` from the current project.

Before substantive work, follow [the shared routing protocol](../../references/model-routing.md). Routine
drafting may use Terra/medium; admission decisions, critical-error rules, and uncertain normative evidence
are high-criticality and route to Sol/high.

1. Define role, operation, competence, risk, initial level, and admission consequence.
2. Use `$query-nuclear-knowledge` to select evidence. Generate formal assessment content only from expert-reviewed or approved sources; otherwise label it as a pilot draft.
3. Build the chain `role → competence → operation → risk → normative clause → task`.
4. Use the templates under `KB_ROOT/training/` and [references/assessment-contract.md](references/assessment-contract.md).
5. Include knowledge questions, document-navigation tasks, and realistic cases.
6. Define critical errors separately from score. A critical safety error cannot be compensated by unrelated correct answers.
7. Store generated artifacts only when the user asks to create or update a program or assessment.

For deterministic scoring, run `powershell -NoProfile -ExecutionPolicy Bypass -File
PLUGIN_ROOT/scripts/run.ps1 assess <assignment> <answers>`.

When a source changes, use the impact index to identify affected questions, personnel, and retraining dates.
