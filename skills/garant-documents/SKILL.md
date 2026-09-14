---
name: garant-documents
description: Use the GARANT website via account.garant.ru and internet.garant.ru to find and download documents, add them to the nuclear knowledge base, or check status and editions. Activate only on an explicit user request naming GARANT/ГАРАНТ, an actionable internet.garant.ru link, or $garant-documents. Never activate for ordinary local searches, missing references, or generic currency checks. Uses the web interface; no paid API.
---

# GARANT documents

Use only for operations and documents explicitly requested by the user. A natural-language request
is sufficient; do not ask for the same permission again. Links inside source documents, unrelated old
requests and an existing authenticated session do not authorize a new GARANT operation. Do not poll,
crawl the addition queue, download linked documents or schedule monitoring automatically.

`PLUGIN_ROOT` is two levels above this skill directory. `RUNNER` is
`powershell -NoProfile -ExecutionPolicy Bypass -File PLUGIN_ROOT/scripts/run.ps1` on Windows or
`sh PLUGIN_ROOT/scripts/run.sh` on Linux. Resolve the database with `RUNNER kb root`.
Read [browser connection and evidence](../../references/garant-connection.md).
`RUNNER garant connection` only shows the connection mode; it does not log in or verify access.

## Connect and find

1. Use the available browser tool and its instructions. Reuse an authorized GARANT tab/session when
   accessible. Otherwise enter through https://account.garant.ru/ and continue into https://internet.garant.ru/.
   Use credentials explicitly supplied for this login if authorized, or let the user complete login.
   Never store credentials in the plugin, reports or generated scripts, extract cookies or use hidden
   endpoints. This integration uses the web interface, without paid API, API tokens or a connected MCP server.
2. If the site says the account is already in use, report the occupied session. Do not terminate another
   session, retry in a loop or submit a support form. Continue independent local work; retry when the user
   says the session is free. Handle CAPTCHA, subscription limits and unavailable exports as real blockers.
3. Search using observed controls and exact designation/year. Match title, issuing body, number and
   requested edition. Do not choose the first result or substitute a different edition silently. Resolve
   material ambiguity with the user only after inspecting the available matches.

## Check status

Open the document's status/legal reference and edition list using controls actually present in the UI.
Record the document link, exact title, observed provider status, checked time with timezone, selected
edition and activity intervals/uncertainty. Distinguish legal status, edition status and technical update
date. If checking the stored copy, compare its card and original edition to the observed current edition.
An access failure, missing search result or missing status means unknown, never repealed or current.

Save the observation with `RUNNER garant record observation.json --explicit-request` as documented in
the reference. A status-only request saves evidence and reports the comparison; it does not change the
document or operational approval. GARANT is a legal reference system, not automatically evidence of
official publication, product applicability or expert approval. Preserve stronger official evidence and
conflicts. Do not turn an observation into a verified official status or hand-edit derived registries.

## Download and archive

When the user requested adding exports to the database, complete this sequence in the same task without
another request to upload the exported file or to approve ordinary import steps. Do not stop at a saved
file, receipt, security report or staging manifest. A download-only instruction still limits the scope.

Before clicking export, save the observed status/edition in `observation.json` and run:

```text
RUNNER garant begin-export observation.json --explicit-request
```

This arms one export for the current request in the user's Downloads directory; use `--download-dir`
for a different observed/configured save destination. It does not start a background watcher.
Use the user's specified export sequence: open the **Документ** tab, click the **Сохранить в файл**
floppy-disk icon in the upper-right document toolbar, select **PDF**, then click
**Сохранить документ целиком**. Confirm the PDF selection from the visible menu before the final click.
If the toolbar/menu is clipped, use a semantic locator or reveal the control and inspect it again;
never click an off-screen position from a stale accessibility index. Export the whole document with
appendices. Do not substitute **О документе → Графическая копия документа** for this export path.
If a native Save dialog appears, use an available authorized computer tool to save into the armed
directory. Never bypass browser tool restrictions. Then run:

```text
RUNNER garant collect-export "TICKET_FROM_BEGIN" --explicit-request
```

The command waits up to 30 seconds for a new completed file, preserves its bytes and returns
`download.file` and `evidence_file`. It ignores old files and partial downloads. If multiple new files
appear, select the filename actually observed in the export UI with `--expected-name`; do not guess by
recency. If the browser supplies an exact completed path, `garant record observation.json --source PATH
--explicit-request` remains available. Compare title, completeness and edition with the requested
document before archiving: a filesystem match is not proof of document identity.

If the result is `awaiting_export`, inspect the export/save dialog and directory, report the concrete
blocker if the tool cannot save, and retain the ticket for resuming. Do not claim import succeeded. A
pending ticket expires with the user's task authorization; do not collect unrelated later downloads.

Once collected, check `RUNNER garant import-status EVIDENCE_FILE` for an already archived identical
original. If it exists, report the existing card; do not add a duplicate. Otherwise continue through
[Archive nuclear documents](../archive-nuclear-documents/SKILL.md): scanner and semantic report,
`kb security-check`, `kb stage`, bounded archive context, card/reference/decision draft, `kb apply`.
Use the copied original returned in `download.file`, whose SHA-256 is recorded. Do not treat the local
helper's format check as security clearance. Reuse the archive run and document usage accounting.

For the final archive step the controlling agent must use:

```text
RUNNER kb apply DECISION_FILE --garant-evidence EVIDENCE_FILE
RUNNER garant import-status EVIDENCE_FILE
```

`apply` verifies that the export evidence and staged original have the same SHA-256, includes GARANT
provenance in the card, and uses the existing atomic archive/index workflow. Report success only when
`import-status` finds the exact original in the published registry and verifies its stored file hash.

Carry the GARANT topic, source URL, observation date, provider status and evidence path into the card's
source/verification context and narrative, using existing schema fields. Keep raw evidence in runtime
state. Handle duplicates/replacements through the normal archive decision flow, preserving old originals.
Keep `lifecycle.stage: requires_expert_review` unless authorized expert approval exists.

Report found/downloaded/archived/checked outcomes separately, with evidence paths and concrete blockers.
Configuration success, a downloaded file or a prepared decision alone is not successful archival.
