# Quality and performance controls

## Changes and intended effect

| Change | Effect |
| --- | --- |
| Exact edition matching, ambiguity detection, correction of wrong family matches | Prevents requirements from another edition being presented as the requested source. |
| Date/header filtering, explicit page quality, disambiguating clause anchors | More reliable citations; unknown pages are not invented. |
| Ranked full-document query windows with honest truncation | Better evidence under the same context budget. |
| Registry-only exact lookup, safe C YAML loading, cached structures/content terms | Less parsing and repeat work; faster retrieval and indexing. |
| Bounded `fetch-batch`, one outer run and `finish` | Fewer service calls, no duplicate routing/counters; recovery after interrupted finalization. |
| Citation/quote/hash checks plus source-bound controller review | Rejects fabricated IDs, misplaced quotations, changed sources and stale reviews. Does not automatically prove legal validity. |
| One usage CSV with edition-safe aliases and private retry IDs | Preserves real demand and usage history; technical retries do not inflate priorities. |
| Immutable index generations and shared writer lock | Readers can pin one published generation; failed builds do not replace it. Historical mirrors are compatibility paths, not a transactional reader API. |
| Per-page extraction checks and backed-up repair preview | Detects partially scanned files and guards against losing existing OCR text. |
| Typed assessment scoring, uncompensated critical errors, source-version checks | Prevents false passes; unreviewed, unpinned or changed sources require review. |
| Current-version reviewed model gate with at least 30 paired cases | Cheaper routing is enabled only by a current reviewed comparison, not string-presence tests. |
| Real versus estimated/unknown tokens, separate phase timings | Makes later speed/cost optimization measurable without treating missing data as zero. |

## Operational commands

Use the plugin runner (`scripts/run.ps1 kb ...` or `scripts/run.sh kb ...`):

- `demand-priorities --limit 10`: absent documents ranked by actual potential demand.
- `document-usage --limit 20`: usage across loaded and absent documents.
- `review-priorities --limit 20`: prioritize expert validation of the loaded core.
- `quality-status`: source-quality, review and telemetry summary.
- `training-impact`: direct source dependencies of questions and cases, not an inferred personnel schedule.
- `repair-extraction ID --dry-run`: inspect proposed extraction without replacing existing text.
- `evaluation-identity`: snapshot plugin/corpus identity before a model comparison.

Expert review must check edition validity against an authoritative source, compare important clauses,
tables and page mapping to originals, then record the actual reviewer, date and evidence. No automatic
operational approval is granted by these scripts.

## Verification and limitations

Run `python -m unittest discover -s tests` for deterministic regressions, including source/quote mismatch,
retry recovery, source changes, critical scoring, writer locking and index publication failure. POSIX
wrapper tests run on Linux; Windows cannot establish their result.

`evals/retrieval-regression.yaml` contains 36 **pending-review scenarios**, not gold answers, completed
model runs or evidence of token savings. Snapshot `dataset.identity` before collecting responses; actual
reviewer grades require hashes of each answer, reviewer identity, date and rubric. The evaluator and gate
reject stale identities and format-only comparisons. Review attestations are provenance records, not
cryptographic authentication of a person.

The rule parser is conservative, not a complete layout model: list numbering, annexes and table structure
can require original-page review. Image PDFs still require OCR; extraction fallback is not OCR. Usage
rows preserve legacy counts, but cannot reconstruct retry IDs that were never stored. Old index
generations are retained for in-flight readers; retention cleanup is deliberately not automatic.
