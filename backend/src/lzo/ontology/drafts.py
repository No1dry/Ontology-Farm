"""Versioned drafts, offline import retention and current-right metadata reads."""
from dataclasses import asdict
import io
from uuid import UUID, uuid4
from sqlalchemy import exists, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from rdflib import URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

from lzo.artifacts.models import artifact_versions
from lzo.artifacts.service import ArtifactService
from lzo.artifacts.store import ArtifactStore
from lzo.auth.models import resource_scopes
from lzo.auth.permissions import authorized_statement, require_permission, validate_actor
from lzo.config import Settings
from lzo.contracts import BlobRef, Page, ResourceScope
from lzo.errors import ApiError, Conflict
from lzo.governance.history import revision_visibility_dependencies
from lzo.governance.models import resource_heads, revisions
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import digest_change
from lzo.ontology.models import drafts, releases, installations, catalog, dependencies
from lzo.ontology.schemas import ClassDef, PropertyDef, RelationDef, SchemaDocument, SchemaDraftView, CurrentSchemaBaseline
from lzo.semantic.files import nquads, parse_semantic_document, semantic_file_context, skolemize
from lzo.semantic.semantic_profile import SemanticProfile, SemanticProfileError, union, validate_document


def settings_for(uow):
    return getattr(uow, "ontology_settings", None) or Settings()


def store_for(uow):
    settings = settings_for(uow)
    return ArtifactStore(settings.file_root / "artifacts", max_upload_bytes=settings.artifact_max_upload_bytes)


def visible_statement(actor, table):
    hidden = exists(select(dependencies.c.dependency_id).where(
        dependencies.c.resource_id == table.c.id,
        dependencies.c.dependency_id.not_in(authorized_statement(actor, "view"))))
    return select(table).where(table.c.id.in_(authorized_statement(actor, "view")), ~hidden)


def authorize(uow, actor, resource_id, action="view"):
    validate_actor(uow, actor)
    visible = uow.session.execute(select(resource_scopes).where(
        resource_scopes.c.resource_id == resource_id,
        resource_scopes.c.resource_id.in_(authorized_statement(actor, "view")))).mappings().first()
    hidden = uow.session.execute(select(dependencies.c.dependency_id).where(
        dependencies.c.resource_id == resource_id,
        dependencies.c.dependency_id.not_in(authorized_statement(actor, "view"))).limit(1)).first()
    if visible is None or hidden:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    if action != "view":
        require_permission(actor, action, ResourceScope(resource_id, None, (), visible["security_class"]), uow=uow)


def global_permission(uow, actor, action):
    require_permission(actor, action, ResourceScope(None, None, (), "schema"), uow=uow)


def add_resource(uow, resource_id, kind):
    uow.session.execute(insert(resource_scopes).values(resource_id=resource_id,
        resource_kind=kind, security_class="schema"))


def bind_dependencies(uow, resource_id, refs, event_id=None):
    references = set(refs) - {resource_id}
    for ref in references:
        uow.session.execute(pg_insert(dependencies).values(resource_id=resource_id,
            dependency_id=ref).on_conflict_do_nothing())
    if event_id:
        bind_history_dependencies(uow, event_id, references)


def bind_history_dependencies(uow, event_id, refs):
    """Protect an immutable payload without changing its public resource view."""
    for ref in set(refs):
        uow.session.execute(pg_insert(revision_visibility_dependencies).values(
            event_id=event_id, resource_id=ref).on_conflict_do_nothing())


def protected_closure(uow, refs):
    """Flatten at command creation; reads use finite SQL ALL-reference checks."""
    return _protected_closure(uow, refs, exclude_catalog=False)


def definition_source_closure(uow, refs):
    """Trusted definition producers exclude catalog lifecycle from provenance."""
    return _protected_closure(uow, refs, exclude_catalog=True)


