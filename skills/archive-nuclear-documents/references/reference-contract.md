# Reference contract

Store the complete array in `generated/<stage_id>/references.yaml`, never in the Markdown card. Use:

```yaml
schema_version: 1
document_id: "document-id"
references: []
```

For each normative mention in `references`, record:

```yaml
- cited_as: "Exact designation or title as printed"
  location: "section, clause, table, note, appendix, and page when available"
  status: "в_базе | отсутствует | устарел_есть_замена | устарел_нет_замены | устарел_специфика"
  target_document: null
  replacement_document: null
  reference_role: "nuclear_rule | normative_standard | governing_act | project_specific | approval_act | amendment_act | informative | other"
  basis: "Evidence for the status or resolution"
  action: "Required follow-up when unresolved"
  confidence: "high | medium | low | not_assessed"
  requires_analysis: false
```

Use `в_базе` only when `target_document` exists. Use a replacement status only with explicit evidence. Preserve old editions required by a contract, ТУ, ОТТ, or КД as `устарел_специфика` and cite that basis.

Classify approval orders, amendment decisions, and informative bibliography separately from operational normative documents. They must remain discoverable but must not automatically receive high queue priority.
