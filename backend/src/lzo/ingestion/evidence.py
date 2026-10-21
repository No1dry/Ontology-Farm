"""Typed source metadata facade over F04's sole artifact/version/review engine."""
from dataclasses import asdict
from uuid import UUID, uuid4
from pydantic import ValidationError, TypeAdapter
from sqlalchemy import select
from lzo.artifacts.service import ArtifactService, _permission, _scope
from lzo.artifacts.store import ArtifactStore, StagedBlob
from lzo.contracts import BlobRef, ResourceScope, RevisionRef
from lzo.errors import ApiError, Conflict
from lzo.governance.models import revisions, resource_heads
from lzo.governance.revisions import append_revision
from lzo.artifacts.models import artifact_reviews
from lzo.ingestion.schemas import EvidenceCreate, EvidenceView
from lzo.ingestion.sources import SourceService, add_scoped, bind


def metadata_command(metadata):
    try:
        if metadata.get("evidence_policy") != "evidence-v1" or metadata.get("high_risk") is not True:
            raise ValueError()
        command = EvidenceCreate.model_validate(metadata["evidence"])
        if command.scope.resource_id is None:
            raise ValueError()
        if command.source_id:
            pin = TypeAdapter(RevisionRef).validate_python(metadata["source_revision"])
            if pin.resource_id != command.source_id or pin.revision < 1:
                raise ValueError()
        elif metadata.get("source_revision") is not None or command.data_nature != "imported_unverified":
            raise ValueError()
        return command
    except (ValidationError, ValueError, KeyError, TypeError, AttributeError):
        raise ApiError("NOT_FOUND", 404, "Evidence not found") from None


class EvidenceService:
    def __init__(self, uow=None, store=None, settings=None):
        self.uow, self.store, self.settings = uow, store, settings

    def preflight(self, uow, actor, command, expected_revision=None):
        references = []
        if command.source_id:
            source = SourceService(uow).read(actor, command.source_id, "source.manage")
            if command.data_nature != source.configuration.data_nature:
                raise ApiError("SOURCE_CONFIGURATION_MISMATCH", 422, "Evidence does not match its source")
            row = uow.session.execute(select(resource_heads).where(resource_heads.c.resource_id == command.source_id)).mappings().one()
            references.append(RevisionRef(command.source_id, row["revision"], row["event_id"]))
        elif command.data_nature != "imported_unverified":
            raise ApiError("SOURCE_REQUIRED", 422, "Evidence without a source is imported unverified")
        for pin in command.target_refs:
            _permission(uow, actor, pin.resource_id, "edit")
            if uow.session.execute(select(revisions.c.event_id).where(revisions.c.resource_id == pin.resource_id,
                revisions.c.revision == pin.revision, revisions.c.event_id == pin.event_id)).first() is None:
                raise ApiError("INVALID_REVISION_PIN", 422, "Evidence reference is not an actual revision")
            references.append(pin)
        if command.replaces_id:
            previous = ArtifactService(uow, self.store, self.settings)._version(actor, command.replaces_id, "edit")
            if _scope(uow, previous["resource_id"])["resource_kind"] != "evidence":
                raise ApiError("NOT_FOUND", 404, "Evidence not found")
            owner = previous["resource_id"]
            scope = _scope(uow, owner)
            if command.scope.resource_id not in (None, owner) or (
                command.scope.owner_scope_id, list(map(str, command.scope.related_scope_ids)), command.scope.security_class
                ) != (scope["owner_scope_id"], scope["related_scope_ids"], scope["security_class"]):
                raise ApiError("INVALID_SCOPE", 422, "Evidence scope cannot be transferred")
            if expected_revision is None:
                raise ApiError("PRECONDITION_REQUIRED", 428, "Evidence replacement requires owner revision")
            # Revision conflicts are checked inside registration so an already
            # committed idempotent replay can retain its original condition.
        else:
            owner = uuid4()
            add_scoped(uow, actor, owner, command.scope, "evidence", "evidence.manage")
        return owner, references

    def create(self, uow, actor, stream, media_type, command, *, expected_revision=None):
        owner, references = self.preflight(uow, actor, command, expected_revision)
        store = self.store or ArtifactStore(self.settings.file_root / "artifacts", max_upload_bytes=self.settings.artifact_max_upload_bytes)
        blob = stream if isinstance(stream, (BlobRef, StagedBlob)) else store.put(stream, media_type)
        typed = command.model_copy(update={"scope": ResourceScope(owner, command.scope.owner_scope_id,
            command.scope.related_scope_ids, command.scope.security_class)})
        metadata = {"evidence_policy": "evidence-v1", "evidence": typed.model_dump(mode="json"),
            "source_revision": asdict(references[0]) if command.source_id else None, "high_risk": True}
        if metadata["source_revision"]:
            metadata["source_revision"] = {"resource_id":str(references[0].resource_id),
                "revision":references[0].revision,"event_id":str(references[0].event_id)}
        if not command.replaces_id:
            event = append_revision(uow, actor, owner, 0, metadata, "evidence.created")
            bind(uow, event, [r.resource_id for r in references])
        else:
            # The old version is itself a disclosed immutable dependency.
            current = uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id == owner)).scalar_one()
            if current != expected_revision:
                raise Conflict("REVISION_CONFLICT", "Evidence owner revision has changed")
        detail = ArtifactService(uow, store, self.settings).register_evidence(actor, owner, blob,
            filename=command.title, metadata=metadata, previous_version_id=command.replaces_id,
            expected_resource_revision=expected_revision if command.replaces_id else 1,
            references=[r.resource_id for r in references] + ([command.replaces_id] if command.replaces_id else []))
        return EvidenceService(uow, store, self.settings).read(actor, detail.version_id)

    def read(self, actor, evidence_id):
        detail = ArtifactService(self.uow, self.store, self.settings).get(actor, evidence_id)
        if _scope(self.uow, detail.resource_id)["resource_kind"] != "evidence":
            raise ApiError("NOT_FOUND", 404, "Evidence not found")
        command = metadata_command(detail.metadata)
        head = self.uow.session.execute(select(resource_heads).where(resource_heads.c.resource_id == evidence_id)).mappings().one()
        decision = self.uow.session.execute(select(artifact_reviews.c.decision).join(revisions,
            revisions.c.event_id == artifact_reviews.c.event_id).where(artifact_reviews.c.version_id == evidence_id)
            .order_by(revisions.c.revision.desc(), revisions.c.event_id.desc()).limit(1)).scalar_one_or_none()
        return EvidenceView(id=evidence_id, revision=RevisionRef(evidence_id, head["revision"], head["event_id"]),
            blob=BlobRef(detail.sha256, detail.size_bytes, detail.media_type, evidence_id),
            **command.model_dump(exclude={"replaces_id"}), replaces_id=detail.previous_version_id,
            review_state=decision if decision in {"accepted", "rejected"} else "candidate", created_at=detail.recorded_at)