def _protected_closure(uow, refs, *, exclude_catalog):
    result, frontier = set(), set(refs)
    while frontier:
        if exclude_catalog:
            frontier.difference_update(uow.session.execute(select(resource_scopes.c.resource_id).where(
                resource_scopes.c.resource_id.in_(frontier),
                resource_scopes.c.resource_kind == "schema.catalog")).scalars())
        result.update(frontier)
        found = set(uow.session.execute(select(dependencies.c.dependency_id).where(
            dependencies.c.resource_id.in_(frontier))).scalars())
        found.update(uow.session.execute(select(revision_visibility_dependencies.c.resource_id).join(
            resource_heads, resource_heads.c.event_id == revision_visibility_dependencies.c.event_id).join(
            resource_scopes, resource_scopes.c.resource_id == resource_heads.c.resource_id).where(
            resource_heads.c.resource_id.in_(frontier),
            # Immutable Schema snapshot dependencies are authoritative source
            # provenance. Their private governance events may additionally
            # name incidental impacted objects, which are not source content.
            resource_scopes.c.resource_kind.not_in(("schema.release", "schema.bundle")))).scalars())
        frontier = found - result
        result.update(found)
    return result


def bind_all_events(uow, resource_id, refs):
    for event_id in uow.session.execute(select(revisions.c.event_id).where(revisions.c.resource_id == resource_id)).scalars():
        bind_dependencies(uow, resource_id, refs, event_id)


def head(uow, resource_id):
    row = uow.session.execute(select(resource_heads).where(
        resource_heads.c.resource_id == resource_id)).mappings().first()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    return row


def ensure_catalog(uow, actor):
    lock_ledger(uow)
    row = uow.session.execute(select(catalog)).mappings().first()
    if row is None:
        resource_id = uuid4()
        add_resource(uow, resource_id, "schema.catalog")
        append_revision(uow, actor, resource_id, 0, {"active_release_id": None}, "schema.catalog.created")
        uow.session.execute(insert(catalog).values(singleton=1, resource_id=resource_id, revision=1))
        row = uow.session.execute(select(catalog)).mappings().one()
    return row


def artifact_reference(uow, actor, blob):
    service = ArtifactService(uow, store_for(uow), settings_for(uow))
    row = service._version(actor, blob.version_id)
    if (row["sha256"], row["size_bytes"], row["media_type"]) != (blob.sha256, blob.size_bytes, blob.media_type):
        raise Conflict("BLOB_REF_CONFLICT", "Artifact reference changed")
    with service.store.open(blob):
        pass
    return row["resource_id"]


def document_refs(uow, actor, document, base_release_id):
    refs = []
    if base_release_id:
        authorize(uow, actor, base_release_id)
        row = uow.session.execute(select(installations.c.state).where(
            installations.c.release_id == base_release_id)).scalar_one_or_none()
        if row not in ("published", "deprecated"):
            raise ApiError("RELEASE_NOT_PUBLISHED", 422, "Draft base must be a published release")
        refs.append(base_release_id)
    for blob in document.dependencies:
        refs.extend((blob.version_id, artifact_reference(uow, actor, blob)))
    return refs


def get_draft(uow, actor, draft_id, action="view"):
    authorize(uow, actor, draft_id, action)
    row = uow.session.execute(select(drafts).where(drafts.c.id == draft_id)).mappings().first()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    result = dict(row) | {"revision": head(uow, draft_id)["revision"]}
    if result["state"] == "submitted":
        from lzo.governance.approvals import ApprovalService, approvals
        current = uow.session.execute(ApprovalService(uow)._statement(actor).where(
            approvals.c.descriptor["command"]["draft_id"].astext == str(draft_id)).order_by(
            approvals.c.position.desc()).limit(1)).mappings().first()
        if current and current["descriptor"]["command"]["revision"] == result["revision"]:
            if current["decision"] == "approve" or (current["risk"] == "low" and current["decision"] != "reject"):
                result["state"] = "approved"
    return result


def current_baseline(uow, actor):
    """Read current catalog evidence with existing view/ALL checks, never editor rights."""
    row = uow.session.execute(select(catalog)).mappings().first()
    if row is None:
        return None
    try:
        authorize(uow, actor, row["resource_id"])
        if row["active_release_id"] is not None:
            authorize(uow, actor, row["active_release_id"])
            state = uow.session.execute(select(installations.c.state).where(
                installations.c.release_id == row["active_release_id"])).scalar_one_or_none()
            if state != "published":
                return None
    except ApiError as error:
        if error.status != 404:
            raise
        return None
    return CurrentSchemaBaseline(active_release_id=row["active_release_id"])


