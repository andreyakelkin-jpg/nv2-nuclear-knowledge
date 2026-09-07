# Evidence contract

For a simple informational lookup, give the answer, exact source locator, and any material limit in a
short paragraph or list. Do not add empty sections. For a decision or multi-source applicability memo use:

1. **Conclusion** — the direct answer and its operational limit.
2. **Normative basis** — `[document_id, designation, clause, page]` for every requirement.
3. **Applicability** — product, safety class, lifecycle stage, material, process, contract, ТУ, and ОТТ conditions.
4. **Evidence status** — approved, expert-reviewed, AI-analyzed, or unverified.
5. **Gaps and conflicts** — missing sources and `[ТРЕБУЕТСЯ АНАЛИЗ]` items.
6. **Recommended action** — owner and next verification step.

Never convert an engineering interpretation into a quoted requirement. Quote exact text only after checking the normalized text or original page.

## Machine-checkable evidence

For every normative requirement, include its supporting source in the final contract:

```yaml
evidence_mode: passage
evidence_ids: [exact-document-id]
evidence:
  - document_id: exact-document-id
    clause: '1.2'  # use '1.2@line:42' if fetch reports duplicate numbering
    page: 3        # optional; omit when mapping is unknown, never invent
    quote: 'Exact supporting text from the fetched source'
    source_sha256: 'normalized source_sha256 returned by fetch'
    claim: 'The corresponding assertion in the final answer'
```

The ID must appear in the answer. All supplied locators and hashes must match. Empty, absent, ambiguous
or truncated evidence cannot establish the claimed requirement; refine the retrieval or report the gap.
Read conditions, exceptions, tables and linked clauses, not just a matching phrase. Use `metadata` only
for registry facts, never to bypass passage validation for normative content.

For high-criticality answers a controller review is mandatory. After reviewing the exact final text and
all source context, add `semantic_review: {reviewer: '<actual reviewer>', supported: true,
answer_sha256: '<hash>', evidence_sha256: '<hash>'}`. Get hashes with `kb evidence-check`. Changed answer
or evidence invalidates the review. This records the reviewer's judgment; it does not automatically
prove entailment, legal validity or operational approval. Do not attest unsupported claims.
