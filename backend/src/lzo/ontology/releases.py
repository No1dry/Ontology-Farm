"""Approval-bound immutable artifacts, verified projection and atomic activation."""
from dataclasses import asdict
import hashlib
import io
import json
from uuid import UUID, uuid4, uuid5

from sqlalchemy import insert, select, update
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

from lzo.artifacts.service import ArtifactService
from lzo.artifacts.models import artifact_versions
from lzo.auth.models import resource_scopes
from lzo.auth.permissions import _current_uow, validate_actor
from lzo.contracts import BlobRef, ExecutionBundle, JobRef, Page, RevisionRef
from lzo.errors import ApiError, Conflict
from lzo.governance.approvals import ApprovalService
from lzo.governance.models import resource_heads, revisions
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import ChangeDescriptor, classify, digest_change, register_policy, policies
from lzo.ontology.drafts import (add_resource, artifact_reference, authorize, bind_dependencies,
    bind_all_events, bind_history_dependencies, protected_closure, definition_source_closure,
    document_refs, get_draft, global_permission, head,
    settings_for, store_for, visible_statement)
from lzo.ontology.models import drafts, releases, installations, catalog, bundles, genesis, dependencies, axioms
from lzo.ontology.schemas import SchemaDocument, SchemaReleaseView, ImpactReport, SchemaDiffView, FormSchemaView
from lzo.semantic.files import dataset_from_nquads, nquads, ParsedSemanticDocument, skolemize
from lzo.semantic.fuseki import FusekiRepository
from lzo.semantic.models import projection_events
from lzo.semantic.projection import (ProjectionContent, ProjectionService, register_projection,
    register_nonsemantic, producers)
from lzo.semantic.rdf import Triple, term_from_node, triple_key, logical_graph, ordered
from lzo.semantic.semantic_profile import parse, union, validate_document, effective_shapes, form_schema
from lzo.semantic.semantica_adapter import SemanticaAdapter


