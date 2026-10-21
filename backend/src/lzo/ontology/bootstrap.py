"""Internal, approved package installation through ordinary S02 publication.

Preparation consumes a registered index BlobRef and an exact logical-path map
of registered content BlobRefs. The index explicitly declares roles/formats.
Its bytes are an approval dependency; its own hash is excluded from logical
identity. A trusted immutable ledger association reserves namespace/version.
The draft metadata is only the approval-covered mirror of that association.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from pydantic import BaseModel, ConfigDict

from lzo.contracts import BlobRef, JobRef
from lzo.errors import ApiError, Conflict
from lzo.governance.models import revisions
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import digest_change
from lzo.ontology import drafts as service
from lzo.ontology.models import releases, installations
from lzo.ontology.schemas import CreateDraftRequest, SchemaDocument, SchemaDraftView
from lzo.semantic.projection import register_nonsemantic


class BootstrapManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    namespace: str
    package_artifacts: tuple[BlobRef, ...]
    inference_profile: str
    data_nature: Literal["synthetic"]
    digest: str


@dataclass(frozen=True)
class BootstrapPreparation:
    index: BlobRef
    artifacts: dict[str, BlobRef]


@dataclass(frozen=True)
class PreparedBootstrap:
    manifest: BootstrapManifest
    draft: SchemaDraftView
    association_id: UUID
    association_revision: int


def _invalid(message):
    raise ApiError("BOOTSTRAP_INVALID", 422, message)


def _bytes(uow, actor, blob):
    service.artifact_reference(uow, actor, blob)
    with service.store_for(uow).open(blob) as stream:
        data = stream.read()
    if hashlib.sha256(data).hexdigest() != blob.sha256 or len(data) != blob.size_bytes:
        _invalid("Package bytes do not match their registered reference")
    return data


def _inventory(uow, actor, preparation):
    try:
        index = json.loads(_bytes(uow, actor, preparation.index))
    except (ValueError, UnicodeError):
        _invalid("Invalid package index JSON")
    if not isinstance(index, dict) or set(index) != {"version", "namespace", "data_nature", "inference_profile", "artifacts"}:
        _invalid("Invalid package index fields")
    if (not isinstance(index["version"], str) or not index["version"] or
            not isinstance(index["namespace"], str) or not index["namespace"].startswith("https://") or
            index["data_nature"] != "synthetic" or
            index["inference_profile"] != "finite_explicit_axioms_v1" or not isinstance(index["artifacts"], list)):
        _invalid("Invalid package identity")
    paths, versions, roles = set(), {preparation.index.version_id}, []
    for item in index["artifacts"]:
        if not isinstance(item, dict) or set(item) != {"path", "role", "format", "sha256", "size_bytes", "media_type"}:
            _invalid("Invalid package inventory item")
        path = item["path"]
        if (not isinstance(path, str) or not path or "\\" in path or ":" in path or
                PurePosixPath(path).is_absolute() or any(p in ("", ".", "..") for p in path.split("/")) or
                path in paths or path not in preparation.artifacts):
            _invalid("Unsafe, duplicate or missing logical path")
        paths.add(path)
        role = item["role"]
        if (role, item["format"], item["media_type"]) not in (
                ("schema", "turtle", "text/turtle"), ("shapes", "turtle", "text/turtle"),
                ("profile", "json", "application/json")):
            _invalid("Unsupported explicit package role or format")
        roles.append(role)
        blob = preparation.artifacts[path]
        if blob.version_id in versions:
            _invalid("Duplicate artifact version")
        versions.add(blob.version_id)
        if (blob.sha256, blob.size_bytes, blob.media_type) != (item["sha256"], item["size_bytes"], item["media_type"]):
            _invalid("Inventory does not match registered artifact")
        data = _bytes(uow, actor, blob)
        if role == "profile":
            from lzo.semantic.inference import SUPPORTED_PROFILE
            try:
                profile = json.loads(data)
            except ValueError:
                _invalid("Invalid profile JSON")
            if profile != SUPPORTED_PROFILE:
                _invalid("Profile artifact is not the supported finite profile")
    if paths != set(preparation.artifacts) or not {"schema", "shapes", "profile"}.issubset(roles) or roles.count("profile") != 1:
        _invalid("Missing or extra package contents")
    identity = index | {"artifacts": sorted(index["artifacts"], key=lambda i: i["path"])}
    manifest = BootstrapManifest(**{key: index[key] for key in (
        "version", "namespace", "data_nature", "inference_profile")}, digest=digest_change(identity),
        package_artifacts=(preparation.index, *(preparation.artifacts[p] for p in sorted(paths))))
    return identity, manifest


def _association(uow, actor, manifest):
    rows = uow.session.execute(select(revisions).where(revisions.c.event_type == "bootstrap.prepared",
        revisions.c.payload["manifest"]["namespace"].astext == manifest.namespace,
        revisions.c.payload["manifest"]["version"].astext == manifest.version)).mappings().all()
    if len(rows) > 1:
        raise Conflict("BOOTSTRAP_ASSOCIATION_CONFLICT", "Ambiguous package association")
    if not rows:
        return None
    row = rows[0]
    service.authorize(uow, actor, row["resource_id"])
    if row["payload"]["manifest"]["digest"] != manifest.digest:
        raise Conflict("BOOTSTRAP_VERSION_CONFLICT", "Namespace/version already reserves different content")
    latest = uow.session.execute(select(revisions).where(revisions.c.resource_id == row["resource_id"],
        revisions.c.event_type.in_(("bootstrap.prepared", "bootstrap.reprepared"))).order_by(
        revisions.c.revision.desc()).limit(1)).mappings().one()
    if (latest["payload"]["manifest"] != row["payload"]["manifest"] or
            service.head(uow, row["resource_id"])["event_id"] != latest["event_id"]):
        raise Conflict("BOOTSTRAP_ASSOCIATION_CONFLICT", "Package association history is inconsistent")
    return latest


def _preparation(payload):
    return BootstrapPreparation(BlobRef(**(payload["index"] | {"version_id": UUID(payload["index"]["version_id"])})),
        {path: BlobRef(**(value | {"version_id": UUID(value["version_id"])})) for path, value in payload["artifacts"].items()})


def _verify(uow, actor, payload):
    identity, manifest = _inventory(uow, actor, _preparation(payload))
    if manifest.model_dump(mode="json") != payload["manifest"]:
        _invalid("Stored manifest bindings differ from actual inventory")
    row = service.get_draft(uow, actor, UUID(payload["draft"]["resource_id"]), "schema.edit")
    if digest_change(row["document"]) != payload["document_digest"] or digest_change(row["imports"]) != payload["imports_digest"]:
        raise Conflict("BOOTSTRAP_DRAFT_CHANGED", "Prepared draft changed; prepare and review the original content again")
    if row["revision"] != payload["draft"]["revision"]:
        # Only S02's single trusted publication transition may advance the
        # prepared draft. Even an identical ordinary edit invalidates its pin.
        latest = uow.session.execute(select(revisions).where(
            revisions.c.event_id == service.head(uow, row["id"])["event_id"])).mappings().one()
        published = uow.session.execute(select(releases.c.id).join(installations,
            installations.c.release_id == releases.c.id).where(releases.c.draft_id == row["id"],
            releases.c.draft_revision == payload["draft"]["revision"],
            installations.c.state.in_(("published", "deprecated")))).scalars().all()
        if (row["revision"] != payload["draft"]["revision"] + 1 or latest["event_type"] != "schema.draft.published"
                or latest["payload"].get("release_id") not in {str(v) for v in published}):
            raise Conflict("BOOTSTRAP_DRAFT_CHANGED", "Prepared draft revision changed")
    return manifest, row


def prepare_bootstrap(uow, actor, preparation: BootstrapPreparation) -> PreparedBootstrap:
    service.global_permission(uow, actor, "schema.edit")
    identity, manifest = _inventory(uow, actor, preparation)
    lock_ledger(uow)
    previous = _association(uow, actor, manifest)
    if previous:
        original, row = _verify(uow, actor, previous["payload"])
        return PreparedBootstrap(original, service.draft_view(row), previous["resource_id"], previous["revision"])
    return _prepare_record(uow, actor, preparation, identity, manifest)


def _prepare_record(uow, actor, preparation, identity, manifest, *, association_id=None, expected_revision=0):
    # Dependencies are registered before this call; parse all schema files as a
    # single effective definition set so file ordering never grants semantics.
    from lzo.semantic.files import parse_semantic_document, semantic_file_context, skolemize, nquads
    from lzo.semantic.semantic_profile import SemanticProfile, union
    imports, classes, properties, relations, shapes = [], [], [], [], []
    with semantic_file_context(service.store_for(uow)):
        for item in identity["artifacts"]:
            if item["role"] == "profile":
                continue
            blob = preparation.artifacts[item["path"]]
            parsed = parse_semantic_document(blob, item["format"])
            support = SemanticProfile().inspect_dataset(parsed.dataset, item["role"])
            if not support.allowed:
                _invalid("Package exceeds the supported semantic profile")
            canonical = skolemize(parsed)
            imports.append({"blob": asdict(blob) | {"version_id": str(blob.version_id)}, "kind": item["role"],
                "format": item["format"], "support": asdict(support), "nquads": nquads(canonical.dataset),
                "digest": canonical.canonical_digest})
            if item["role"] == "schema":
                cs, ps, rs = service.imported_definitions(parsed.dataset)
                classes.extend(cs); properties.extend(ps); relations.extend(rs)
            else:
                shapes.append(union(parsed.dataset).serialize(format="turtle"))
    document = SchemaDocument(classes=classes, properties=properties, relations=relations,
        shapes="\n".join(shapes), dependencies=manifest.package_artifacts,
        ui_metadata={"lzo_bootstrap": {"identity": identity, "digest": manifest.digest}})
    draft = service.create_draft(uow, actor, CreateDraftRequest(document=document), imports=imports)
    identifier = association_id or uuid4()
    if association_id is None:
        service.add_resource(uow, identifier, "schema.bootstrap")
    ref = service.head(uow, draft.id)
    payload = {"manifest": manifest.model_dump(mode="json"),
        "index": asdict(preparation.index) | {"version_id": str(preparation.index.version_id)},
        "artifacts": {p: asdict(b) | {"version_id": str(b.version_id)} for p, b in preparation.artifacts.items()},
        "draft": {"resource_id": str(draft.id), "revision": ref["revision"], "event_id": str(ref["event_id"])},
        "document_digest": digest_change(document.model_dump(mode="json")), "imports_digest": digest_change(imports)}
    event = append_revision(uow, actor, identifier, expected_revision, payload,
        "bootstrap.reprepared" if association_id else "bootstrap.prepared")
    refs = service.definition_source_closure(uow, [draft.id, *service.document_refs(uow, actor, document, None)])
    service.bind_dependencies(uow, identifier, refs, event.event_id)
    return PreparedBootstrap(manifest, draft, identifier, event.revision)


def _existing_installation(uow, actor, association):
    """Reserve one actual job across every trusted preparation, not approvals.

    Superseded normal drafts remain independently reviewable by S02. Their
    installations still occupy this reservation, including newer revisions.
    Reading J neither makes its original approval executable nor retries J.
    """
    history = uow.session.execute(select(revisions.c.payload).where(
        revisions.c.resource_id == association["resource_id"],
        revisions.c.event_type.in_(("bootstrap.prepared", "bootstrap.reprepared")))).scalars().all()
    prepared = {UUID(value["draft"]["resource_id"]): value for value in history}
    installed = uow.session.execute(select(releases.c.draft_id, releases.c.document,
        releases.c.imports, installations.c.job_id).join(installations,
        installations.c.release_id == releases.c.id).where(releases.c.draft_id.in_(prepared),
        installations.c.job_id.is_not(None)).order_by(releases.c.id)).mappings().all()
    from lzo.worker.service import JobService
    # Check ALL actual job dependencies before even reporting ambiguous history.
    jobs = [JobService(uow, service.settings_for(uow)).get(actor, row["job_id"]).job for row in installed]
    if len(jobs) > 1:
        raise Conflict("BOOTSTRAP_INSTALLATION_CONFLICT", "Package reservation has multiple installations")
    if not jobs:
        return None
    row = installed[0]
    original = prepared[row["draft_id"]]
    if (digest_change(row["document"]) != original["document_digest"] or
            digest_change(row["imports"]) != original["imports_digest"]):
        raise Conflict("BOOTSTRAP_INSTALLATION_CHANGED", "Historical installation differs from prepared content")
    return jobs[0]


def reprepare_bootstrap(uow, actor, manifest: BootstrapManifest,
                        expected_association_revision: int) -> PreparedBootstrap:
    """Explicit pre-install recovery; reuse original bytes, require new review.

    Never replace or retry an existing real installation. All old association
    revisions remain historical authority, and the first reserves content.
    """
    service.global_permission(uow, actor, "schema.edit")
    lock_ledger(uow)
    previous = _association(uow, actor, manifest)
    if previous is None:
        raise Conflict("BOOTSTRAP_NOT_PREPARED", "Prepare package inputs before recovery")
    if previous["revision"] != expected_association_revision:
        raise Conflict("REVISION_CONFLICT", "Package association revision changed")
    if _existing_installation(uow, actor, previous):
        raise Conflict("BOOTSTRAP_INSTALLATION_EXISTS", "Recover the existing installation job explicitly")
    preparation = _preparation(previous["payload"])
    identity, original = _inventory(uow, actor, preparation)
    if original != manifest or original.model_dump(mode="json") != previous["payload"]["manifest"]:
        raise Conflict("BOOTSTRAP_BINDINGS_CHANGED", "Recovery must retain original registered input versions")
    # An outstanding approval for the superseded draft must become stale even
    # when recovery was requested before any ordinary edit. Use S02's revision
    # command; never mutate its workflow row or approval state directly.
    old = service.get_draft(uow, actor, UUID(previous["payload"]["draft"]["resource_id"]), "schema.edit")
    service.update_draft(uow, actor, old["id"], SchemaDocument.model_validate(old["document"]), old["revision"])
    return _prepare_record(uow, actor, preparation, identity, original,
        association_id=previous["resource_id"], expected_revision=expected_association_revision)


def bootstrap_packages(uow, actor, manifest: BootstrapManifest, approval_id: UUID) -> JobRef:
    service.global_permission(uow, actor, "schema.publish")
    lock_ledger(uow)
    association = _association(uow, actor, manifest)
    if association is None:
        raise Conflict("BOOTSTRAP_NOT_PREPARED", "Prepare package inputs before approval")
    original, row = _verify(uow, actor, association["payload"])
    if original != manifest:
        raise Conflict("BOOTSTRAP_BINDINGS_CHANGED", "Use the original prepared artifact versions")
    previous = _existing_installation(uow, actor, association)
    if previous:
        return previous
    if row["revision"] != association["payload"]["draft"]["revision"]:
        raise Conflict("BOOTSTRAP_DRAFT_CHANGED", "Prepared draft revision changed")
    from lzo.ontology.releases import publish_release
    return publish_release(uow, actor, row["id"], approval_id, row["revision"])


register_nonsemantic("bootstrap.prepared", "bootstrap-v1")
register_nonsemantic("bootstrap.reprepared", "bootstrap-v1")
