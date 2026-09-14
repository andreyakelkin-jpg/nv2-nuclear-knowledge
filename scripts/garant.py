#!/usr/bin/env python3
"""Local evidence intake for the GARANT browser workflow. Never connects to the network."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

MAX_FILE = 50 * 1024 * 1024
MAX_OBSERVATION = 1024 * 1024
FIELDS = {"source_url", "title", "checked_at", "provider_status", "revision_label",
          "revision_status", "effective_from", "effective_to", "uncertainty", "observation_text"}
DOWNLOAD_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
PARTIAL_EXTENSIONS = {".crdownload", ".part", ".partial", ".tmp"}


def read_json(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(MAX_OBSERVATION + 1)
    if len(raw) > MAX_OBSERVATION:
        raise ValueError("Observation exceeds 1 MiB.")
    result = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object.")
    return result


def write_json(path: Path, data: dict) -> None:
    from index_store import atomic_write
    atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def file_snapshot(directory: Path) -> dict:
    result = {}
    for path in directory.iterdir():
        if path.suffix.lower() in DOWNLOAD_EXTENSIONS | PARTIAL_EXTENSIONS and path.is_file() and not path.is_symlink():
            try:
                info = path.stat()
            except FileNotFoundError:
                continue  # Browser renamed a temporary download between listing and stat.
            result[path.name] = [info.st_size, info.st_mtime_ns]
    return result


def begin_export(observation: dict, root: Path, directory: Path, *, explicit_request: bool) -> dict:
    if not explicit_request:
        raise ValueError("Export import requires an explicit user request.")
    validate_observation(observation)
    directory = directory.expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Download directory does not exist.")
    folder = root / "garant" / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    ticket = Path(tempfile.mkdtemp(prefix="export-", dir=folder)) / "export.json"
    payload = {"schema_version": 1, "state": "awaiting_export", "observation": observation,
               "download_directory": str(directory), "baseline": file_snapshot(directory),
               "created_at": datetime.now(timezone.utc).isoformat()}
    write_json(ticket, payload)
    return {"ticket": str(ticket), "download_directory": str(directory), "state": payload["state"]}


def collect_export(ticket: Path, root: Path, *, explicit_request: bool, wait_seconds: float = 30,
                   expected_name: str | None = None) -> dict:
    """One bounded export pickup, scoped to a previously created ticket, never a background watcher."""
    if not explicit_request:
        raise ValueError("Export import requires an explicit user request.")
    if not 0 <= wait_seconds <= 60:
        raise ValueError("wait_seconds must be between 0 and 60.")
    if expected_name and (Path(expected_name).name != expected_name or "/" in expected_name or "\\" in expected_name):
        raise ValueError("expected_name must be a filename, not a path.")
    ticket = ticket.resolve(strict=True)
    if (root / "garant" / "exports").resolve() not in ticket.parents:
        raise ValueError("Export ticket must belong to this runtime state.")
    from write_lock import exclusive_file_lock
    with exclusive_file_lock(ticket.parent / "collect.lock", "GARANT export pickup"):
        payload = read_json(ticket)
        if payload.get("evidence_file"):
            evidence = Path(payload["evidence_file"])
            return {"evidence_file": str(evidence), **read_json(evidence)}
        if payload.get("state") != "awaiting_export":
            raise ValueError("Export ticket is not awaiting a download.")
        directory = Path(payload["download_directory"]).resolve(strict=True)
        previous = {}
        deadline = time.monotonic() + wait_seconds
        while True:
            current = file_snapshot(directory)
            changed = {name: sig for name, sig in current.items() if payload["baseline"].get(name) != sig}
            # A partial download may still be destined for another candidate. Wait until it finishes.
            partial = any(Path(name).suffix.lower() in PARTIAL_EXTENSIONS for name in changed)
            candidates = [name for name in changed if Path(name).suffix.lower() in DOWNLOAD_EXTENSIONS
                          and (expected_name is None or name == expected_name)]
            if len(candidates) > 1:
                raise ValueError("Several new exports found; select the observed filename with --expected-name.")
            if len(candidates) == 1 and not partial:
                name = candidates[0]
                if previous.get(name) == current[name] and current[name][0] > 0:
                    source = directory / name
                    report = record(payload["observation"], root, explicit_request=True, source=source)
                    payload.update(state="collected", evidence_file=report["evidence_file"])
                    write_json(ticket, payload)
                    return report
            if time.monotonic() >= deadline:
                return {"state": "awaiting_export", "ticket": str(ticket), "download_directory": str(directory),
                        "reason": "No single completed export is available yet.", "archived": False}
            previous = current
            time.sleep(min(1, max(0, deadline - time.monotonic())))


def import_evidence(path: Path, root: Path, digest: str) -> dict:
    path = path.resolve(strict=True)
    if (root / "garant" / "downloads").resolve() not in path.parents:
        raise ValueError("GARANT evidence must belong to this runtime state's download bundles.")
    evidence = read_json(path)
    validated = validate_observation({key: evidence[key] for key in FIELDS if key in evidence})
    if evidence.get("topic") != validated["topic"]:
        raise ValueError("GARANT evidence topic does not match its document URL.")
    download = evidence.get("download", {})
    original = Path(download.get("file", "")).resolve(strict=True)
    if original.parent != path.parent or download.get("sha256") != digest:
        raise ValueError("GARANT export does not match the staged document.")
    if hashlib.sha256(read_original(original)).hexdigest() != digest:
        raise ValueError("GARANT export changed after collection.")
    return {"provider": "GARANT", "transport": "browser", "topic": evidence["topic"],
            "source_url": evidence["source_url"], "checked_at": evidence["checked_at"],
            "provider_status": evidence.get("provider_status"), "revision_label": evidence.get("revision_label"),
            "revision_status": evidence.get("revision_status"), "evidence_file": str(path),
            "evidence_level": "reference_system", "official_status_verified": False}


def import_status(evidence: Path, root: Path, kb_root: Path) -> dict:
    report = read_json(evidence)
    digest = report.get("download", {}).get("sha256")
    import_evidence(evidence, root, digest)
    from index_store import index_path
    import yaml
    registry = yaml.safe_load(index_path(kb_root).read_text(encoding="utf-8")) or {}
    matches = []
    for doc in registry.get("documents", []):
        if doc.get("source_sha256") != digest:
            continue
        original = (kb_root / doc.get("original_path", "")).resolve()
        if kb_root.resolve() not in original.parents or not original.is_file():
            continue
        if hashlib.sha256(read_original(original)).hexdigest() == digest and (kb_root / doc["path"]).is_file():
            matches.append({"id": doc["id"], "card": str(kb_root / doc["path"]),
                            "original": str(original), "lifecycle_stage": doc.get("lifecycle_stage")})
    return {"archived": bool(matches), "documents": matches, "evidence_file": str(evidence)}


def validate_observation(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError("Observation must contain only documented fields; do not include credentials or session URLs.")
    for key in ("source_url", "title", "checked_at", "observation_text"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Missing observation field: {key}")
    if any(value is not None and not isinstance(value, str) for value in data.values()):
        raise ValueError("Observation fields must be strings or null.")
    url = urlsplit(data["source_url"])
    if url.scheme != "https" or url.netloc != "internet.garant.ru" or url.query or url.path not in {"", "/"}:
        raise ValueError("Use the clean HTTPS internet.garant.ru document link, without query/session parameters.")
    match = re.fullmatch(r"/document/([1-9][0-9]{0,17})(?:/(?:entry|paragraph)/[0-9:]+)?/?", url.fragment)
    if not match:
        raise ValueError("Unrecognized document link; copy the document link from GARANT.")
    checked = datetime.fromisoformat(data["checked_at"].replace("Z", "+00:00"))
    if checked.tzinfo is None or checked > datetime.now(timezone.utc):
        raise ValueError("checked_at must include timezone and must not be in the future.")
    return {
        **data, "schema_version": 1, "provider": "GARANT", "transport": "browser",
        "topic": int(match[1]), "recorded_at": datetime.now(timezone.utc).isoformat(),
        "evidence_level": "reference_system", "official_status_verified": False,
        "result": "provider_status_observed" if (data.get("provider_status") or "").strip() else "status_unknown",
        "requires_expert_review": True,
    }


def read_original(source: Path) -> bytes:
    if source.suffix.lower() not in DOWNLOAD_EXTENSIONS:
        raise ValueError("Archive intake supports PDF/DOCX/TXT/MD; preserve other exports and convert safely first.")
    with source.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    if not data or len(data) > MAX_FILE:
        raise ValueError("Download is empty or exceeds 50 MiB.")
    if source.suffix.lower() == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("The downloaded file is not a PDF.")
    if source.suffix.lower() == ".docx":
        import io
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("The downloaded file is not a DOCX.")
        except zipfile.BadZipFile:
            raise ValueError("The downloaded file is not a DOCX.") from None
    return data


def record(observation: dict, root: Path, *, explicit_request: bool, source: Path | None = None) -> dict:
    if not explicit_request:
        raise ValueError("Use only after an explicit GARANT request from the user (--explicit-request).")
    report = validate_observation(observation)
    data = read_original(source) if source is not None else None
    destination = root / "garant" / ("downloads" if source is not None else "checks")
    destination.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f"{report['topic']}-", dir=destination))
    if data is not None:
        original = folder / f"garant-{report['topic']}{source.suffix.lower()}"
        original.write_bytes(data)
        report["download"] = {"file": str(original), "sha256": hashlib.sha256(data).hexdigest(),
                              "bytes": len(data), "source_name": source.name,
                              "security_status": "not_checked", "archive_status": "not_archived"}
    evidence = folder / "evidence.json"
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"evidence_file": str(evidence), **report}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("connection", help="Show browser entry points; no network, login or credential checks")
    intake = commands.add_parser("record", help="Save an observed browser status and optionally a downloaded file")
    intake.add_argument("observation", type=Path, help="JSON observation from the browser, without credentials")
    intake.add_argument("--source", type=Path, help="Completed local browser download")
    intake.add_argument("--explicit-request", action="store_true", required=True)
    begin = commands.add_parser("begin-export", help="Arm a single browser export import before clicking Save")
    begin.add_argument("observation", type=Path)
    begin.add_argument("--download-dir", type=Path, default=Path.home() / "Downloads")
    begin.add_argument("--explicit-request", action="store_true", required=True)
    collect = commands.add_parser("collect-export", help="Pick up the completed file from this export only")
    collect.add_argument("ticket", type=Path)
    collect.add_argument("--wait-seconds", type=float, default=30)
    collect.add_argument("--expected-name")
    collect.add_argument("--explicit-request", action="store_true", required=True)
    status = commands.add_parser("import-status", help="Verify that the collected export exists in the KB")
    status.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "connection":
            result = {"mode": "browser_explicit_only", "login_url": "https://account.garant.ru/",
                      "documents_url": "https://internet.garant.ru/", "api_required": False,
                      "network_checked": False, "credentials_stored": False}
        else:
            from kb_root import resolve_state_root
            root = resolve_state_root()
            if args.command == "collect-export":
                result = collect_export(args.ticket, root, explicit_request=args.explicit_request,
                                        wait_seconds=args.wait_seconds, expected_name=args.expected_name)
            elif args.command == "import-status":
                from kb_root import resolve_kb_root
                result = import_status(args.evidence, root, resolve_kb_root())
            elif args.command == "begin-export":
                result = begin_export(read_json(args.observation), root, args.download_dir,
                                      explicit_request=args.explicit_request)
            else:
                result = record(read_json(args.observation), root, explicit_request=args.explicit_request, source=args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({"error": str(error), "status_verified": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
