"""Artifact registration, current-right reads and append-only evidence events."""
import json
import hashlib
from collections.abc import Iterable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lzo.artifacts.models import artifact_links, artifact_quotas, artifact_reviews, artifact_versions
from lzo.artifacts.schemas import ArtifactDetail, ArtifactLinkDetail, ArtifactMutationDetail, ArtifactReviewDetail
from lzo.artifacts.store import StagedBlob
from lzo.storage.blobs import get_blob_store
from lzo.auth.models import resource_scopes
from lzo.auth.permissions import authorized_statement, require_permission, validate_actor
from lzo.contracts import ActorContext, BlobRef, JsonObject, ResourceScope, RevisionRef, UnitOfWork
from lzo.config import Settings
from lzo.errors import ApiError, Conflict, Forbidden
from lzo.governance.history import revision_metadata, revision_visibility_dependencies
from lzo.governance.models import resource_heads, revisions
from lzo.governance.revisions import append_revision, lock_ledger


def _scope(uow: UnitOfWork, resource_id: UUID):
    row = uow.session.execute(select(resource_scopes).where(
        resource_scopes.c.resource_id == resource_id)).mappings().one_or_none()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    return row


def _permission(uow: UnitOfWork, actor: ActorContext, resource_id: UUID, action: str):
    row = _scope(uow, resource_id)
    scope = ResourceScope(resource_id, row["owner_scope_id"],
                          tuple(UUID(value) for value in row["related_scope_ids"]), row["security_class"])
    try:
        require_permission(actor, "view", scope, uow=uow)
    except Forbidden:
        raise ApiError("NOT_FOUND", 404, "Resource not found") from None
    if row["resource_kind"] in {"schema.draft", "schema.release", "schema.bundle", "schema.catalog", "catalog.model", "catalog.document", "catalog.device"}:
        hidden = uow.session.execute(select(revision_visibility_dependencies.c.resource_id).join(
            resource_heads, resource_heads.c.event_id == revision_visibility_dependencies.c.event_id).where(
            resource_heads.c.resource_id == resource_id,
            revision_visibility_dependencies.c.resource_id.not_in(authorized_statement(actor, "view"))).limit(1)).first()
        if hidden:
            raise ApiError("NOT_FOUND", 404, "Resource not found")
    if action != "view":
        require_permission(actor, action, scope, uow=uow)
    return row


def _catalog_write_guard(scope):
    if scope['resource_kind'] == 'telemetry.batch':
        raise Forbidden('TELEMETRY_COMMAND_REQUIRED', 'Use immutable telemetry manifests and explicit retry')
    if scope['resource_kind'] in {'catalog.model', 'catalog.document', 'catalog.device'}:
        raise Forbidden('CATALOG_COMMAND_REQUIRED', 'Use versioned catalog attachment references')


def attach_evidence(uow: UnitOfWork, actor: ActorContext, resource_id: UUID,
                    blob: BlobRef, metadata: JsonObject) -> RevisionRef:
    """Register a fresh put result and attach it in the caller's UoW.

    A producer may supply ``uow.artifact_settings`` for nondefault quota
    policy; ordinary runtime UoWs read the configured Settings environment.
    """
    return _attach_evidence(uow, actor, resource_id, blob, metadata, action="edit")