def draft_view(row, *, uow=None, actor=None):
    # Hash the exact stored JSON, before DTO coercion or browser serialization.
    return SchemaDraftView(**{k: row[k] for k in ("id", "revision", "base_release_id", "document", "state")},
        document_digest=digest_change(row["document"]),
        current_baseline=current_baseline(uow, actor) if uow is not None and actor is not None else None)


def create_draft(uow, actor, request, *, imports=None):
    global_permission(uow, actor, "schema.edit")
    refs = definition_source_closure(uow, document_refs(uow, actor, request.document, request.base_release_id))
    if imports is None:
        imports = uow.session.execute(select(releases.c.imports).where(
            releases.c.id == request.base_release_id)).scalar_one() if request.base_release_id else []
    validate_document(request.document)
    ensure_catalog(uow, actor)
    identifier = uuid4()
    add_resource(uow, identifier, "schema.draft")
    payload = {"document": request.document.model_dump(mode="json"),
               "base_release_id": str(request.base_release_id) if request.base_release_id else None,
               "imports": list(imports)}
    revision = append_revision(uow, actor, identifier, 0, payload, "schema.draft.created")
    uow.session.execute(insert(drafts).values(id=identifier, revision=revision.revision,
        base_release_id=request.base_release_id, document=payload["document"], imports=list(imports), state="draft"))
    bind_dependencies(uow, identifier, refs, revision.event_id)
    return draft_view(get_draft(uow, actor, identifier), uow=uow, actor=actor)


def update_draft(uow, actor, draft_id, document, expected_revision, *, imports=None):
    row = get_draft(uow, actor, draft_id, "schema.edit")
    refs = definition_source_closure(uow, document_refs(uow, actor, document, row["base_release_id"]))
    validate_document(document)
    lock_ledger(uow)
    row = get_draft(uow, actor, draft_id, "schema.edit")
    if row["revision"] != expected_revision:
        raise Conflict("REVISION_CONFLICT", "Draft revision changed")
    if row["state"] == "installing":
        # A failed/abandoned worker must be explicitly cancelled before editing.
        from lzo.worker.models import jobs
        job_states = uow.session.execute(select(jobs.c.status).join(installations,
            installations.c.job_id == jobs.c.id).join(releases,
            releases.c.id == installations.c.release_id).where(
                releases.c.draft_id == draft_id, releases.c.draft_revision == expected_revision)).scalars().all()
        if not job_states or any(state not in ("failed", "cancelled", "paused") for state in job_states):
            raise Conflict("SCHEMA_INSTALLING", "Draft has an active installation")
    retained = row["imports"] if imports is None else list(imports)
    payload = {"document": document.model_dump(mode="json"), "imports": retained,
               "base_release_id": str(row["base_release_id"]) if row["base_release_id"] else None}
    revision = append_revision(uow, actor, draft_id, expected_revision, payload, "schema.draft.updated")
    uow.session.execute(update(drafts).where(drafts.c.id == draft_id).values(
        revision=revision.revision, document=payload["document"], imports=retained, state="draft"))
    bind_dependencies(uow, draft_id, refs, revision.event_id)
    return draft_view(get_draft(uow, actor, draft_id), uow=uow, actor=actor)


def list_drafts(uow, actor, cursor=None):
    validate_actor(uow, actor)
    statement = visible_statement(actor, drafts)
    if cursor:
        try:
            statement = statement.where(drafts.c.id > UUID(cursor))
        except ValueError:
            raise ApiError("INVALID_CURSOR", 422, "Invalid list cursor") from None
    size = min(settings_for(uow).list_page_size, settings_for(uow).list_max_page_size)
    rows = uow.session.execute(statement.order_by(drafts.c.id).limit(size + 1)).mappings().all()
    return Page([draft_view(get_draft(uow, actor, row["id"]), uow=uow, actor=actor) for row in rows[:size]],
                str(rows[size-1]["id"]) if len(rows) > size else None)


