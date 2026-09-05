# Document usage counters

Use this protocol for every substantive user-facing answer produced by an NV2 nuclear-knowledge skill.
The outermost skill records the answer once, immediately before sending the final response. A nested
`query-nuclear-knowledge` evidence step passes its document lists to the outer skill and does not record a
second event.

## What to count

- `used_documents`: documents that actually support, are cited in, or are the main subject of the final
  answer. Do not count every search candidate or incidental cross-reference.
- `missing_documents`: specific documents that would have been used for a fuller or more reliable answer
  but were unavailable in the corpus. Do not add vague document families, missing project parameters, or
  every unresolved bibliography item.
- Count a document at most once in each list for one answer. A later answer is a new observation even when
  another user asks the same question verbatim.

Use the final validated routing `run_id` as `answer_id`. This makes a technical retry of the same answer
idempotent while allowing every later request to increment the counters normally. Do not store the prompt,
user identity, or an explanation of why the document was needed.

## Record one answer

Run the command below immediately before the final response. Repeat `--used` and `--missing` for every
document; omit either option when that list is empty. Name an exact edition when the edition matters.

```text
RUNNER kb usage-record --answer-id <final-run-id> --used "НП-001-15" --missing "ГОСТ 00000-0000"
```

At least one document is required. Confirm that the command succeeds before sending the final response.
It increments two simple per-answer counters, ignores only a repeated write with the same `answer_id`, and
refreshes the single UTF-8 Excel-compatible table `meta/document-usage.csv`.
Each document has `used_count` and `potential_count`; the remaining columns provide status, dates, and
technical idempotency keys.

If no document was used and no specific missing document can be named, do not invent a row.

## Answer statistics questions

- For “what should we upload first?”, run `RUNNER kb demand-priorities --limit 10`. Rank strictly by the
  number of separate answers that needed the document; use recency only to break equal counts.
- For “which documents are used most?”, run `RUNNER kb document-usage --limit 20`.
- Do not record these administrative statistics lookups as document usage unless the answer independently
  relies on normative evidence.