def _attach_evidence(uow, actor, resource_id, blob, metadata, *, action):
    """Trusted producer primitive; public evidence retains its edit action."""
    scope = _permission(uow, actor, resource_id, action)
    _catalog_write_guard(scope)
    lock_ledger(uow)
    version = uow.session.execute(select(artifact_versions).where(
        artifact_versions.c.id == blob.version_id)).mappings().one_or_none()
    if version is None:
        if not isinstance(metadata, dict):
            raise ApiError("INVALID_METADATA", 422, "Artifact metadata must be a JSON object")
        filename = metadata.get("filename", "artifact")
        if not isinstance(filename, str):
            raise ApiError("INVALID_FILENAME", 422, "Invalid artifact filename")
        settings = getattr(uow, "artifact_settings", None) or Settings()
        store = getattr(uow, "artifact_store", None) or get_blob_store(settings)
        with store.open(blob):
            pass
        ArtifactService(uow, None, settings)._register_upload(actor, resource_id, blob,
            filename=filename, metadata=metadata, action=action)
        return _attach_evidence(uow, actor, resource_id, blob, metadata, action=action)
    if version["resource_id"] != resource_id:
        raise ApiError("NOT_FOUND", 404, "Artifact not found")
    if (version["sha256"], version["size_bytes"], version["media_type"]) != (
            blob.sha256, blob.size_bytes, blob.media_type):
        raise Conflict("BLOB_REF_CONFLICT", "Artifact reference does not match its registered version")
    lock_id = int.from_bytes(hashlib.sha256(blob.version_id.bytes).digest()[:8], "big", signed=True)
    uow.session.execute(select(func.pg_advisory_xact_lock(lock_id))).scalar_one()
    existing = uow.session.execute(select(revisions).join(revision_metadata,
        revisions.c.event_id == revision_metadata.c.event_id).where(
        revisions.c.resource_id == resource_id,
        revisions.c.event_type == "evidence.attached",
        revision_metadata.c.evidence_refs.contains([str(blob.version_id)]))).mappings().one_or_none()
    if existing is not None:
        if existing["payload"].get("metadata") != metadata:
            raise Conflict("EVIDENCE_CONFLICT", "Evidence was attached with different metadata")
        return RevisionRef(resource_id, existing["revision"], existing["event_id"])
    current = uow.session.execute(select(resource_heads.c.revision).where(
        resource_heads.c.resource_id == resource_id)).scalar_one_or_none()
    if current is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    protected = uow.session.execute(select(revision_visibility_dependencies.c.resource_id).join(
        resource_heads, resource_heads.c.event_id == revision_visibility_dependencies.c.event_id).where(
        resource_heads.c.resource_id == resource_id)).scalars().all()
    ref = append_revision(uow, actor, resource_id, current,
        {"evidence_version_id": str(blob.version_id), "sha256": blob.sha256,
         "size_bytes": blob.size_bytes, "metadata": metadata}, "evidence.attached")
    uow.session.execute(insert(revision_metadata).values(
        event_id=ref.event_id, evidence_refs=[str(blob.version_id)], proof_refs=[]))
    for dependency in set(protected):
        uow.session.execute(pg_insert(revision_visibility_dependencies).values(
            event_id=ref.event_id, resource_id=dependency).on_conflict_do_nothing())
    return ref