def imported_definitions(dataset):
    graph = union(dataset)
    classes, properties, relations = [], [], []
    def values(subject, predicate):
        return tuple(sorted(str(v) for v in graph.objects(subject, predicate)))
    def label(subject):
        labels = sorted(graph.objects(subject, RDFS.label), key=lambda x: (x.language or "", str(x)))
        return str(labels[0] if labels else subject)
    for subject in sorted(set(graph.subjects(RDF.type, OWL.Class)) | set(graph.subjects(RDF.type, RDFS.Class))):
        classes.append(ClassDef(iri=str(subject), label=label(subject),
            description=str(graph.value(subject, RDFS.comment) or ""),
            parents=values(subject, RDFS.subClassOf),
            deprecated=str(graph.value(subject, OWL.deprecated) or "false") in ("true", "1")))
    for subject in sorted(set(graph.subjects(RDF.type, OWL.DatatypeProperty))):
        ranges = values(subject, RDFS.range)
        if len(ranges) != 1:
            raise ApiError("DATATYPE_RANGE_REQUIRED", 422, "Datatype properties require one datatype range")
        properties.append(PropertyDef(iri=str(subject), label=label(subject), datatype_iri=ranges[0],
            owner_policy_iri="urn:lzo:policy:platform-manual", superproperties=values(subject, RDFS.subPropertyOf)))
    relation_nodes = set(graph.subjects(RDF.type, OWL.ObjectProperty)) | set(
        graph.subjects(RDF.type, OWL.SymmetricProperty)) | set(graph.subjects(RDF.type, OWL.TransitiveProperty))
    for subject in sorted(relation_nodes):
        inverse = values(subject, OWL.inverseOf)
        if len(inverse) > 1:
            raise ApiError("RELATION_INVERSE_UNSUPPORTED", 422, "Multiple inverse definitions exceed DTO profile")
        relations.append(RelationDef(iri=str(subject), label=label(subject), domain_iris=values(subject, RDFS.domain),
            range_iris=values(subject, RDFS.range), superproperties=values(subject, RDFS.subPropertyOf),
            inverse_iri=inverse[0] if inverse else None,
            symmetric=(subject, RDF.type, OWL.SymmetricProperty) in graph,
            transitive=(subject, RDF.type, OWL.TransitiveProperty) in graph))
    return tuple(classes), tuple(properties), tuple(relations)


def import_semantic_draft(uow, actor, request):
    from lzo.ontology.schemas import CreateDraftRequest
    if request.target_draft_id:
        row = get_draft(uow, actor, request.target_draft_id, "schema.edit")
        if request.base_release_id != row["base_release_id"]:
            raise Conflict("DRAFT_BASE_CONFLICT", "Import base differs from draft")
        document = SchemaDocument.model_validate(row["document"])
        retained = row["imports"]
    else:
        global_permission(uow, actor, "schema.edit")
        if request.expected_revision:
            raise Conflict("REVISION_CONFLICT", "New draft starts at revision zero")
        document, retained = SchemaDocument(), []
    owner = artifact_reference(uow, actor, request.blob)
    with semantic_file_context(store_for(uow)):
        try:
            parsed = parse_semantic_document(request.blob, request.format)
        except ValueError as error:
            raise ApiError("SEMANTIC_PARSE_FAILED", 422, "Unsupported or invalid semantic input") from error
    support = SemanticProfile().inspect_dataset(parsed.dataset, request.kind)
    if not support.allowed:
        raise SemanticProfileError("SEMANTIC_PROFILE_UNSUPPORTED", 422, "Unsupported semantic document",
                       {"support": asdict(support), "artifact": str(request.blob.version_id)})
    canonical = skolemize(parsed)
    record = {"blob": asdict(request.blob) | {"version_id": str(request.blob.version_id)},
              "kind": request.kind, "format": request.format, "support": asdict(support),
              "nquads": nquads(canonical.dataset), "digest": canonical.canonical_digest}
    if request.kind == "schema":
        classes, properties, relations = imported_definitions(parsed.dataset)
        document = document.model_copy(update={"classes": (*document.classes, *classes),
            "properties": (*document.properties, *properties), "relations": (*document.relations, *relations)})
    else:
        document = document.model_copy(update={"shapes": document.shapes + "\n" + union(parsed.dataset).serialize(format="turtle")})
    document = document.model_copy(update={"dependencies": (*document.dependencies, request.blob)})
    retained = [*retained, record]
    if request.target_draft_id:
        result = update_draft(uow, actor, request.target_draft_id, document, request.expected_revision, imports=retained)
    else:
        result = create_draft(uow, actor, CreateDraftRequest(
            base_release_id=request.base_release_id, document=document), imports=retained)
    bind_dependencies(uow, result.id, (owner, request.blob.version_id), head(uow, result.id)["event_id"])
    return result
