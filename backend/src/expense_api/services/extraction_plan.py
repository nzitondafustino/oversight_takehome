"""Resolve immutable document manifests and scope compiler logic; never call a provider or mutate a policy."""
import json
from dataclasses import dataclass

import yaml
from expense_validator import CompilerSettings
from expense_validator.compiler.extract import extract_document
from expense_validator.policy.vocabulary import RULE_ID_PARAMS

from expense_api.errors import ApiError
from expense_api.services.rulesets import verified_artifact


@dataclass(frozen=True)
class ExtractionPlan:
    revisions: list[dict]
    logic_snapshot: str | None
    recording_set: str | None
    recorded_with: dict | None
    report: dict | None


def resolve(service, request) -> ExtractionPlan:
    if request.run_mode is None:
        return ExtractionPlan(service._revisions([(item.document_id, item.revision) for item in request.documents]), None,
            request.recording_set, None if request.recorded_with is None else request.recorded_with.model_dump(), None)
    collection = service.repository.rulesets
    base = None
    if request.base_ruleset_id or request.run_mode != "full":
        query = {"status": "published", "deleted_at": {"$exists": False}}
        if request.base_ruleset_id:
            query["_id"] = request.base_ruleset_id
        if request.ruleset_name:
            query["name"] = request.ruleset_name
        base = collection.find_one(query, sort=[("published_at", -1), ("_id", -1)])
        if base is None or base.get("compilation") is None:
            raise ApiError(409, "compiled_baseline_required", "select a compiled published baseline by base_ruleset_id or ruleset_name")
        verified_artifact(base)
    excluded = set(((((base or {}).get("compilation") or {}).get("run")) or {}).get("excluded_document_ids", []))
    def latest(filename=None):
        offset = 0
        while True:
            records, total = service.documents.page(filename, 100, offset)
            yield from records
            offset += len(records)
            if offset >= total or not records:
                break

    def hidden(record):
        kept = None if not record.get("duplicate_of") else service.documents.repository.metadata(record["duplicate_of"])
        return kept is not None and kept["content_sha256"] == record["content_sha256"]

    selected = {}
    def select(record):
        previous = selected.get(record["document_id"])
        if previous and previous["revision"] != record["revision"]:
            raise ApiError(422, "conflicting_revisions", "select only one revision of each document")
        selected[record["document_id"]] = record
    for ref in request.documents:
        select(service.documents.get(ref.document_id, ref.revision))
    for identifier in request.document_ids:
        select(service.documents.get(identifier))
    for filename in request.filenames:
        matches = [item for item in latest(filename) if not hidden(item)]
        if len(matches) != 1:
            raise ApiError(409, "ambiguous_document", f"{filename}: expected one document, found {len(matches)}; provide document_ids", {"document_ids": [item["document_id"] for item in matches]})
        select(service.documents.get(matches[0]["document_id"]))
    baseline = {}
    if base:
        entries = base["compilation"]["documents"]
        texts = service.compilation.documents.resolve(entries)
        for entry, text in zip(entries, texts):
            # Older inline compilations used external IDs: resolve their exact content to a library revision.
            record = service.documents.repository.collection.find_one({"document_id": entry["document_id"], "content_sha256": entry["sha256"], **({"revision": entry["revision"]} if "revision" in entry else {})}, sort=[("revision", 1)])
            if record is None:
                record = service.documents.repository.collection.find_one({"filename": entry["filename"], "content_sha256": entry["sha256"]}, sort=[("document_id", 1), ("revision", 1)])
            if record is None:
                raise ApiError(409, "baseline_document_unavailable", f"upload the source document {entry['filename']} to the document library before an incremental run")
            revision = service.documents.get(record["document_id"], record["revision"])
            baseline[entry["filename"]] = revision
    if not selected and request.run_mode != "remove":
        eligible = [record for record in latest() if not hidden(record) and record["document_id"] not in excluded]
        names = [record["filename"] for record in eligible]
        if len(names) != len(set(names)):
            raise ApiError(409, "ambiguous_document", "automatic selection found duplicate filenames; select explicit document_ids")
        for record in eligible:
            if request.run_mode == "full" or record["filename"] not in baseline or record["content_sha256"] != baseline[record["filename"]]["content_sha256"]:
                select(service.documents.get(record["document_id"]))
    if not selected:
        raise ApiError(422, "no_document_changes", "provide documents to remove, or documents with new content for an incremental run")
    if len({item["filename"] for item in selected.values()}) != len(selected):
        raise ApiError(409, "duplicate_filename", "a policy candidate must have one document per filename")
    changed = {item["filename"]: item for item in selected.values()}
    if request.run_mode == "remove":
        if any(name not in baseline or changed[name]["document_id"] != baseline[name]["document_id"] for name in changed):
            raise ApiError(422, "document_not_in_baseline", "only documents in the selected baseline can be removed")
        final = {name: item for name, item in baseline.items() if name not in changed}
    elif request.run_mode == "incremental":
        final = baseline | changed
    else:
        final = changed
    if not final or len(final) > 50:
        raise ApiError(422, "invalid_document_manifest", "a policy candidate needs between 1 and 50 documents")
    config = CompilerSettings.from_config(service.settings.core_config, mode="replay")
    logic = yaml.safe_load(config.rule_logic_path.read_text())
    clauses = {}
    for name, item in final.items():
        try:
            for clause in extract_document(item["content"], name).clauses:
                if clause.rule_id in clauses:
                    raise ValueError(f"duplicate rule ID {clause.rule_id}")
                clauses[clause.rule_id] = name
        except ValueError as error:
            raise ApiError(422, "invalid_documents", str(error)) from None
    old = [] if base is None else json.loads(verified_artifact(base))["rules"]
    retained = [rule for rule in old if request.run_mode != "full" and rule["policy_doc"] not in changed]
    retained_ids = {rule["rule_id"] for rule in retained}
    def source(entry):
        return entry["modifies"][0] if entry["kind"] == "carve_out" else entry["rule_id"]
    entries = [entry for entry in logic["rules"] if source(entry) in clauses and entry["rule_id"] not in retained_ids]
    # A clause no rule implements is compiled anyway: the build stops on it and reports what the clause states, so a
    # reviewer can add its rule (a rule override with add), here or in the published version this run starts from.
    carried = [item for item in ((base or {}).get("compilation") or {}).get("rule_overrides") or [] if item.get("add")]
    added_by_review = ({change.rule_id for change in request.rule_overrides if change.add} | {item["rule_id"] for item in carried}) & set(clauses)
    # References to a removed rule have no target. Prune only those edges, retaining every independent value.
    removed_ids = {rule["rule_id"] for rule in old} - {entry["rule_id"] for entry in [*entries, *retained]}
    added_ids = {entry["rule_id"] for entry in entries} - {rule["rule_id"] for rule in old}
    templates = {entry["rule_id"]: entry for entry in logic["rules"]}
    dependency_ids = []
    kept = []
    for original in retained:
        rule = json.loads(json.dumps(original))
        if rule.get("replaces"):
            rule["replaces"] = [target for target in rule["replaces"] if target["rule_id"] not in removed_ids]
        for name in RULE_ID_PARAMS & set(rule.get("params", {})):
            rule["params"][name]["values"] = [identifier for identifier in rule["params"][name]["values"] if identifier not in removed_ids]
        template = templates.get(rule["rule_id"], {})
        for name in RULE_ID_PARAMS & set(rule.get("params", {})):
            defaults = template.get("params", {}).get(name, {}).get("values", [])
            current = rule["params"][name]["values"]
            additions = [identifier for identifier in defaults if identifier in added_ids and identifier not in current]
            if additions:
                values = set(current + additions)
                rule["params"][name]["values"] = [identifier for identifier in defaults if identifier in values] + [identifier for identifier in current if identifier not in defaults]
        if rule.get("kind") == "check":
            targets = rule.get("replaces", [])
            rule["replaces"] = targets + [target for target in template.get("replaces", []) if target["rule_id"] in added_ids and target not in targets]
        if rule != original:
            dependency_ids.append(rule["rule_id"])
            entries.append({key: value for key, value in rule.items() if key not in ("policy_doc", "provenance")})
        else:
            kept.append(original)
    retained = kept
    retained_ids = {rule["rule_id"] for rule in retained}
    allowed = {entry["rule_id"] for entry in entries} | (added_by_review - retained_ids)
    if any(change.rule_id not in allowed for change in request.rule_overrides):
        raise ApiError(422, "unaffected_rule_override", "incremental overrides may change only rules derived from affected documents")
    snapshot = yaml.safe_dump({"ruleset_version": logic["ruleset_version"], "rules": entries, "retained_rules": retained,
        "rule_order": [rule["rule_id"] for rule in old] + [entry["rule_id"] for entry in entries if entry["rule_id"] not in {rule["rule_id"] for rule in old}]}, sort_keys=False)
    compiled = {} if base is None else base["compilation"]
    recording_set = request.recording_set or compiled.get("recording_set")
    generation = None if request.recorded_with is None else request.recorded_with.model_dump()
    if generation is None:
        generation = compiled.get("recorded_with")
    if request.run_mode != "full" and request.execution_mode == "replay" and (recording_set != compiled["recording_set"] or generation != compiled.get("recorded_with")):
        raise ApiError(422, "baseline_readings_required", "incremental and removal runs must reuse the baseline recording set and generation settings")
    affected = [entry["rule_id"] for entry in entries] + sorted(added_by_review - retained_ids - {entry["rule_id"] for entry in entries})
    removed = sorted(rule["rule_id"] for rule in old if rule["rule_id"] not in retained_ids | allowed | (set(clauses) - {entry["rule_id"] for entry in entries}))
    return ExtractionPlan(sorted(final.values(), key=lambda item: item["filename"]), snapshot, recording_set, generation,
        {"mode": request.run_mode, "base_ruleset_id": None if base is None else base["_id"], "ruleset_name": request.ruleset_name or (base or {}).get("name"),
         "affected_rule_ids": affected, "dependency_rule_ids": dependency_ids, "retained_rule_ids": sorted(retained_ids), "removed_rule_ids": removed,
         "excluded_document_ids": sorted(excluded | set(selected) if request.run_mode == "remove" else excluded - set(selected)),
         "changed_document_ids": list(selected), "removed_document_ids": list(selected) if request.run_mode == "remove" else [],
         "documents": [{"document_id": item["document_id"], "revision": item["revision"], "filename": item["filename"]} for item in sorted(final.values(), key=lambda item: item["filename"]) ]})
