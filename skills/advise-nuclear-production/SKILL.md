---
name: advise-nuclear-production
description: Provide role-based, evidence-backed advice for a nuclear-industry manufacturing company. Use for decisions by the quality engineer, technical director, chief designer, economist, sales head, project manager, training specialist, or company director involving tenders, design, manufacturing, quality, suppliers, cost, schedule, and regulatory risk.
---

# Advise nuclear production

Treat the directory two levels above this skill directory as `PLUGIN_ROOT`. Set `RUNNER` to
`powershell -NoProfile -ExecutionPolicy Bypass -File PLUGIN_ROOT/scripts/run.ps1` on Windows or
`sh PLUGIN_ROOT/scripts/run.sh` on Linux. Run `RUNNER kb root` and use the returned absolute path as
`KB_ROOT`; never infer it from the current project. For installation, transfer, or diagnostic failures,
read [the platform setup guide](../../references/platform-setup.md) and run `RUNNER doctor`.

Before substantive work, follow [the shared routing protocol](../../references/model-routing.md). Safety,
compliance, deviations, substitutions, conformity, release, and contractual commitments are
high-criticality and route to Sol/high.

1. Identify the decision, role, product, project stage, safety class, customer requirements, material, process, and deadline. State missing inputs explicitly.
2. Use `$query-nuclear-knowledge` to assemble the evidence package.
3. Apply the relevant role lens from [references/roles.md](references/roles.md). Read the matching detailed prompt in `KB_ROOT/prompts/advisors/` when present.
4. Separate mandatory requirements, engineering interpretation, commercial assumption, and recommendation.
5. Return a decision memo: recommendation, alternatives, normative basis, impacts on safety/quality/cost/schedule, risk owner, evidence required, and approval gate.

Do not approve deviations, material substitutions, conformity, product release, or contractual promises without explicit authority and evidence. If no card is approved for operational use, label the memo preliminary.