class ArtifactService:
    versions_table = artifact_versions

    def __init__(self, uow: UnitOfWork, store, settings):
        self.uow = uow
        self.store = store
        self.settings = settings

    def authorize_upload(self, actor: ActorContext, resource_id: UUID) -> None:
        scope = _permission(self.uow, actor, resource_id, self._upload_action(resource_id))
        _catalog_write_guard(scope)
        if _scope(self.uow, resource_id)["resource_kind"] in {"evidence", "evidence.pending"}:
            raise Forbidden("EVIDENCE_PRODUCER_REQUIRED", "Use the controlled evidence operation")

    def _upload_action(self, resource_id):
        # Deployment-owned resource classification; never a JSON/metadata action.
        row = _scope(self.uow, resource_id)
        return "schema.edit" if row["resource_kind"] == "schema.draft" else "edit"

    def authorize_version(self, actor: ActorContext, version_id: UUID, action="view") -> None:
        self._version(actor, version_id, action)

    def authorize_versions(self, actor: ActorContext, version_ids: Iterable[UUID]) -> None:
        """Read-only ALL-reference check with the existing single-read semantics.

        Ordinary uploads depend on their current owner, schema-owner head pins
        and every artifact.uploaded history dependency. Check those sets once
        per transaction, without a cross-request/chunk authorization cache.
        Evidence and generated exports retain their authoritative specialized
        _version path. This does not authorize mutations or open any content.
        """
        ids = set(version_ids)
        try:
            validate_actor(self.uow, actor)
        except Forbidden:
            raise ApiError('NOT_FOUND', 404, 'Artifact not found') from None
        if not ids:
            return
        rows = self.uow.session.execute(select(artifact_versions.c.id,
            artifact_versions.c.resource_id, resource_scopes.c.resource_kind).join(
            resource_scopes, resource_scopes.c.resource_id == artifact_versions.c.resource_id).where(
            artifact_versions.c.id.in_(ids))).mappings().all()
        if {row['id'] for row in rows} != ids:
            raise ApiError('NOT_FOUND', 404, 'Artifact not found')
        ordinary = [row for row in rows if row['resource_kind'] not in {'evidence', 'semantic.export'}]
        owners = {row['resource_id'] for row in ordinary}
        visible = authorized_statement(actor, 'view')
        if owners:
            hidden_owner = self.uow.session.execute(select(resource_scopes.c.resource_id).where(
                resource_scopes.c.resource_id.in_(owners),
                resource_scopes.c.resource_id.not_in(visible)).limit(1)).first()
            if hidden_owner:
                raise ApiError('NOT_FOUND', 404, 'Artifact not found')
            schema_owners = {row['resource_id'] for row in ordinary if row['resource_kind'] in {
                'schema.draft', 'schema.release', 'schema.bundle', 'schema.catalog'}}
            upload_dependencies = select(revision_visibility_dependencies.c.resource_id).join(
                revisions, revisions.c.event_id == revision_visibility_dependencies.c.event_id).where(
                revisions.c.resource_id.in_([row['id'] for row in ordinary]),
                revisions.c.event_type == 'artifact.uploaded',
                revision_visibility_dependencies.c.resource_id.not_in(visible))
            head_dependencies = select(revision_visibility_dependencies.c.resource_id).join(
                resource_heads, resource_heads.c.event_id == revision_visibility_dependencies.c.event_id).where(
                resource_heads.c.resource_id.in_(schema_owners),
                revision_visibility_dependencies.c.resource_id.not_in(visible))
            if self.uow.session.execute(upload_dependencies.union_all(head_dependencies).limit(1)).first():
                raise ApiError('NOT_FOUND', 404, 'Artifact not found')
        for row in rows:
            if row['resource_kind'] in {'evidence', 'semantic.export'}:
                self.authorize_version(actor, row['id'])

    def authorize_link(self, actor: ActorContext, version_id: UUID, target_resource_id: UUID) -> None:
        self._version(actor, version_id, "edit")
        _permission(self.uow, actor, target_resource_id, "edit")

    def _version(self, actor: ActorContext, version_id: UUID, action="view"):
        row = self.uow.session.execute(select(artifact_versions).where(
            artifact_versions.c.id == version_id)).mappings().one_or_none()
        if row is None:
            raise ApiError("NOT_FOUND", 404, "Artifact not found")
        owner = _permission(self.uow, actor, row["resource_id"], "view")
        if owner["resource_kind"] == "evidence":
            from lzo.ingestion.evidence import metadata_command
            command = metadata_command(row["metadata"])
            if command.scope.resource_id != row["resource_id"]:
                raise ApiError("NOT_FOUND", 404, "Artifact not found")
            _permission(self.uow, actor, version_id, "view")
            if action != "view":
                _permission(self.uow, actor, row["resource_id"], "evidence.manage")
                for pin in command.target_refs:
                    _permission(self.uow, actor, pin.resource_id, "edit")
                if command.source_id:
                    _permission(self.uow, actor, command.source_id, "source.manage")
            action = "view"
        if owner["resource_kind"] == "semantic.export":
            _permission(self.uow, actor, version_id, "view")
            if action != "view":
                raise Forbidden("IMMUTABLE_EXPORT", "Generated exports are immutable")
        hidden = self.uow.session.execute(select(revision_visibility_dependencies.c.resource_id).join(
            revisions, revisions.c.event_id == revision_visibility_dependencies.c.event_id).where(
            revisions.c.resource_id == version_id, revisions.c.event_type == "artifact.uploaded",
            revision_visibility_dependencies.c.resource_id.not_in(authorized_statement(actor, "view"))).limit(1)).first()
        if hidden:
            raise ApiError("NOT_FOUND", 404, "Artifact not found")
        if action != "view":
            _permission(self.uow, actor, row["resource_id"], action)
        return row

    def _charge(self, kind: str, owner_id: UUID, size: int, limit: int):
        self.uow.session.execute(pg_insert(artifact_quotas).values(
            kind=kind, owner_id=owner_id, used_bytes=0).on_conflict_do_nothing())
        used = self.uow.session.execute(select(artifact_quotas.c.used_bytes).where(
            artifact_quotas.c.kind == kind, artifact_quotas.c.owner_id == owner_id)
            .with_for_update()).scalar_one()
        if used + size > limit:
            raise ApiError("ARTIFACT_QUOTA_EXCEEDED", 413, "Artifact quota exceeded")
        self.uow.session.execute(update(artifact_quotas).where(
            artifact_quotas.c.kind == kind, artifact_quotas.c.owner_id == owner_id)
            .values(used_bytes=used + size))

    def register_upload(self, actor: ActorContext, resource_id: UUID, blob: BlobRef, *,
                        filename: str, metadata: JsonObject, previous_version_id: UUID | None = None,
                        expected_resource_revision: int | None = None) -> ArtifactDetail:
        return self._register_upload(actor, resource_id, blob, filename=filename, metadata=metadata,
            previous_version_id=previous_version_id, expected_resource_revision=expected_resource_revision,
            action=self._upload_action(resource_id))

    def register_semantic_export(self, actor, resource_id, blob, *, filename, metadata):
        """Trusted producer entry; immutable binding, never metadata, grants access."""
        from lzo.semantic.exports import authorize_publication
        authorize_publication(self.uow, actor, resource_id)
        return self._register_upload(actor, resource_id, blob, filename=filename,
            metadata=metadata, action="view", semantic_export=True)

    def register_evidence(self, actor, resource_id, blob, *, references, **kwargs):
        """Trusted facade entry, fixed action and immutable pinned dependencies."""
        from lzo.ingestion.evidence import metadata_command
        command = metadata_command(kwargs["metadata"])
        if command.scope.resource_id != resource_id:
            raise ApiError("INVALID_SCOPE", 422, "Evidence owner binding is required")
        return self._register_upload(actor, resource_id, blob, action="evidence.manage",
            evidence_references=tuple(references), **kwargs)

    def _register_upload(self, actor, resource_id, blob, *, filename, metadata,
                         previous_version_id=None, expected_resource_revision=None, action,
                         semantic_export=False, evidence_references=None):
        staged = blob if isinstance(blob, StagedBlob) else None
        blob = staged.ref if staged is not None else blob
        scope = _permission(self.uow, actor, resource_id, action)
        _catalog_write_guard(scope)
        if (scope["resource_kind"] in {"evidence", "evidence.pending"}) != (evidence_references is not None):
            raise Forbidden("EVIDENCE_PRODUCER_REQUIRED", "Use the controlled evidence operation")
        if scope["resource_kind"] == "semantic.export":
            if not semantic_export or previous_version_id is not None:
                raise Forbidden("IMMUTABLE_EXPORT", "Generated exports are immutable")
            from lzo.semantic.exports import authorize_publication
            authorize_publication(self.uow, actor, resource_id)
        elif semantic_export:
            raise Forbidden("IMMUTABLE_EXPORT", "Export owner binding is required")
        budget = self.settings.artifact_resource_budgets.get(scope['resource_kind'], self.settings.artifact_max_upload_bytes)
        if blob.size_bytes < 1 or blob.size_bytes > budget:
            raise ApiError("UPLOAD_TOO_LARGE", 413, "Artifact exceeds upload limit")
        if not isinstance(metadata, dict):
            raise ApiError("INVALID_METADATA", 422, "Artifact metadata must be a JSON object")
        try:
            json.dumps(metadata)
        except (ValueError, TypeError):
            raise ApiError("INVALID_METADATA", 422, "Artifact metadata must be JSON") from None
        filename = filename.replace("\\", "/").split("/")[-1].strip()
        if not filename or len(filename) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in filename):
            raise ApiError("INVALID_FILENAME", 422, "Invalid artifact filename")
        lock_ledger(self.uow)
        current = self.uow.session.execute(select(resource_heads.c.revision).where(
            resource_heads.c.resource_id == resource_id)).scalar_one_or_none()
        if current is None:
            raise ApiError("NOT_FOUND", 404, "Resource not found")
        if expected_resource_revision is not None and current != expected_resource_revision:
            raise Conflict("REVISION_CONFLICT", "Resource revision has changed")
        version_number = 1
        if previous_version_id is not None:
            previous = self._version(actor, previous_version_id)
            if previous["resource_id"] != resource_id:
                raise Conflict("ARTIFACT_VERSION_CONFLICT", "Previous version belongs to another resource")
            version_number = previous["version_number"] + 1
            if self.uow.session.execute(select(artifact_versions.c.id).where(
                artifact_versions.c.previous_version_id == previous_version_id)).first():
                raise Conflict("ARTIFACT_VERSION_CONFLICT", "Previous version already has a successor")
        self._charge("user", actor.subject_id, blob.size_bytes, self.settings.artifact_user_quota_bytes)
        self._charge("scope", scope["scope_id"] or resource_id, blob.size_bytes,
                     self.settings.artifact_scope_quota_bytes)
        recorded_at = datetime.now(timezone.utc)
        self.uow.session.execute(insert(artifact_versions).values(id=blob.version_id,
            resource_id=resource_id, previous_version_id=previous_version_id,
            version_number=version_number, sha256=blob.sha256, size_bytes=blob.size_bytes,
            media_type=blob.media_type, filename=filename, metadata=metadata,
            uploaded_by=actor.subject_id, recorded_at=recorded_at))
        self.uow.session.execute(insert(resource_scopes).values(resource_id=blob.version_id,
            scope_id=scope["scope_id"], owner_scope_id=scope["owner_scope_id"],
            related_scope_ids=scope["related_scope_ids"], security_class=scope["security_class"],
            resource_kind="artifact"))
        event = append_revision(self.uow, actor, blob.version_id, 0,
            {"version_id": str(blob.version_id), "resource_id": str(resource_id),
             "sha256": blob.sha256, "size_bytes": blob.size_bytes,
             "previous_version_id": str(previous_version_id) if previous_version_id else None},
            "artifact.uploaded")
        inherited_refs = self.uow.session.execute(select(revision_visibility_dependencies.c.resource_id).join(
            resource_heads, resource_heads.c.event_id == revision_visibility_dependencies.c.event_id).where(
            resource_heads.c.resource_id == resource_id)).scalars().all()
        for dependency in set(inherited_refs) | set(evidence_references or ()):
            self.uow.session.execute(pg_insert(revision_visibility_dependencies).values(
                event_id=event.event_id, resource_id=dependency).on_conflict_do_nothing())
        if semantic_export:
            from lzo.semantic.exports import complete_publication
            complete_publication(self.uow, resource_id, blob)
        _attach_evidence(self.uow, actor, resource_id, blob, metadata, action=action)
        if evidence_references is not None:
            head_event = self.uow.session.execute(select(resource_heads.c.event_id).where(
                resource_heads.c.resource_id == resource_id)).scalar_one()
            for dependency in set(evidence_references):
                self.uow.session.execute(pg_insert(revision_visibility_dependencies).values(
                    event_id=head_event, resource_id=dependency).on_conflict_do_nothing())
            self.uow.session.execute(update(resource_scopes).where(resource_scopes.c.resource_id == resource_id)
                .values(resource_kind="evidence"))
        if staged is not None:
            self.store.complete(staged)
        return self.get(actor, blob.version_id)

    def get(self, actor: ActorContext, version_id: UUID) -> ArtifactDetail:
        row = self._version(actor, version_id)
        # validate_actor has run in _version; compose the current-right filter
        # before exposing target IDs in artifact links.
        visible = authorized_statement(actor, "view")
        links = self.uow.session.execute(select(artifact_links).where(
            artifact_links.c.version_id == version_id,
            artifact_links.c.target_resource_id.in_(visible)).order_by(
            artifact_links.c.recorded_at, artifact_links.c.id)).mappings().all()
        reviews = self.uow.session.execute(select(artifact_reviews).where(
            artifact_reviews.c.version_id == version_id).order_by(
            artifact_reviews.c.recorded_at, artifact_reviews.c.id)).mappings().all()
        revision = self.uow.session.execute(select(resource_heads.c.revision).where(
            resource_heads.c.resource_id == version_id)).scalar_one()
        return ArtifactDetail(version_id=row["id"], resource_id=row["resource_id"],
            previous_version_id=row["previous_version_id"], version_number=row["version_number"],
            revision=revision, sha256=row["sha256"], size_bytes=row["size_bytes"],
            media_type=row["media_type"], filename=row["filename"], metadata=row["metadata"],
            uploaded_by=row["uploaded_by"], recorded_at=row["recorded_at"],
            links=[ArtifactLinkDetail(**link) for link in links],
            reviews=[ArtifactReviewDetail(**review) for review in reviews])

    def content(self, actor: ActorContext, version_id: UUID, byte_range=None):
        row = self._version(actor, version_id)
        ref = BlobRef(row["sha256"], row["size_bytes"], row["media_type"], row["id"])
        return self.store.open_range(ref, *byte_range) if byte_range else self.store.open(ref)

    def link(self, actor: ActorContext, version_id: UUID, target_resource_id: UUID,
             relation: str, expected_revision: int) -> ArtifactMutationDetail:
        self._version(actor, version_id, "edit")
        _permission(self.uow, actor, target_resource_id, "edit")
        if not relation or len(relation) > 128:
            raise ApiError("INVALID_RELATION", 422, "Invalid artifact relation")
        event = append_revision(self.uow, actor, version_id, expected_revision,
            {"target_resource_id": str(target_resource_id), "relation": relation}, "artifact.linked")
        link_id = uuid4()
        self.uow.session.execute(insert(artifact_links).values(id=link_id,
            version_id=version_id, target_resource_id=target_resource_id, relation=relation,
            actor_id=actor.subject_id, event_id=event.event_id, recorded_at=datetime.now(timezone.utc)))
        self.uow.session.execute(insert(revision_visibility_dependencies).values(
            event_id=event.event_id, resource_id=target_resource_id))
        return ArtifactMutationDetail(id=link_id, version_id=version_id,
            revision=event.revision, event_id=event.event_id)

    def review(self, actor: ActorContext, version_id: UUID, decision: str,
               comment: str, expected_revision: int) -> ArtifactMutationDetail:
        row = self._version(actor, version_id, "edit")
        if decision not in {"accepted", "rejected", "needs_changes"} or len(comment) > 2000:
            raise ApiError("INVALID_REVIEW", 422, "Invalid artifact review")
        if (_scope(self.uow, row["resource_id"])["resource_kind"] == "evidence" or
                row["metadata"].get("high_risk") is True) and row["uploaded_by"] == actor.subject_id:
            raise Forbidden("REVIEWER_SEPARATION", "High-risk evidence requires another reviewer")
        event = append_revision(self.uow, actor, version_id, expected_revision,
            {"decision": decision, "comment": comment}, "artifact.reviewed")
        review_id = uuid4()
        self.uow.session.execute(insert(artifact_reviews).values(id=review_id,
            version_id=version_id, decision=decision, comment=comment,
            actor_id=actor.subject_id, event_id=event.event_id, recorded_at=datetime.now(timezone.utc)))
        return ArtifactMutationDetail(id=review_id, version_id=version_id,
            revision=event.revision, event_id=event.event_id)