def _json(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _command(row):
    return {"operation": "ontology.release", "draft_id": str(row["id"]), "revision": row["revision"],
            "document_digest": digest_change(row["document"]), "imports_digest": digest_change(row["imports"])}


def _active_release(uow, active_id):
    """Risk follows the immutable snapshot that activation will replace."""
    if active_id is None:
        return None
    return uow.session.execute(select(releases).where(releases.c.id == active_id)).mappings().one()


def metadata_changes(old, new):
    """Only existing definitions' labels/descriptions qualify; no caller risk flag."""
    if old is None:
        return None
    old, new = json.loads(json.dumps(old)), json.loads(json.dumps(new))
    changed = set()
    for section in ("classes", "properties", "relations"):
        if len(old[section]) != len(new[section]):
            return None
        a = {v["iri"]: v for v in old[section]}
        b = {v["iri"]: v for v in new[section]}
        if a.keys() != b.keys():
            return None
        for key in a:
            for field in ("label", "description"):
                if a[key].get(field) != b[key].get(field):
                    changed.add(field)
                a[key].pop(field, None)
                b[key].pop(field, None)
        old[section], new[section] = a, b
    return tuple(sorted(changed)) if old == new and changed else None


def _inputs(uow):
    """Latest registered authoritative input, including unprojected writes."""
    latest = {}
    for event in uow.session.execute(select(projection_events).order_by(
            projection_events.c.position)).mappings():
        latest[event["resource_id"]] = event
    references, data, baseline = set(), [], {}
    for resource_id, event in latest.items():
        row = uow.session.execute(select(revisions).where(revisions.c.event_id == event["event_id"])).mappings().one()
        if row["event_type"] == "schema.install":
            continue
        producer = producers.get(row["event_type"])
        if producer is None:
            raise ApiError("IMPACT_COVERAGE_UNAVAILABLE", 409, "Complete impact coverage is unavailable")
        if producer.dirty_kind is None:
            continue  # Managed inference outputs are never fresh base inputs.
        content = producer.resolve(uow, row)
        references.update(content.references)
        data.extend(content.graphs.get("accepted", ()))
        baseline[str(resource_id)] = event["revision"]
        for ref in content.dependency_revisions:
            baseline[str(ref.resource_id)] = head(uow, ref.resource_id)["revision"]
    return references, tuple(data), baseline


def _descriptor(uow, targets, command, *, metadata=False):
    core_keys = {"operation", "draft_id", "revision", "document_digest", "imports_digest"}
    if set(command) not in (core_keys, core_keys | {"impact_digest"}) or command["operation"] != "ontology.release":
        raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Invalid ontology command")
    draft_id = UUID(command["draft_id"])
    row = uow.session.execute(select(drafts).where(drafts.c.id == draft_id)).mappings().first()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    row = dict(row) | {"revision": head(uow, draft_id)["revision"]}
    if _command(row) != {k: command[k] for k in core_keys}:
        raise Conflict("APPROVAL_STALE", "Draft changed; preview again")
    document = SchemaDocument.model_validate(row["document"])
    validate_document(document)
    active = uow.session.execute(select(catalog)).mappings().one()
    previous_release = _active_release(uow, active["active_release_id"])
    metadata_fields = metadata_changes(previous_release["document"] if previous_release else None, row["document"])
    if metadata and (not metadata_fields or (previous_release and previous_release["imports"] != row["imports"])):
        raise ApiError("METADATA_CHANGE_REQUIRED", 422, "Only existing labels and descriptions qualify")
    refs, data, baseline = _inputs(uow)
    refs.update(uow.session.execute(select(dependencies.c.dependency_id).where(
        dependencies.c.resource_id == draft_id)).scalars())
    refs.update((draft_id, active["resource_id"]))
    refs = protected_closure(uow, refs)
    for ref in refs:
        baseline[str(ref)] = head(uow, ref)["revision"]
    # Bundle IDs here are deterministic preview identities, never installed snapshots.
    # The exact shape bytes are bound just as they are for a persistent bundle.
    shapes = effective_shapes(document)
    shapes_digest = hashlib.sha256(shapes.encode()).hexdigest()
    preview_bundle = ExecutionBundle(uuid5(draft_id, shapes_digest), draft_id, None,
        draft_id, draft_id, draft_id, shapes_digest)
    data_graph = logical_graph(data)
    # Only explicit class hierarchy is added for SHACL class/target closure;
    # domain/range never invent confirmed instance types.
    for triple in schema_graph(document, row["imports"]).triples((None, RDFS.subClassOf, None)):
        data_graph.add(triple)
    validation = SemanticaAdapter().validate(data_graph.serialize(format="turtle"), shapes, preview_bundle)
    previous = previous_release["document"] if previous_release else None
    shape_change = previous is not None and previous["shapes"] != row["document"]["shapes"]
    migration = bool(validation.violations) or shape_change
    old_ids = {c["iri"] for c in previous["classes"]} if previous else set()
    new_ids = {c.iri for c in document.classes}
    impact = {"affected_ids": sorted(str(r) for r in refs), "violations": _json([asdict(v) for v in validation.violations]),
        "form_diff": {"added_classes": sorted(new_ids-old_ids), "removed_classes": sorted(old_ids-new_ids),
                      "document_changed": previous != row["document"]},
        "permission_diff": {"changed": bool(previous and previous["ui_metadata"] != row["document"]["ui_metadata"])},
        "migration_required": migration, "shapes_digest": shapes_digest,
        "active_release_id": str(active["active_release_id"]) if active["active_release_id"] else None}
    impact_digest = digest_change({"baseline": baseline, "impact": impact})
    if "impact_digest" in command and command["impact_digest"] != impact_digest:
        raise Conflict("APPROVAL_STALE", "Impact or current baseline changed")
    command = {**command, "impact_digest": impact_digest}
    kind = "metadata.update" if metadata else "schema.publish"
    return ChangeDescriptor(kind=kind, target_ids=(draft_id, *sorted(refs-{draft_id}, key=str)),
        command=command, baseline=baseline, impact=impact, submit_action="schema.edit",
        review_action="schema.review", execute_action="schema.publish",
        global_security_class="schema", operation_key=f"ontology.release:{draft_id}",
        semantic_change=not metadata, changed_fields=metadata_fields or (), policy_version=1)


def descriptor(uow, actor, draft_id):
    row = get_draft(uow, actor, draft_id, "schema.edit")
    active_id = uow.session.execute(select(catalog.c.active_release_id)).scalar_one()
    active = _active_release(uow, active_id)
    low = bool(active and metadata_changes(active["document"], row["document"]) and active["imports"] == row["imports"])
    return ApprovalService(uow).preview(actor, "metadata.update" if low else "schema.publish",
                                        [draft_id], _command(row))


def impact_view(change):
    risk, reasons = classify(change)
    return ImpactReport(digest=change.change_digest, baseline=change.baseline, risk=risk, reasons=reasons,
        active_release_id=change.impact["active_release_id"],
        **{key: change.impact[key] for key in (
            "affected_ids", "violations", "form_diff", "permission_diff", "migration_required")})


def preview_release(actor, draft_id):
    return impact_view(descriptor(_current_uow.get(), actor, draft_id))


def submit_draft(uow, actor, draft_id, impact_digest, expected_revision):
    row = get_draft(uow, actor, draft_id, "schema.edit")
    if row["revision"] != expected_revision:
        raise Conflict("REVISION_CONFLICT", "Draft revision changed")
    change = descriptor(uow, actor, draft_id)
    if impact_digest != change.change_digest:
        raise Conflict("APPROVAL_STALE", "Impact preview changed")
    require_migration(row, change)
    approval = ApprovalService(uow).submit(actor, change.kind, change.target_ids,
        change.change_digest, change.baseline, {"command": change.command})
    uow.session.execute(update(drafts).where(drafts.c.id == draft_id).values(state="submitted"))
    return approval


def require_migration(row, change):
    if change.impact["migration_required"]:
        plan = row["document"]["ui_metadata"].get("migration_plan")
        if not isinstance(plan, dict) or not all(isinstance(plan.get(k), str) and plan[k].strip()
                                               for k in ("description", "validation", "rollback")):
            raise ApiError("MIGRATION_PLAN_REQUIRED", 422, "Shape changes require a reviewed migration plan")
        if any(issue["severity"] == "Violation" for issue in change.impact["violations"]):
            raise ApiError("MIGRATION_NOT_APPLIED", 422, "Current authoritative data violates the proposed shapes")


def schema_graph(document, imports):
    graph = Graph()
    from lzo.ontology.drafts import imported_definitions
    originals = {}
    # All imported axioms survive. Editable DTO predicates replace only their
    # corresponding subject predicates; originals and graph indexes stay retained.
    for source in imports:
        if source["kind"] == "schema":
            dataset = dataset_from_nquads(source["nquads"])
            for group in imported_definitions(dataset):
                originals.update({value.iri: value for value in group})
            for triple in union(dataset):
                graph.add(triple)
    identifiers = {value.iri for value in (*document.classes, *document.properties, *document.relations)}
    for removed in originals.keys() - identifiers:
        graph.remove((URIRef(removed), None, None))
    def label(subject, value, field, predicate):
        original = originals.get(str(subject))
        if original is not None and getattr(original, field, None) == value:
            return
        graph.remove((subject, predicate, None))
        graph.add((subject, predicate, Literal(value)))
    for cls in document.classes:
        subject = URIRef(cls.iri)
        for predicate in (RDFS.subClassOf,):
            graph.remove((subject, predicate, None))
        graph.add((subject, RDF.type, OWL.Class))
        label(subject, cls.label, "label", RDFS.label)
        label(subject, cls.description, "description", RDFS.comment)
        for parent in cls.parents:
            graph.add((subject, RDFS.subClassOf, URIRef(parent)))
        if cls.iri not in originals or originals[cls.iri].deprecated != cls.deprecated:
            graph.remove((subject, OWL.deprecated, None))
            if cls.deprecated:
                graph.add((subject, OWL.deprecated, Literal("true", datatype=XSD.boolean, normalize=False)))
    for prop in (*document.properties, *document.relations):
        subject = URIRef(prop.iri)
        for predicate in (RDFS.subPropertyOf,):
            graph.remove((subject, predicate, None))
        label(subject, prop.label, "label", RDFS.label)
        for parent in prop.superproperties:
            graph.add((subject, RDFS.subPropertyOf, URIRef(parent)))
    for prop in document.properties:
        graph.add((URIRef(prop.iri), RDF.type, OWL.DatatypeProperty))
        graph.remove((URIRef(prop.iri), RDFS.range, None))
        graph.add((URIRef(prop.iri), RDFS.range, URIRef(prop.datatype_iri)))
    for rel in document.relations:
        subject = URIRef(rel.iri)
        graph.add((subject, RDF.type, OWL.ObjectProperty))
        for predicate in (RDFS.domain, RDFS.range, OWL.inverseOf):
            graph.remove((subject, predicate, None))
        for predicate, values in ((RDFS.domain, rel.domain_iris), (RDFS.range, rel.range_iris)):
            for value in values:
                graph.add((subject, predicate, URIRef(value)))
        for kind, enabled in ((OWL.SymmetricProperty, rel.symmetric), (OWL.TransitiveProperty, rel.transitive)):
            graph.remove((subject, RDF.type, kind))
            if enabled:
                graph.add((subject, RDF.type, kind))
        if rel.inverse_iri:
            graph.add((subject, OWL.inverseOf, URIRef(rel.inverse_iri)))
    return graph


def register_schema_artifact(uow, actor, release_id, blob, filename):
    """Fixed trusted generation entry point; never a generic upload escape hatch."""
    authorize(uow, actor, release_id, "schema.publish")
    row = uow.session.execute(select(revisions).where(revisions.c.resource_id == release_id,
        revisions.c.event_type == "schema.release.prepared")).mappings().first()
    kind = uow.session.execute(select(resource_scopes.c.resource_kind).where(
        resource_scopes.c.resource_id == release_id)).scalar_one()
    if kind != "schema.release" or row is None or row["actor_id"] != actor.subject_id or uow.session.execute(select(
            installations.c.release_id).where(installations.c.release_id == release_id)).first():
        raise ApiError("SCHEMA_ARTIFACT_TARGET_INVALID", 422, "Generated artifact target is not a new release")
    return ArtifactService(uow, store_for(uow), settings_for(uow))._register_upload(
        actor, release_id, blob, filename=filename, metadata={"generated": "ontology.release"},
        action="schema.publish")


def _genesis(uow, actor):
    defaults = {
        "authority": {"platform_owned": "authorized_manual", "external_values": "candidate",
                      "identity_claim": "explicit_review", "conflict_override": False},
        "ruling": {"external_acceptance": "deterministic_rule_or_independent_review",
                   "unresolved_conflicts": "block", "automatic_overwrite": False},
        "inference": {"profile": "finite_explicit_axioms", "enabled_by_default": False,
                      "families": ["subclass", "subproperty", "domain_range", "inverse", "symmetric", "transitive"]}}
    result = {}
    for kind, payload in defaults.items():
        identifier = uow.session.execute(select(genesis.c.version_id).where(genesis.c.kind == kind)).scalar_one_or_none()
        if identifier is None:
            identifier = uuid4()
            add_resource(uow, identifier, "schema.configuration")
            append_revision(uow, actor, identifier, 0, payload, "schema.genesis.created")
            uow.session.execute(insert(genesis).values(kind=kind, version_id=identifier))
        result[kind] = identifier
    selected = uow.session.execute(select(catalog.c.active_bundle_id)).scalar_one_or_none()
    if selected is not None:
        result["inference"] = uow.session.execute(select(bundles.c.inference_version_id).where(
            bundles.c.id == selected)).scalar_one()
    return result


def create_execution_bundle(uow, actor, schema_release_id, mapping_version_id,
                            authority_version_id, ruling_policy_version_id, inference_version_id, shapes_digest):
    authorize(uow, actor, schema_release_id, "schema.publish")
    if len(shapes_digest) != 64 or any(c not in "0123456789abcdef" for c in shapes_digest):
        raise ApiError("SHAPES_DIGEST_INVALID", 422, "Invalid shape digest")
    actual = uow.session.execute(select(artifact_versions.c.id).where(
        artifact_versions.c.resource_id == schema_release_id,
        artifact_versions.c.filename == "shapes.ttl", artifact_versions.c.media_type == "text/turtle",
        artifact_versions.c.sha256 == shapes_digest)).scalar_one_or_none()
    if actual is None:
        raise ApiError("SHAPES_DIGEST_MISMATCH", 422, "Bundle must bind the stored release Shape artifact")
    prepared = uow.session.execute(select(revisions.c.event_id).where(
        revisions.c.resource_id == schema_release_id,
        revisions.c.event_type == "schema.release.prepared")).first()
    if prepared is None:
        raise ApiError("SCHEMA_RELEASE_REQUIRED", 422, "Bundle requires an authoritative release")
    refs = [schema_release_id, authority_version_id, ruling_policy_version_id, inference_version_id]
    if mapping_version_id:
        refs.append(mapping_version_id)
    for ref in refs:
        authorize(uow, actor, ref)
        if head(uow, ref)["revision"] != 1 and ref != schema_release_id:
            raise ApiError("BUNDLE_VERSION_MUTABLE", 422, "Configuration identities must be immutable versions")
    protected = definition_source_closure(uow, refs)
    for ref in protected:
        authorize(uow, actor, ref)
    bundle = ExecutionBundle(uuid4(), schema_release_id, mapping_version_id, authority_version_id,
        ruling_policy_version_id, inference_version_id, shapes_digest)
    add_resource(uow, bundle.id, "schema.bundle")
    event = append_revision(uow, actor, bundle.id, 0, _json(asdict(bundle)), "schema.bundle.created")
    uow.session.execute(insert(bundles).values(**asdict(bundle)))
    bind_dependencies(uow, bundle.id, protected, event.event_id)
    return bundle


def get_execution_bundle(actor, bundle_id):
    uow = _current_uow.get()
    authorize(uow, actor, bundle_id)
    row = uow.session.execute(select(bundles).where(bundles.c.id == bundle_id)).mappings().first()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    return ExecutionBundle(**row)


def current_execution_bundle(uow, actor):
    """One ontology-owned selection for every current semantic consumer."""
    from lzo.auth.permissions import permission_context
    selected = uow.session.execute(select(catalog)).mappings().one_or_none()
    if selected is None or selected["active_bundle_id"] is None:
        raise Conflict("BUNDLE_NOT_ACTIVE", "No active execution bundle")
    with permission_context(uow):
        bundle = get_execution_bundle(actor, selected["active_bundle_id"])
    if bundle.schema_release_id != selected["active_release_id"]:
        raise Conflict("BUNDLE_NOT_ACTIVE", "Current execution selection is inconsistent")
    state = uow.session.execute(select(installations.c.state).where(
        installations.c.release_id == bundle.schema_release_id)).scalar_one()
    if state != "published":
        raise Conflict("BUNDLE_NOT_ACTIVE", "Current release is not published")
    return bundle


def publish_release(uow, actor, draft_id, approval_id, expected_revision):
    row = get_draft(uow, actor, draft_id, "schema.publish")
    lock_ledger(uow)
    row = get_draft(uow, actor, draft_id, "schema.publish")
    if row["revision"] != expected_revision:
        raise Conflict("REVISION_CONFLICT", "Draft revision changed")
    previous = uow.session.execute(select(releases.c.id, installations.c.job_id).join(
        installations, installations.c.release_id == releases.c.id).where(
            releases.c.draft_id == draft_id, releases.c.draft_revision == expected_revision,
            releases.c.approval_id == approval_id)).first()
    if previous:
        from lzo.worker.service import JobService
        return JobService(uow, settings_for(uow)).get(actor, previous.job_id).job
    change = descriptor(uow, actor, draft_id)
    require_migration(row, change)
    ApprovalService(uow).require_executable(actor, approval_id, change.change_digest, change.baseline)
    document = SchemaDocument.model_validate(row["document"])
    identifier = uuid4()
    add_resource(uow, identifier, "schema.release")
    append_revision(uow, actor, identifier, 0, {"draft_id": str(draft_id), "approval_id": str(approval_id)},
                    "schema.release.prepared")
    store = store_for(uow)
    source_graph = schema_graph(document, row["imports"])
    serialized = [("schema.ttl", source_graph.serialize(format="turtle"), "text/turtle"),
                  ("shapes.ttl", effective_shapes(document), "text/turtle"),
                  ("ui.json", json.dumps(document.ui_metadata, sort_keys=True, ensure_ascii=False), "application/json")]
    artifacts = []
    for filename, content, media_type in serialized:
        blob = store.put(io.BytesIO(content.encode()), media_type)
        register_schema_artifact(uow, actor, identifier, blob, filename)
        artifacts.append(blob)
    defaults = _genesis(uow, actor)
    bundle = create_execution_bundle(uow, actor, identifier, None, defaults["authority"],
        defaults["ruling"], defaults["inference"], artifacts[1].sha256)
    # Revalidate real exact published Shape bytes using the durable bundle.
    with store.open(artifacts[1]) as stream:
        shapes = stream.read().decode()
    SemanticaAdapter().validate("", shapes, bundle)
    uow.session.execute(insert(releases).values(id=identifier, draft_id=draft_id,
        draft_revision=expected_revision, version=f"schema-{identifier}", document=row["document"],
        imports=row["imports"], artifacts=_json([asdict(blob) for blob in artifacts]),
        approval_id=approval_id, bundle_id=bundle.id, impact=impact_view(change).model_dump(mode="json")))
    uow.session.execute(insert(installations).values(release_id=identifier, state="installing"))
    governance_refs = protected_closure(uow, set(change.target_ids) |
        {approval_id, bundle.id, *defaults.values(), *(b.version_id for b in artifacts)}) - {identifier}
    # Definition consumers need actual imported/declared sources, not incidental
    # objects assessed by the governance impact analysis. The draft's persistent
    # dependencies retain its original/base source provenance.
    definition_refs = definition_source_closure(uow,
        {draft_id, bundle.id, *defaults.values(), *(b.version_id for b in artifacts)}) - {identifier}
    graphs = {}
    for role, blob in zip(("schema", "shape"), artifacts):
        with store.open(blob) as stream:
            dataset = parse(stream.read().decode())
        normalized = skolemize(ParsedSemanticDocument(dataset, blob, "turtle"))
        graphs[role] = ordered(Triple(str(s), str(p), term_from_node(o)) for s, p, o, _ in normalized.dataset.quads())
    pinned = [RevisionRef(ref, head(uow, ref)["revision"], head(uow, ref)["event_id"])
              for ref in sorted(governance_refs, key=str)]
    payload = {"graphs": {role: [asdict(t) for t in triples] for role, triples in graphs.items()},
               "references": [str(identifier), *map(str, sorted(governance_refs, key=str))],
               "dependency_revisions": _json([asdict(ref) for ref in pinned]),
               "artifacts": _json([asdict(blob) for blob in artifacts]), "bundle_id": str(bundle.id)}
    event = append_revision(uow, actor, identifier, head(uow, identifier)["revision"], payload, "schema.install")
    bind_dependencies(uow, identifier, definition_refs)
    for protected in (bundle.id, *(b.version_id for b in artifacts)):
        bind_all_events(uow, protected, definition_refs | {identifier})
    for history in uow.session.execute(select(revisions).where(
            revisions.c.resource_id == identifier)).mappings():
        private = history["event_type"] in ("schema.release.prepared", "schema.install")
        bind_history_dependencies(uow, history["event_id"],
                                  (governance_refs if private else definition_refs) | {identifier})
    for triple in graphs["schema"]:
        axiom_id = "urn:lzo:axiom:" + hashlib.sha256((artifacts[0].sha256 + "\n" + triple_key(triple)).encode()).hexdigest()
        uow.session.execute(insert(axioms).values(iri=axiom_id, release_id=identifier, triple=asdict(triple)))
    job = ProjectionService(uow, FusekiRepository(settings_for(uow).fuseki_url)).schedule(actor, event)
    uow.session.execute(update(installations).where(installations.c.release_id == identifier).values(
        event_id=event.event_id, job_id=job.id))
    uow.session.execute(update(drafts).where(drafts.c.id == draft_id).values(state="installing"))
    return job


def _resolve_projection(uow, row):
    from lzo.semantic.rdf import RdfTerm
    payload = row["payload"]
    # Immutable evidence is checked on every schedule and retry.
    for encoded in payload["artifacts"]:
        blob = BlobRef(**(encoded | {"version_id": UUID(encoded["version_id"])}))
        with store_for(uow).open(blob):
            pass
    return ProjectionContent(
        {role: tuple(Triple(value["subject_iri"], value["predicate_iri"], RdfTerm(**value["object"]))
                     for value in triples) for role, triples in payload["graphs"].items()},
        tuple(UUID(value) for value in payload["references"]),
        tuple(RevisionRef(UUID(value["resource_id"]), value["revision"], UUID(value["event_id"]))
              for value in payload["dependency_revisions"]))


def _before_install(uow, actor, event):
    if actor is None:
        raise ApiError("SCHEMA_ACTOR_REQUIRED", 403, "Schema installation requires its initiating actor")
    row = uow.session.execute(select(releases).where(releases.c.id == event["resource_id"])).mappings().one()
    state = uow.session.execute(select(installations.c.state).where(installations.c.release_id == row["id"])).scalar_one()
    if state in ("published", "deprecated"):
        authorize(uow, actor, row["id"], "schema.publish")
        return
    change = descriptor(uow, actor, row["draft_id"])
    require_migration(get_draft(uow, actor, row["draft_id"]), change)
    ApprovalService(uow).require_executable(actor, row["approval_id"], change.change_digest, change.baseline)
    if row["impact"]["digest"] != change.change_digest or row["impact"]["baseline"] != change.baseline:
        raise Conflict("APPROVAL_STALE", "Installation inputs changed")


def _after_install(uow, actor, event, receipt):
    row = uow.session.execute(select(releases).where(releases.c.id == event["resource_id"])).mappings().one()
    state = uow.session.execute(select(installations.c.state).where(installations.c.release_id == row["id"])).scalar_one()
    if state in ("published", "deprecated"):
        return
    if receipt.state != "synced":
        uow.session.execute(update(installations).where(installations.c.release_id == row["id"]).values(
            state="failed", error_code="RDF_PROJECTION_FAILED"))
        if head(uow, row["draft_id"])["revision"] == row["draft_revision"]:
            uow.session.execute(update(drafts).where(drafts.c.id == row["draft_id"]).values(state="failed"))
        return
    active = uow.session.execute(select(catalog).with_for_update()).mappings().one()
    expected = row["impact"]["baseline"][str(active["resource_id"])]
    if active["revision"] != expected:
        raise Conflict("APPROVAL_STALE", "Active schema baseline changed")
    draft = get_draft(uow, actor, row["draft_id"])
    ref = append_revision(uow, actor, row["draft_id"], row["draft_revision"],
                          {"release_id": str(row["id"])}, "schema.draft.published")
    uow.session.execute(update(drafts).where(drafts.c.id == row["draft_id"]).values(
        state="published", revision=ref.revision))
    active_ref = append_revision(uow, actor, active["resource_id"], expected,
                                 {"release_id": str(row["id"])}, "schema.catalog.activated")
    protected = definition_source_closure(uow, [row["id"]])
    bind_dependencies(uow, row["draft_id"], protected, ref.event_id)
    # Bind history, not the global catalog resource's permanent visibility.
    for reference in protected:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from lzo.governance.history import revision_visibility_dependencies
        uow.session.execute(pg_insert(revision_visibility_dependencies).values(
            event_id=active_ref.event_id, resource_id=reference).on_conflict_do_nothing())
    uow.session.execute(update(catalog).values(revision=active_ref.revision,
        active_release_id=row["id"], active_bundle_id=row["bundle_id"]))
    if active["active_release_id"]:
        uow.session.execute(update(installations).where(
            installations.c.release_id == active["active_release_id"]).values(state="deprecated"))
    uow.session.execute(update(installations).where(installations.c.release_id == row["id"]).values(
        state="published", error_code=None))
    event = append_revision(uow, actor, row["id"], head(uow, row["id"])["revision"],
                            {"bundle_id": str(row["bundle_id"]), "projection_digest": receipt.digest},
                            "schema.release.published")
    bind_dependencies(uow, row["id"], protected, event.event_id)


def release_view(uow, row):
    state = uow.session.execute(select(installations.c.state).where(installations.c.release_id == row["id"])).scalar_one()
    return SchemaReleaseView(id=row["id"], revision=head(uow, row["id"])["revision"],
        version=row["version"], state=state, document=row["document"], artifacts=row["artifacts"])


def _installation_result(uow, actor, event, receipt):
    """Correlate completed publication to its exact immutable install event."""
    from lzo.worker.service import JobResultRef
    if (receipt.state != "synced" or receipt.resource_id != event["resource_id"]
            or receipt.revision != event["revision"]):
        return None
    installation = uow.session.execute(select(installations).where(
        installations.c.release_id == event["resource_id"],
        installations.c.event_id == event["event_id"])).mappings().first()
    if installation is None or installation["state"] not in ("published", "deprecated"):
        return None
    return JobResultRef(kind="schema.release", resource_id=event["resource_id"],
                        api_path=f"/api/v1/schema/releases/{event['resource_id']}")


def get_release(uow, actor, identifier):
    authorize(uow, actor, identifier)
    row = uow.session.execute(select(releases).where(releases.c.id == identifier)).mappings().first()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    return release_view(uow, row)


def get_released_form_schema(uow, actor, release_id, class_iri) -> FormSchemaView:
    """Only PG-published immutable versions are consumable as released forms."""
    release = get_release(uow, actor, release_id)
    if release.state not in ("published", "deprecated"):
        raise Conflict("SCHEMA_NOT_PUBLISHED", "Schema publication has not completed")
    return form_schema(release.document, class_iri, release_id)


def list_releases(uow, actor, cursor=None):
    validate_actor(uow, actor)
    query = visible_statement(actor, releases)
    if cursor:
        try:
            query = query.where(releases.c.id > UUID(cursor))
        except ValueError:
            raise ApiError("INVALID_CURSOR", 422, "Invalid list cursor") from None
    size = min(settings_for(uow).list_page_size, settings_for(uow).list_max_page_size)
    rows = uow.session.execute(query.order_by(releases.c.id).limit(size+1)).mappings().all()
    return Page([release_view(uow, row) for row in rows[:size]], str(rows[size-1]["id"]) if len(rows)>size else None)


def diff_release(uow, actor, target, base):
    a, b = get_release(uow, actor, base), get_release(uow, actor, target)
    row = uow.session.execute(select(releases).where(releases.c.id == target)).mappings().one()
    # This DTO includes the full immutable impact report. Public definition
    # visibility alone does not authorize its business-object references.
    for reference in row["impact"]["baseline"]:
        authorize(uow, actor, UUID(reference))
    return SchemaDiffView(base_release_id=base, target_release_id=target,
        changes={"before": a.document.model_dump(mode="json"), "after": b.document.model_dump(mode="json")},
        impact=ImpactReport.model_validate(row["impact"]))


register_policy("schema.publish", _descriptor)
# S02 owns this discriminator. An accidental second deployment registration
# must fail rather than silently replace another trusted metadata dispatcher.
def _metadata_policy(uow, targets, command):
    return _descriptor(uow, targets, command, metadata=True)

if "metadata.update" in policies and policies["metadata.update"].__module__ != __name__:
    raise RuntimeError("metadata.update is already owned by another trusted policy")
register_policy("metadata.update", _metadata_policy)
register_projection("schema.install", _resolve_projection, version="ontology-v1", dirty_kind="schema",
                    action="schema.publish", before_publish=_before_install, after_publish=_after_install,
                    result_resolver=_installation_result)
from lzo.worker.service import register_result_kind
register_result_kind("schema.release", "/api/v1/schema/releases/{resource_id}")
for kind in ("schema.catalog.created", "schema.catalog.activated", "schema.draft.created",
             "schema.draft.updated", "schema.draft.published", "schema.release.prepared",
             "schema.release.published", "schema.genesis.created", "schema.bundle.created"):
    register_nonsemantic(kind, "ontology-v1")
