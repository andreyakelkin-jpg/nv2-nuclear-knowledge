# Assessment contract

Each item must contain a stable source reference. Formal assessments may use
only sources whose current `lifecycle_stage` is `expert_reviewed` or
`approved_for_operational_use`; every formal source must pin at least one
source hash. A source without a pin, a changed/missing declared version, or an
unqualified source produces `requires_review` or `blocked`, never `passed`.

Question definition:

```yaml
id: q-role-topic-001
type: single_choice | multiple_choice | short_answer | document_navigation
role: quality_engineer
competencies: []
source:
  document_id: example-document-id
  sha256: <pinned-original-sha256> # or original_sha256 / normalized_sha256
  anchor: p-1
  page: 1
question: "..."
correct_answer: a # scalar for single_choice; a set-like YAML list for multiple_choice
critical: false
```

`single_choice` uses strict scalar equality (a Boolean is not an integer).
`multiple_choice` compares the selected set, so ordering is irrelevant.
`short_answer` and `document_navigation` require an explicit
`scoring.exact_answer` (or `exact_answer`) for automatic scoring; otherwise
they require manual review. A missing answer or missing objective key cannot
produce a pass.

Case definition uses `type: case` implicitly and may use one `source` object
or a list of them. The submitted case response must include a non-empty
`evaluator` and a finite `expert_score` from 0 to 100. Set
`critical_failure: true`, or list `critical_errors`/`critical_failures`, when
the evaluator finds a critical safety error.

Assignment and answer shape:

```yaml
# training/assignments/<id>.yaml
id: attestation-example-001
mode: formal # formal | pilot
pass_score: 80
critical_errors_allowed: 0 # deprecated; only 0 is accepted
items:
  - kind: question # question | case
    id: q-role-topic-001
    weight: 1 # finite, > 0; IDs may not repeat

# answers.yaml
answers:
  q-role-topic-001:
    answer: a
  case-example-001:
    evaluator: expert-id
    expert_score: 90
    critical_failure: false
```

`pilot` is explicit and always returns `requires_review`, never a formal
admission result. Any critical error independently makes the result fail;
score and the deprecated limit cannot compensate for it. Reports record each
declared/current source identity, edition, original SHA-256, normalized
SHA-256, score, review requirements, and blockers.

Recommended assessment mix:

- 35% knowledge of requirements;
- 30% locating and applying the correct clause;
- 25% situational decision;
- 10% evidence quality and citation.

Record score, critical errors, competence gaps, retraining actions, assessor,
source versions, and next assessment date.
