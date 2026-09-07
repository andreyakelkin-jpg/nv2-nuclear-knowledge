# Cascaded model routing

Use this protocol before substantive analysis. It is fail-closed: when routing is disabled, the quality
gate has not passed, model selection is unavailable, or confidence is low, use `gpt-5.6-sol` with `high`
reasoning.

Administrative `status`, `document-usage`, `demand-priorities`, `review-priorities`, `quality-status`,
and metadata-only lookups do not need a worker, draft file, routing run, or usage write. Run the bounded
read-only command and answer directly. Resolve `KB_ROOT` once per task. An evidence-only nested skill
reuses the outer routing run and returns evidence instead of routing, validating, or counting again.
Keep the current controlling model when it is already at least as capable as the required fallback;
do not spawn an identical worker solely to relay its answer. Never label selected tier as actual model.

## 1. Assess and route

Assess these fields explicitly before selecting a worker:

- `complexity`: `low` only for one bounded operation; `high` for dependent reasoning or broad synthesis.
- `ambiguity`: `low` when the term and success criteria are clear; `medium` when bounded retrieval or one
  clarification can resolve the uncertainty; `high` only when competing interpretations remain after that
  check and materially affect the answer.
- `criticality`: assess the consequence of using this answer in the current task, not the subject domain.
  Generic definitions and educational explanations are `low`; a generic multi-source determination method
  is normally `medium`; use `high` when the answer will decide or approve a concrete product, project,
  safety, compliance, release, legal, or financial action.
- `context_chars`: characters expected to be read by the worker.
- `tool_count`: count only model-facing evidence/retrieval calls made by the worker. Exclude `route`,
  `route-check`, logging, and deterministic controller operations. Several bounded read-only calls do not
  by themselves make a task high-risk.
- `side_effects`: `none`, `local`, or `external`.
- `confidence`: confidence that the task was classified into the correct routing tier. This is not
  confidence that the answer or evidence is already known. Do not lower it merely because retrieval has
  not happened yet; missing evidence is handled by answer validation and the single escalation.

Run:

```text
RUNNER kb route \
  --task-id <non-sensitive-id> --complexity <low|medium|high> \
  --ambiguity <low|medium|high> --criticality <low|medium|high> \
  --context-chars <n> --tool-count <n> --side-effects <none|local|external> \
  --confidence <0..1>
```

The router enforces Luna/low only for bounded, unambiguous, low-risk work; Terra/medium is the balanced
default; Sol/high handles hard, risky, ambiguous, large-context, and low-confidence work. Defined boundary
bands are promoted one tier. Do not override the returned tier downward.

The paired quality gate remains in the immutable knowledge-base release under
`KB_ROOT/reports/model-routing/gate-latest.yaml`. Mutable events and run records are written under
`NV2_NUCLEAR_STATE_ROOT/reports/model-routing/`; set a separate writable state root whenever `KB_ROOT` is
mounted read-only.

For calibration, treat `0.85` as sufficient routing confidence for an otherwise all-low Luna lookup.
Confidence from `0.65` upward does not by itself promote a medium task beyond Terra. Values below `0.60`
are fail-closed Sol; the narrow `0.60–0.65` band promotes a task with multiple medium dimensions.

For a list of independent questions, assess and route each item (or a genuinely homogeneous subgroup)
separately. Do not sum their context and retrieval calls into one artificial high-complexity task. Use one
Terra/medium synthesis only when the user needs a combined answer across the independently retrieved facts.

Use an isolated worker with the returned model and effort when the runtime supports model-specific workers.
If it does not, retain or request Sol/high. A worker may gather evidence and prepare a draft, but must not
commit filesystem, Git, marketplace, or external side effects.

## 2. Define the answer contract

Before dispatch, create a small YAML contract containing the explicit success conditions:

```yaml
min_chars: 1
required_strings: []
required_sections: []
evidence_ids: []
evidence_mode: none  # none for administrative plans; metadata for card facts; passage for requirements
evidence: []
forbidden_strings: []
format: text  # text | markdown | json
```

Use exact user-required fields and known evidence IDs. Do not invent requirements merely to make grading
easy.

## 3. Validate and escalate once

Save the draft and run:

```text
RUNNER kb finish <run-id> --answer <answer-file> --contract <contract-file> \
  [--used <document-id>] [--missing <exact-designation>] \
  [--input <input-file>] [--input-tokens N --output-tokens N --latency-ms N] \
  [--actual-model <executed-model>] [--retrieval-ms N --generation-ms N]
```

The check covers format and source/quote integrity, not automatic proof of meaning. Normative answers
must follow [the evidence contract](../skills/query-nuclear-knowledge/references/evidence-contract.md).
For high criticality, the controller must review every requirement against its complete source context,
then attest the exact answer/evidence hashes; never create an attestation merely to pass the checker.
`kb evidence-check --answer FILE --contract FILE` is a read-only way to verify sources and obtain hashes.
An attestation is a review record, not independent authentication of a human expert.

`finish` records usage only after acceptance. Repeating the identical operation is safe, even after an
interruption between checking and recording; a changed answer requires a new run. `route-check` remains
available when usage is not yet ready, followed by `usage-record` with that run ID. Do not use both paths
to count different versions of one final answer. Omit unknown token counts rather than writing zero.
Exact runtime counts take precedence; estimates are labeled, unavailable input/total are null. Supply
actual model only when known. Timings separate retrieval, generation, validation and end-to-end time.

If accepted and the task has side effects, claim the operation before executing it:

```text
RUNNER kb route-claim <run-id> --operation-id <stable-idempotency-key>
```

Execute only when this returns `execute_once: true`; the same run cannot be claimed again. If rejected
and `escalate_once` is present, run `RUNNER kb route-escalate <run-id>` and dispatch one new worker at the
returned next tier. Validate the new draft once. Never perform a second escalation and never replay side
effects. A rejected Sol draft is a hard stop requiring clearer evidence or user input.

## 4. Quality gate

`kb routing-status` is authoritative. Routing is effective only when both the feature flag and the latest
paired non-inferiority gate pass. `kb routing-gate <comparisons.yaml>` automatically disables routing when
the one-sided 95% bootstrap bound falls below the configured margin versus Sol/high.
The gate must match `kb evaluation-identity` (plugin instructions/code and corpus version). Old gates
are stale after changes. Only `reviewed_answers` with reviewer scores tied to exact answer hashes can
enable routing; format-only checks and the pending scenarios in `evals/retrieval-regression.yaml` cannot.
Capture `dataset.identity` before collecting model answers and reviews; stale datasets cannot be relabeled
as current by re-running the evaluator. The gate requires at least 30 paired cases.
Do not claim measured token savings from estimated counts or from unexecuted comparison scenarios.

## 5. Calibration mode

When the user asks to compare or calibrate models, do not use the production router to select only one
model and present that as a comparison. Run every requested question independently on Luna/low,
Terra/medium, and Sol/high using the same retrieved evidence and answer contract. Validate every draft,
record reported tokens and latency (clearly mark estimates), and then run the paired comparison. Do not
apply side effects. If model-specific workers are unavailable, report that the experiment cannot be
completed; do not fabricate missing model runs. Choose the weakest non-inferior tier, then apply the
user-requested one-tier safety margin only to a genuinely borderline result.
