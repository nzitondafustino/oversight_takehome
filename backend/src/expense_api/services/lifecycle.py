"""The ruleset lifecycle: draft → in_review → approved → published, and a recorded rejection back to draft.

Each transition is one conditional, atomic update of the version, which also appends its audit event, so two
requests can never both move a version, and the history can never miss a move. Approval and publication are
decided by the core's PolicyCompiler; nothing here can approve content the compiler would not publish.
"""

from expense_validator import Approval

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.schemas.rulesets import ApproveRequest, PublishRequest, ReviewRequest, TransitionRequest
from expense_api.services.compilation import Compilation, parse_approval
from expense_api.services.rulesets import RulesetService, now, sha256


class LifecycleService:
    def __init__(self, settings: Settings, rulesets: RulesetService, compilation: Compilation):
        self.settings, self.rulesets, self.compilation = settings, rulesets, compilation

    def submit(self, ruleset_id: str, request: TransitionRequest) -> dict:
        record = self._at(ruleset_id, "draft")
        # A new submission starts a new review: the previous one, a rejection, stays in the transitions.
        return self._move(record, "in_review", {"submitted_at": now(), "review": None}, request.actor, request.reason)

    def review(self, ruleset_id: str, request: ReviewRequest) -> dict:
        record = self._at(ruleset_id, "in_review")
        if record.get("review") is not None:
            raise ApiError(409, "already_reviewed", "this submission already has a review decision")
        review = {"decision": request.decision, "reviewer": _claimed(request.actor), "reason": request.reason, "decided_at": now(), "policy": None, "decisions": None, "review_sha256": None}
        if request.decision == "rejected":
            return self._move(record, "draft", {"review": review}, request.actor, request.reason, expected={"review": None})
        decisions, policy = request.decisions, request.review_policy
        prepared = (record.get("compilation") or {}).get("prepared_review")
        if decisions is None and prepared is not None:  # the extraction review the version was made from
            decisions, policy = prepared["decisions"], prepared["policy"]
        if decisions is not None:
            if record.get("compilation") is None:
                raise ApiError(422, "not_compiled", "review decisions apply to a compiled version's extraction, and this version was uploaded")
            result = self.compilation.review(record, decisions, policy)
            if not result.ready_for_approval:
                issues = [{"code": issue.code, "comparison_id": issue.comparison_id, "message": issue.message} for issue in result.issues if issue.level == "error"]
                raise ApiError(422, "review_not_ready", "the compiler does not accept this review for approval", {"remaining": list(result.remaining), "issues": issues})
            review |= {"policy": policy, "decisions": decisions, "review_sha256": result.review_sha256}
        elif record.get("compilation") is not None and not record["compilation"]["ready_for_approval"]:
            raise ApiError(422, "review_not_ready", "the extraction has fields that need a review decision", {"blockers": record["compilation"]["blockers"]})
        return self._move(record, "in_review", {"review": review}, request.actor, request.reason, expected={"review": None})

    def approve(self, ruleset_id: str, request: ApproveRequest) -> dict:
        record = self._at(ruleset_id, "in_review")
        if (record.get("review") or {}).get("decision") != "accepted":
            raise ApiError(409, "review_required", "only a version whose review was accepted can be approved")
        if record.get("compilation") is None:
            raise ApiError(409, "approval_unverifiable", "this version is an uploaded artifact: the compiler's approval covers only a version it compiled from policy documents")
        if request.mode not in self.settings.allowed_publication_modes:
            raise ApiError(422, "publication_mode_not_allowed", f"this deployment does not publish in {request.mode} mode", {"allowed": sorted(self.settings.allowed_publication_modes)})
        rebuilt = self.compilation.rebuild(record)
        approval = parse_approval(request.approval) if request.approval is not None else self.compilation.approve(rebuilt, request.actor, request.note)
        self.compilation.verify(rebuilt, record, approval, request.mode)
        text = approval.model_dump_json()  # the core's record, kept exactly; its hash is the approval reference
        changes = {"approved_at": now(), "approval_json": text, "approval_reference": sha256(text), "approval_mode": request.mode}
        return self._move(record, "approved", changes, request.actor, request.note or None, expected={"review.decision": "accepted"})

    def publish(self, ruleset_id: str, request: PublishRequest) -> dict:
        record = self.rulesets.get(ruleset_id)
        if record["status"] == "published":
            return self._already_published(record, request)
        record = self._at(ruleset_id, "approved", record)
        if request.approval_reference not in (None, record["approval_reference"]):
            raise ApiError(409, "publication_conflict", "this version was approved with another approval")
        # The approval must still hold now: the compiler rebuilds the candidate and runs its publication safeguards.
        approval = Approval.model_validate_json(record["approval_json"])
        self.compilation.verify(self.compilation.rebuild(record), record, approval, record["approval_mode"])
        changes = {"published_at": now(), "publication_mode": record["approval_mode"]}
        event = _event(record, "published", request.actor, None, record["approval_reference"])
        updated = self.rulesets.repository.transition(ruleset_id, {"status": "approved", "approval_reference": record["approval_reference"]}, changes | {"status": "published"}, event)
        if updated is None:  # another request moved it first: the same publication is no conflict
            return self._already_published(self.rulesets.get(ruleset_id), request)
        return updated

    def _already_published(self, record: dict, request: PublishRequest) -> dict:
        if record["status"] != "published":
            raise ApiError(409, "concurrent_modification", f"the version changed while this request ran, and is now {record['status']}")
        if request.approval_reference not in (None, record["approval_reference"]):
            raise ApiError(409, "publication_conflict", "this version is already published with another approval")
        return record  # idempotent: the same version, content and approval

    def _at(self, ruleset_id: str, status: str, record: dict | None = None) -> dict:
        record = record or self.rulesets.get(ruleset_id)
        if record["status"] != status:
            raise ApiError(409, "invalid_transition", f"this step needs a {status} version, and this one is {record['status']}", {"status": record["status"]})
        return record

    def _move(self, record: dict, to_status: str, changes: dict, actor: str | None, reason: str | None, expected: dict | None = None) -> dict:
        reference = changes.get("approval_reference", record.get("approval_reference"))
        updated = self.rulesets.repository.transition(
            record["_id"], {"status": record["status"], "artifact_sha256": record["artifact_sha256"], **(expected or {})},
            changes | {"status": to_status}, _event(record, to_status, actor, reason, reference),
        )
        if updated is None:
            raise ApiError(409, "concurrent_modification", "the version changed while this request ran: read it again")
        return updated


def _claimed(actor: str | None) -> dict | None:
    return None if actor is None else {"claimed": actor, "verified": False}


def _event(record: dict, to_status: str, actor: str | None, reason: str | None, approval_reference: str | None) -> dict:
    return {"from_status": record["status"], "to_status": to_status, "at": now(), "actor": _claimed(actor), "content_hash": record["artifact_sha256"], "approval_reference": approval_reference, "reason": reason}
