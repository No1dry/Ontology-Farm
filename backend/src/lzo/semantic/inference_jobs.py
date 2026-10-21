"""Complete affected footprints and original-initiator inference jobs."""
from dataclasses import asdict
from datetime import datetime
from uuid import UUID, uuid4
import httpx

from sqlalchemy import Column, DateTime, Integer, String, Table, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID, insert

from lzo.auth.models import resource_scopes
from lzo.auth.permissions import _current_uow, permission_context, validate_actor
from lzo.contracts import JobRef, RevisionRef
from lzo.errors import ApiError, Conflict
from lzo.governance.models import metadata, outbox_events, platform_watermarks, revisions
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import digest_change
from lzo.ontology.drafts import authorize, definition_source_closure, settings_for
from lzo.ontology.releases import current_execution_bundle
from lzo.semantic import facts
from lzo.semantic.fuseki import FusekiRepository, RepresentationError, graph_iri
from lzo.semantic.inference import (InferenceInput, InferenceScope, InferenceBudget,
    InferenceResult, SchemaAxiomEvidence, SUPPORTED_PROFILE, _TripleBudget, derive, derived_assertion_id)
from lzo.semantic.models import (objects, assertions, supports, object_dependencies,
    projection_heads, inference_requests, inference_inputs, inference_fence)
from lzo.semantic.proofs import get_type_declaration_evidence, _ref
from lzo.semantic.projection import (ProjectionService, ProjectionContent,
    register_nonsemantic, register_projection)
from lzo.semantic.rdf import ordered, logical_digest
from lzo.worker.service import enqueue, register_handler, JobService


def _snapshot_table(name):
    return Table(name, metadata, Column("id", PGUUID(as_uuid=True), primary_key=True),
                 Column("payload", JSONB, nullable=False))


runs = _snapshot_table("inference_runs")
components = _snapshot_table("inference_components")
manifests = _snapshot_table("inference_manifests")
current = Table("inference_current", metadata, Column("singleton", Integer, primary_key=True),
                Column("assembly_id", PGUUID(as_uuid=True)),
                Column("complete_manifest_id", PGUUID(as_uuid=True)))
coverage = Table("inference_coverage", metadata,
    Column("event_id", PGUUID(as_uuid=True), primary_key=True),
    Column("run_id", PGUUID(as_uuid=True), nullable=False))
schedules = Table("inference_schedules", metadata,
    Column("event_id", PGUUID(as_uuid=True), primary_key=True),
    Column("bundle_id", PGUUID(as_uuid=True), primary_key=True),
    Column("job_id", PGUUID(as_uuid=True), nullable=False))
terminals = Table("inference_terminals", metadata,
    Column("run_id", PGUUID(as_uuid=True), primary_key=True),
    Column("state", String(32), nullable=False),
    Column("audit_event_id", PGUUID(as_uuid=True), nullable=False))


def _terminal_state(uow, run_id):
    return uow.session.execute(select(terminals.c.state).where(
        terminals.c.run_id == run_id)).scalar_one_or_none()


def _record_terminal(uow, actor, run_id, run, state, payload, extra_refs=()):
    from lzo.ontology.drafts import bind_history_dependencies
    existing = _terminal_state(uow, run_id)
    if existing is not None:
        return
    event_type = {"published": "inference.manifest.published",
        "superseded": "inference.manifest.superseded", "retired": "inference.run.retired"}[state]
    audit = append_revision(uow, actor, run_id, facts.revision_ref(uow, run_id).revision,
                            payload, event_type)
    bind_history_dependencies(uow, audit.event_id,
        {UUID(i) for i in run["scope"]["reference_ids"]} | set(extra_refs))
    uow.session.execute(insert(terminals).values(
        run_id=run_id, state=state, audit_event_id=audit.event_id))
    if state in ("retired", "superseded"):
        uow.session.execute(update(inference_requests).where(
            inference_requests.c.event_id.in_(
                [UUID(i) for i in run["scope"]["covered_input_event_ids"]]),
            ~exists(select(coverage.c.event_id).where(
                coverage.c.event_id == inference_requests.c.event_id))
        ).values(state="pending", error_code="INFERENCE_RECOMPUTE_REQUIRED"))
    return audit


def _generation_obsolete(uow, run):
    scope = InferenceScope.model_validate(run["scope"])
    base_id, base = _manifest(uow)
    if base_id == scope.base_generation_id:
        return False
    if scope.mode == "global" or base is None or not set(run["replaced"]) <= set(base["components"]):
        return True
    selected = {str(p.resource_id) for p in scope.members}
    return any(set(v["members"]) & selected for key, v in base["components"].items()
               if key not in run["replaced"])


def retire_inference_run(uow, actor, run_id, expected_revision):
    """Explicit authorized retirement, never a successful projection or coverage."""
    from lzo.semantic.models import projection_events, inference_output_retirements
    from lzo.worker.models import jobs
    with permission_context(uow):
        validate_actor(uow, actor)
        lock_ledger(uow)
        authorize(uow, actor, run_id)
        run = uow.session.execute(select(runs.c.payload).where(runs.c.id == run_id)).scalar_one()
        authorize(uow, actor, UUID(run["primary"]), run["action"])
        kind = uow.session.execute(select(resource_scopes.c.security_class).where(
            resource_scopes.c.resource_id == UUID(run["primary"]))).scalar_one()
        authorize(uow, actor, UUID(run["primary"]), "schema.publish" if kind == "schema" else "edit")
        for ref in run["scope"]["reference_ids"]:
            authorize(uow, actor, UUID(ref))
        if facts.revision_ref(uow, run_id).revision != expected_revision:
            raise Conflict("REVISION_CONFLICT", "Inference run revision changed")
        if _terminal_state(uow, run_id) == "retired":
            return
        if _terminal_state(uow, run_id) is not None:
            raise Conflict("INFERENCE_RUN_TERMINAL", "Inference run already has a terminal disposition")
        outputs = uow.session.execute(select(components).where(
            components.c.payload["run_id"].astext == str(run_id))).mappings().all()
        _, assembly = _manifest(uow)
        if assembly and set(map(str, (r["id"] for r in outputs))) & set(assembly["components"]):
            raise Conflict("INFERENCE_RUN_REFERENCED", "A selected generation references this run")
        obsolete = _generation_obsolete(uow, run)
        reason = "INFERENCE_GENERATION_SUPERSEDED" if obsolete else None
        if not obsolete:
            try:
                _validate_run(uow, actor, run)
            except ApiError as error:
                if error.code != "INFERENCE_INPUT_STALE":
                    raise
                obsolete, reason = True, error.code
        computation = uow.session.execute(select(jobs).where(jobs.c.kind == "inference.compute",
            jobs.c.payload["run_id"].astext == str(run_id))).mappings().one()
        records = []
        for output in outputs:
            authorize(uow, actor, output["id"])
            event = uow.session.execute(select(projection_events, revisions.c.event_type).join(
                revisions, revisions.c.event_id == projection_events.c.event_id).where(
                projection_events.c.resource_id == output["id"])).mappings().one()
            if event["event_type"] != "inference.output.ready" or event["revision"] != 1:
                raise Conflict("INFERENCE_OUTPUT_INVALID", "Output event binding is invalid")
            publication = uow.session.execute(select(jobs).where(jobs.c.kind == "rdf.project",
                jobs.c.payload["event_id"].astext == str(event["event_id"]))).mappings().one()
            records.append((event, publication))
        complete_outputs = (len(outputs) == len(run["components"]) and
            sorted(sorted(r["payload"]["members"]) for r in outputs) ==
            sorted(sorted(p["members"]) for p in run["components"]))
        compute_terminal = computation["status"] == "cancelled" or (
            computation["status"] == "succeeded" and complete_outputs and bool(records))
        cancelled = compute_terminal and all(job["status"] == "cancelled" for _, job in records)
        if not obsolete and not cancelled:
            raise Conflict("INFERENCE_RUN_NOT_OBSOLETE", "Run is neither obsolete nor explicitly cancelled")
        audit = _record_terminal(uow, actor, run_id, run, "retired",
            {"reason": reason or "INFERENCE_PUBLICATION_CANCELLED",
             "output_component_ids": [str(r["id"]) for r in outputs]},
            {r["id"] for r in outputs})
        for event, _ in records:
            if event["state"] not in ("synced", "superseded"):
                uow.session.execute(insert(inference_output_retirements).values(
                    event_id=event["event_id"], run_id=run_id, actor_id=actor.subject_id,
                    reason=reason or "INFERENCE_PUBLICATION_CANCELLED", audit_event_id=audit.event_id))
        ProjectionService(uow, None).advance_contiguous()


def affected_members(members, edges, previous_components, seeds):
    """Old + new connectivity, including removed bridges and incoming edges.

    Inventory is unfiltered. Callers must authorize every selected member and
    dependency before loading its semantic content; denied is never absent.
    """
    selected = set(seeds) & set(members)
    previous = [set(c) & set(members) for c in previous_components]
    while True:
        before = set(selected)
        for component in previous:
            if component & selected:
                selected.update(component)
        for left, right in edges:
            if left in selected or right in selected:
                selected.update((left, right))
        if selected == before:
            return selected & set(members)


def _inventory(uow):
    """Trusted unfiltered membership index; never returned to an unauthorized caller."""
    rows = uow.session.execute(select(objects.c.id, objects.c.iri, objects.c.revision,
        objects.c.payload["state"].astext.label("state"),
        objects.c.next_transition_at)).mappings().all()
    heads = {str(r["id"]): r["revision"] for r in rows}
    iri_ids = {r["iri"]: str(r["id"]) for r in rows}
    # Endpoints alone establish the full boundary; no assertion values/support
    # payloads are loaded here. Future accepted links conservatively connect.
    endpoints = uow.session.execute(select(assertions.c.resource_id,
        assertions.c.payload["object"]["value"].astext).where(
            assertions.c.payload["state"].astext == "accepted",
            assertions.c.payload["object"]["kind"].astext == "iri",
            or_(assertions.c.payload["valid_to"].astext.is_(None),
                assertions.c.payload["valid_to"].astext.cast(DateTime(timezone=True)) > func.clock_timestamp()),
            exists(select(supports.c.id).where(supports.c.assertion_id == assertions.c.id,
                                              supports.c.active.is_(True))))).all()
    endpoint_owners = {}
    active = {str(r["id"]) for r in rows if r["state"] != "inactive"}
    for source, target in endpoints:
        if str(source) in active:
            endpoint_owners.setdefault(target, set()).add(str(source))
    edges = set()
    for endpoint, owners in endpoint_owners.items():
        if endpoint in iri_ids:
            owners.add(iri_ids[endpoint])
        first = min(owners)
        edges.update((first, other) for other in owners if other != first)
    boundaries = {str(r["id"]): r["next_transition_at"].isoformat()
                  if r["next_transition_at"] else None for r in rows}
    return {"heads": heads, "edges": sorted(edges), "boundaries": boundaries}


def _parts(members, edges):
    todo, result = set(members), []
    while todo:
        part = affected_members(members, edges, (), {min(todo)})
        result.append(sorted(part))
        todo -= part
    return result or [[]]  # schema-only platform still has a complete generation


def _manifest(uow):
    identifier = uow.session.execute(select(current.c.assembly_id)).scalar_one()
    if identifier is None:
        return None, None
    return identifier, uow.session.execute(select(manifests.c.payload).where(
        manifests.c.id == identifier)).scalar_one()


def _profile(uow, actor, bundle):
    if current_execution_bundle(uow, actor) != bundle:
        raise Conflict("INFERENCE_INPUT_STALE", "Execution bundle is no longer current")
    row = uow.session.execute(select(revisions.c.payload).where(
        revisions.c.resource_id == bundle.inference_version_id, revisions.c.revision == 1)).scalar_one()
    expected = {k: row.get(k) for k in SUPPORTED_PROFILE}
    if expected != SUPPORTED_PROFILE or set(row) - {*SUPPORTED_PROFILE, "approval_id"}:
        raise Conflict("INFERENCE_PROFILE_UNSUPPORTED", "Inference profile is disabled or unsupported")


def _member_inputs(uow, actor, ids, bundle, repository, at):
    values, declarations, refs, boundaries, generations = [], [], set(), [], {}
    for key in sorted(ids):
        identifier = UUID(key)
        row = facts.get_object_row(uow, actor, identifier)
        refs.add(identifier)
        deps = set(uow.session.execute(select(object_dependencies.c.dependency_id).where(
            object_dependencies.c.resource_id == identifier)).scalars())
        for dependency in deps:
            authorize(uow, actor, dependency)
        refs.update(deps)
        declaration = get_type_declaration_evidence(uow, actor, identifier)
        declarations.append(declaration)
        refs.update(p.resource_id for p in declaration.dependency_refs)
        projected = uow.session.execute(select(projection_heads).where(
            projection_heads.c.resource_id == identifier)).mappings().first()
        if projected is None or projected["revision"] != row["revision"]:
            raise Conflict("INFERENCE_PROJECTION_PENDING", "Current semantic projection is incomplete")
        logical = repository.read_graph(graph_iri(identifier, "accepted"))
        generation = repository.current_revision(identifier)
        if generation is None or generation["revision"] != row["revision"] or generation["digest"] != projected["digest"]:
            raise Conflict("INFERENCE_PROJECTION_PENDING", "Current semantic projection is inconsistent")
        generations[key] = {"revision": row["revision"], "digest": projected["digest"],
                            "accepted_digest": logical_digest(logical)}
        for raw in facts.current_assertions(uow, identifier):
            value = facts.assertion_view(uow, raw)
            if value.state != "accepted":
                continue
            for support in value.supports:
                refs.update(facts.support_references(uow, actor, support))
            if value.decision_ref is not None:
                facts.verify_reference(uow, actor, value.decision_ref)
                refs.add(value.decision_ref.resource_id)
            for boundary in (value.valid_from, value.valid_to):
                if boundary is not None and boundary > at and row["payload"]["state"] != "inactive":
                    boundaries.append(boundary)
            if row["payload"]["state"] != "inactive" and value.valid_from <= at and (
                    value.valid_to is None or at < value.valid_to):
                from lzo.semantic.rdf import Triple
                if Triple(row["iri"], value.predicate_iri, value.object) not in logical:
                    raise Conflict("INFERENCE_PROJECTION_PENDING", "Current time projection is incomplete")
                values.append(value)
    return values, declarations, refs, boundaries, generations


def _prepare(uow, actor, bundle, watermark, trigger=None):
    validate_actor(uow, actor)
    lock_ledger(uow)
    _profile(uow, actor, bundle)
    actual = uow.session.execute(select(platform_watermarks.c.projection_watermark)).scalar_one()
    if watermark != actual:
        raise Conflict("INFERENCE_INPUT_STALE", "Requested projection frontier is obsolete")
    inventory = _inventory(uow)
    base_id, base = _manifest(uow)
    dirty = list(uow.session.execute(select(inference_inputs.c.event_id,
        inference_inputs.c.position, inference_inputs.c.kind, revisions.c.resource_id).join(
            revisions, revisions.c.event_id == inference_inputs.c.event_id).where(
                ~exists(select(coverage.c.event_id).where(coverage.c.event_id == inference_inputs.c.event_id))
            )).mappings())
    # No historical replay: scope is a coherent current snapshot. A semantic
    # change beyond the complete projection frontier must be projected first.
    if any(d["position"] > watermark for d in dirty):
        raise Conflict("INFERENCE_PROJECTION_PENDING", "Semantic inputs exceed the complete projection frontier")
    full = base is None or trigger is None or base["bundle_id"] != str(bundle.id) or any(
        d["kind"] in ("schema", "rules") for d in dirty)
    previous = [entry["members"] for entry in base["components"].values()] if base else []
    members = set(inventory["heads"]) if full else affected_members(
        set(inventory["heads"]), inventory["edges"], previous, {str(trigger.resource_id)})
    if not full and not members:
        raise Conflict("INFERENCE_SCOPE_UNAVAILABLE", "Affected input membership is unavailable")
    covered = [d["event_id"] for d in dirty if full or str(d["resource_id"]) in members]
    refs = definition_source_closure(uow, {bundle.id})
    primary = trigger.resource_id if trigger else bundle.schema_release_id
    refs.add(primary)
    for identifier in refs:
        authorize(uow, actor, identifier)
    action = "edit" if str(primary) in inventory["heads"] else "schema.publish"
    authorize(uow, actor, primary, action)
    repository = FusekiRepository(settings_for(uow).fuseki_url)
    schema = repository.read_graph(graph_iri(bundle.schema_release_id, "schema"))
    from lzo.ontology.models import axioms, releases
    artifacts = uow.session.execute(select(releases.c.artifacts).where(
        releases.c.id == bundle.schema_release_id)).scalar_one()
    registered_axioms = tuple(SchemaAxiomEvidence.model_validate({"iri": row["iri"], "triple": row["triple"],
        "release_id": bundle.schema_release_id, "artifact_sha256": artifacts[0]["sha256"]})
        for row in uow.session.execute(select(axioms).where(
            axioms.c.release_id == bundle.schema_release_id)).mappings())
    if {a.triple for a in registered_axioms} != set(schema):
        raise Conflict("INFERENCE_SCHEMA_INCONSISTENT", "Exact published axiom registry is inconsistent")
    at = uow.session.execute(select(func.clock_timestamp())).scalar_one()
    component_inputs = []
    all_boundaries = []
    all_generations = {}
    for part in _parts(members, inventory["edges"]):
        values, declarations, local_refs, boundaries, generations = _member_inputs(
            uow, actor, part, bundle, repository, at)
        refs.update(local_refs)
        all_boundaries.extend(boundaries)
        all_generations.update(generations)
        component_inputs.append({"members": part,
            "references": sorted(str(i) for i in local_refs),
            "next_boundary": min(boundaries).isoformat() if boundaries else None,
            "input": InferenceInput(bundle=bundle, input_watermark=watermark,
                assertions=tuple(values), schema_triples=schema, type_declarations=tuple(declarations),
                evaluation_time=at, schema_axioms=registered_axioms).model_dump(mode="json")})
    replaced = sorted(key for key, entry in (base["components"].items() if base else ())
                      if full or set(entry["members"]) & members)
    for key in replaced:
        authorize(uow, actor, UUID(key))
        refs.add(UUID(key))
    pins = facts.json_value(tuple(facts.revision_ref(uow, i) for i in sorted(refs, key=str)))
    input_digest = digest_change({"components": component_inputs, "generations": all_generations,
                                  "dependency_revisions": pins})
    scope = InferenceScope(mode="global" if full else "affected",
        members=tuple(facts.revision_ref(uow, UUID(i)) for i in sorted(members)),
        trigger_revisions=(trigger,) if trigger else (),
        evaluation_time=at, next_boundary=min(all_boundaries) if all_boundaries else None,
        authorization_version=actor.authorization_version, reference_ids=tuple(sorted(refs, key=str)),
        base_generation_id=base_id, inventory_stamp=digest_change(inventory),
        covered_input_event_ids=tuple(sorted(covered, key=str)), input_watermark=watermark, bundle_id=bundle.id,
        input_manifest_digest=input_digest)
    for part in component_inputs:
        part["input"]["scope"] = scope.model_dump(mode="json")
    return {"scope": scope.model_dump(mode="json"), "inventory": inventory,
        "components": component_inputs, "replaced": replaced, "generations": all_generations,
        "primary": str(primary), "action": action, "bundle_id": str(bundle.id),
        "dependency_revisions": pins}


def _new_resource(uow, actor, identifier, primary, kind):
    row = dict(uow.session.execute(select(resource_scopes).where(
        resource_scopes.c.resource_id == primary)).mappings().one())
    row.update(resource_id=identifier, resource_kind=kind)
    uow.session.execute(insert(resource_scopes).values(**row))


def _enqueue_run(uow, actor, payload):
    identifier = uuid4()
    _new_resource(uow, actor, identifier, UUID(payload["primary"]), "inference.run")
    refs = [payload["primary"], *payload["scope"]["reference_ids"], str(identifier)]
    refs = list(dict.fromkeys(refs))
    selected = {p["resource_id"] for p in payload["scope"]["members"]}
    # The private operational manifest proves the unfiltered boundary. Ordinary
    # F02 history may reveal only this actor's selected footprint, plus its
    # opaque inventory commitment, never retained hidden membership.
    visible_inventory = {
        "heads": {k: v for k, v in payload["inventory"]["heads"].items() if k in selected},
        "boundaries": {k: v for k, v in payload["inventory"]["boundaries"].items() if k in selected},
        "edges": [edge for edge in payload["inventory"]["edges"] if set(edge) <= selected]}
    revision = append_revision(uow, actor, identifier, 0,
        {**payload, "inventory": visible_inventory}, "inference.run.prepared")
    from lzo.ontology.drafts import bind_history_dependencies
    bind_history_dependencies(uow, revision.event_id, {UUID(i) for i in refs})
    uow.session.execute(insert(runs).values(id=identifier, payload={**payload,
        "initiator_id": str(actor.subject_id)}))
    job = enqueue(uow, actor, "inference.compute", {"resource_id": payload["primary"],
        "run_id": str(identifier), "reference_ids": refs, "action": payload["action"]}, UUID(payload["bundle_id"]))
    return job


def recompute_inference(actor, bundle, input_watermark) -> JobRef:
    uow = _current_uow.get()
    return _enqueue_run(uow, actor, _prepare(uow, actor, bundle, input_watermark))


def schedule_inference(uow, actor, revision, bundle) -> JobRef:
    with permission_context(uow):
        from lzo.semantic.projection import _revision
        if _revision(uow, revision)["actor_id"] != actor.subject_id:
            raise ApiError("INFERENCE_ACTOR_REQUIRED", 403, "Trigger requires its original initiator")
        validate_actor(uow, actor)
        lock_ledger(uow)
        _profile(uow, actor, bundle)
        existing = uow.session.execute(select(schedules.c.job_id).where(
            schedules.c.event_id == revision.event_id, schedules.c.bundle_id == bundle.id)).scalar_one_or_none()
        if existing:
            view = JobService(uow, settings_for(uow)).get(actor, existing)
            from lzo.worker.models import jobs
            run_id = UUID(uow.session.execute(select(jobs.c.payload).where(
                jobs.c.id == existing)).scalar_one()["run_id"])
            if _terminal_state(uow, run_id) in ("retired", "superseded"):
                raise Conflict("INFERENCE_RECOMPUTE_REQUIRED", "Enqueue a fresh authorized recomputation")
            return view.job
        watermark = uow.session.execute(select(platform_watermarks.c.projection_watermark)).scalar_one()
        payload = _prepare(uow, actor, bundle, watermark, revision)
        job = _coalesced_job(uow, actor, payload, revision.event_id)
        if job is None:
            job = _enqueue_run(uow, actor, payload)
        uow.session.execute(insert(schedules).values(event_id=revision.event_id, bundle_id=bundle.id, job_id=job.id))
        uow.session.execute(update(inference_requests).where(inference_requests.c.event_id == revision.event_id)
                            .values(state="pending", error_code=None))
        return job


def _coalesced_job(uow, actor, payload, event_id):
    """Bind duplicate triggers only to an unchanged, already-covered snapshot."""
    from lzo.worker.models import jobs
    candidates = uow.session.execute(select(jobs.c.id, jobs.c.payload).where(
        jobs.c.kind == "inference.compute", jobs.c.initiator_id == actor.subject_id,
        jobs.c.bundle_id == UUID(payload["bundle_id"]),
        jobs.c.status.in_(("queued", "running"))).order_by(jobs.c.created_at.desc())
        .limit(settings_for(uow).job_claim_scan_limit)).mappings()
    scope = payload["scope"]
    for candidate in candidates:
        if _terminal_state(uow, UUID(candidate["payload"]["run_id"])) is not None:
            continue
        other = uow.session.execute(select(runs.c.payload).where(
            runs.c.id == UUID(candidate["payload"]["run_id"]))).scalar_one()
        old = other["scope"]
        if str(event_id) not in old["covered_input_event_ids"]:
            continue
        if not set(scope["covered_input_event_ids"]) <= set(old["covered_input_event_ids"]):
            continue
        keys = ("mode", "members", "authorization_version", "reference_ids",
                "base_generation_id", "inventory_stamp", "next_boundary", "bundle_id")
        if any(old[k] != scope[k] for k in keys):
            continue
        if (other["generations"] != payload["generations"]
                or other["dependency_revisions"] != payload["dependency_revisions"]):
            continue
        def evidence(value):
            return [{**part, "input": {k: v for k, v in part["input"].items()
                if k not in ("scope", "evaluation_time", "input_watermark")}}
                for part in value["components"]]
        if evidence(other) != evidence(payload):
            continue
        # _prepare has just verified all actual roots/rights/generations. Equal
        # next-boundary and a current preparation prove the same validity window.
        return JobService(uow, settings_for(uow)).get(actor, candidate["id"]).job
    return None


def try_schedule_inference(uow, actor, revision):
    """Wider scope failure never undoes a successful base projection."""
    try:
        with uow.session.begin_nested(), permission_context(uow):
            bundle = current_execution_bundle(uow, actor)
            schedule_inference(uow, actor, revision, bundle)
    except (ApiError, httpx.HTTPError, RepresentationError) as error:
        code = error.code if isinstance(error, ApiError) else "INFERENCE_INPUT_UNAVAILABLE"
        uow.session.execute(update(inference_requests).where(inference_requests.c.event_id == revision.event_id)
                            .values(state="pending", error_code=code))
        if code == "INFERENCE_PROJECTION_PENDING":
            from lzo.semantic.projection import _outbox, producers, _revision
            from lzo.semantic.models import projection_events
            from lzo.worker.service import enqueue_outbox
            registration = uow.session.execute(select(projection_events).where(
                projection_events.c.event_id == revision.event_id)).mappings().one()
            producer = producers[_revision(uow, revision)["event_type"]]
            enqueue_outbox(uow, actor, "inference.dispatch", _outbox(uow, revision)["id"],
                {"resource_id": str(revision.resource_id), "revision": revision.revision,
                 "event_id": str(revision.event_id), "reference_ids": registration["reference_ids"],
                 "bundle_id": str(bundle.id), "action": producer.action}, bundle.id)
    # A later event may close the frontier gap for earlier requests. Retry only
    # this exact initiator; other actors retain their own durable dispatch job.
    from lzo.semantic.models import projection_events
    from lzo.worker.models import jobs
    pending = uow.session.execute(select(inference_requests.c.event_id).join(
        projection_events, projection_events.c.event_id == inference_requests.c.event_id).where(
            inference_requests.c.state == "pending", projection_events.c.initiator_id == actor.subject_id,
            inference_requests.c.event_id != revision.event_id).order_by(inference_requests.c.position)
            .limit(settings_for(uow).job_claim_scan_limit)).scalars().all()
    for event_id in pending:
        row = uow.session.execute(select(revisions).where(revisions.c.event_id == event_id)).mappings().one()
        try:
            with uow.session.begin_nested(), permission_context(uow):
                selected = current_execution_bundle(uow, actor)
                pinned = uow.session.execute(select(jobs.c.bundle_id).where(jobs.c.kind == "inference.dispatch",
                    jobs.c.payload["event_id"].astext == str(event_id))).scalar_one_or_none()
                if pinned is not None and pinned != selected.id:
                    continue
                schedule_inference(uow, actor, RevisionRef(row["resource_id"], row["revision"], event_id), selected)
        except (ApiError, httpx.HTTPError, RepresentationError):
            pass  # Durable reason/dispatch remain; no hidden-input fallback.


def _references(payload):
    return tuple(UUID(i) for i in payload["reference_ids"])


def _action(uow, payload):
    row = uow.session.execute(select(runs.c.payload).where(runs.c.id == UUID(payload["run_id"]))).scalar_one()
    if row["primary"] != payload["resource_id"] or row["action"] != payload["action"]:
        raise Conflict("JOB_POLICY_CHANGED", "Inference job identity changed")
    return row["action"]


def _dispatch_action(uow, payload):
    from lzo.semantic.projection import _revision, producers
    from lzo.semantic.models import projection_events
    ref = RevisionRef(UUID(payload["resource_id"]), payload["revision"], UUID(payload["event_id"]))
    row = _revision(uow, ref)
    registration = uow.session.execute(select(projection_events).where(
        projection_events.c.event_id == ref.event_id)).mappings().one()
    if registration["reference_ids"] != payload["reference_ids"] or producers[row["event_type"]].action != payload["action"]:
        raise Conflict("JOB_POLICY_CHANGED", "Inference dispatch descriptor changed")
    return payload["action"]


def _dispatch(ctx):
    pending = False
    with ctx.batch():
        ctx.uow.ontology_settings = ctx.uow.job_settings = ctx.runner.settings
        from lzo.ontology.releases import get_execution_bundle
        bundle = get_execution_bundle(ctx.actor, UUID(ctx.payload["bundle_id"]))
        ref = RevisionRef(UUID(ctx.payload["resource_id"]), ctx.payload["revision"], UUID(ctx.payload["event_id"]))
        try:
            with ctx.uow.session.begin_nested():
                job = schedule_inference(ctx.uow, ctx.actor, ref, bundle)
            ctx.checkpoint({"computation_job_id": str(job.id)})
        except ApiError as error:
            ctx.uow.session.execute(update(inference_requests).where(inference_requests.c.event_id == ref.event_id)
                                    .values(state="pending", error_code=error.code))
            pending = error.code == "INFERENCE_PROJECTION_PENDING"
            ctx.checkpoint({"pending_reason": error.code, "computation_enqueued": False})
    if pending:
        # F05's bounded attempts and delayed backoff; no busy loop or elevation.
        raise Conflict("INFERENCE_PROJECTION_PENDING", "Waiting for the complete projection frontier")


def _validate_run(uow, actor, payload):
    scope = InferenceScope.model_validate(payload["scope"])
    unscoped = [{**part, "input": {k: v for k, v in part["input"].items() if k != "scope"}}
                for part in payload["components"]]
    # Input serialization originally contains scope=None; restore that exact
    # pre-binding form when checking the immutable manifest commitment.
    for part in unscoped:
        part["input"]["scope"] = None
    digest = digest_change({"components": unscoped, "generations": payload["generations"],
                            "dependency_revisions": payload["dependency_revisions"]})
    if scope.input_manifest_digest is None or digest != scope.input_manifest_digest:
        raise Conflict("INFERENCE_INPUT_INVALID", "Immutable input manifest does not match its scope")
    for identifier in scope.reference_ids:
        authorize(uow, actor, identifier)
    bundle = InferenceInput.model_validate(payload["components"][0]["input"]).bundle
    _profile(uow, actor, bundle)
    now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
    if scope.next_boundary is not None and now >= scope.next_boundary:
        raise Conflict("INFERENCE_INPUT_STALE", "An input validity boundary has passed")
    inventory = _inventory(uow)
    members = {str(p.resource_id) for p in scope.members}
    if any(inventory["heads"].get(key) != payload["inventory"]["heads"][key] for key in members):
        raise Conflict("INFERENCE_INPUT_STALE", "Selected semantic inputs changed")
    if scope.mode == "global" and digest_change(inventory) != scope.inventory_stamp:
        raise Conflict("INFERENCE_INPUT_STALE", "Global semantic membership changed")
    if affected_members(set(inventory["heads"]), inventory["edges"], (), members) != members:
        raise Conflict("INFERENCE_INPUT_STALE", "An incoming boundary changed")
    repository = FusekiRepository(settings_for(uow).fuseki_url)
    for key, expected in payload["generations"].items():
        actual = repository.current_revision(UUID(key))
        if actual is None or actual["revision"] != expected["revision"] or actual["digest"] != expected["digest"]:
            raise Conflict("INFERENCE_INPUT_STALE", "Logical input generation changed")
        if logical_digest(repository.read_graph(graph_iri(UUID(key), "accepted"))) != expected["accepted_digest"]:
            raise Conflict("INFERENCE_INPUT_STALE", "Logical input reconstruction changed")
    return scope, inventory


def _compute(ctx):
    import time
    with ctx.batch():
        ctx.uow.ontology_settings = ctx.uow.job_settings = ctx.runner.settings
        payload = ctx.uow.session.execute(select(runs.c.payload).where(
            runs.c.id == UUID(ctx.payload["run_id"]))).scalar_one()
        terminal = _terminal_state(ctx.uow, UUID(ctx.payload["run_id"]))
        if terminal is not None:
            ctx.checkpoint({"terminal": terminal, "publication_pending": False})
            return
        if ctx.cursor.get("computed"):
            return
        scope, _ = _validate_run(ctx.uow, ctx.actor, payload)
        settings = ctx.runner.settings
        budget = InferenceBudget(max_triples=settings.inference_max_triples,
            max_proofs=settings.inference_max_proofs, timeout_ms=settings.inference_timeout_ms)
        computed = []
        accounting = _TripleBudget(settings.inference_max_triples)
        started = time.monotonic()
        for part in payload["components"]:
            inputs = InferenceInput.model_validate(part["input"])
            budget = budget.model_copy(update={"timeout_ms": max(0, settings.inference_timeout_ms
                - int((time.monotonic() - started) * 1000))})
            result = derive(inputs, budget, _triple_budget=accounting)
            if not result.complete:
                raise ApiError("INFERENCE_BUDGET_EXCEEDED", 409, "Inference did not complete within its budget")
            computed.append((part, result))
            budget = budget.model_copy(update={
                "max_proofs": max(0, budget.max_proofs - len(result.proofs))})
        _validate_run(ctx.uow, ctx.actor, payload)
        for part, result in computed:
            identifier = uuid4()
            _new_resource(ctx.uow, ctx.actor, identifier, UUID(payload["primary"]), "inference.component")
            value = {"run_id": ctx.payload["run_id"], "members": part["members"],
                "input": part["input"], "result": result.model_dump(mode="json"),
                "assertion_ids": {str(derived_assertion_id(t)): asdict(t) for t in result.conclusions},
                "heads": {key: payload["inventory"]["heads"][key] for key in part["members"]},
                "next_boundary": part["next_boundary"],
                "references": payload["scope"]["reference_ids"]}
            ctx.uow.session.execute(insert(components).values(id=identifier, payload=value))
            event = append_revision(ctx.uow, ctx.actor, identifier, 0, value, "inference.output.ready")
            ProjectionService(ctx.uow, None).schedule(ctx.actor, event)
        watermark = ctx.uow.session.execute(select(platform_watermarks.c.inference_watermark)).scalar_one()
        ctx.checkpoint({"computed": True, "scope_digest": scope.digest(), "scope_mode": scope.mode,
            "input_watermark": scope.input_watermark, "global_inference_watermark": watermark,
            "publication_pending": True})


def _output_content(uow, row):
    value = uow.session.execute(select(components.c.payload).where(components.c.id == row["resource_id"])).scalar_one()
    result = InferenceResult.model_validate(value["result"])
    if not result.complete:
        raise ValueError("Incomplete inference result cannot be projected")
    # Revision refs must be immutable producer output, not today's dependency
    # heads. Resolve from the run's originating snapshot event below.
    run = uow.session.execute(select(runs.c.payload).where(runs.c.id == UUID(value["run_id"]))).scalar_one()
    pinned = run["dependency_revisions"]
    pins = tuple(_ref(p) for p in pinned)
    return ProjectionContent({"derived": result.conclusions},
        (row["resource_id"], *(p.resource_id for p in pins)), pins)


def _before_output(uow, actor, event):
    value = uow.session.execute(select(components.c.payload).where(
        components.c.id == event["resource_id"])).scalar_one()
    run = uow.session.execute(select(runs.c.payload).where(runs.c.id == UUID(value["run_id"]))).scalar_one()
    if actor is None or str(actor.subject_id) != run["initiator_id"]:
        raise ApiError("INFERENCE_ACTOR_REQUIRED", 403, "Inference requires its original initiator")
    run_id = UUID(value["run_id"])
    if _terminal_state(uow, run_id) is not None:
        return
    from lzo.worker.models import jobs
    status = uow.session.execute(select(jobs.c.status).where(
        jobs.c.kind == "inference.compute", jobs.c.payload["run_id"].astext == value["run_id"])).scalar_one()
    if status == "cancelled":
        retire_inference_run(uow, actor, run_id, facts.revision_ref(uow, run_id).revision)
        return
    if status != "succeeded":
        raise Conflict("INFERENCE_COMPUTATION_INACTIVE", "Computation has not completed successfully")
    try:
        _validate_run(uow, actor, run)
        if not _generation_obsolete(uow, run):
            return
    except ApiError as error:
        if error.code != "INFERENCE_INPUT_STALE":
            raise
    retire_inference_run(uow, actor, run_id, facts.revision_ref(uow, run_id).revision)


def _advance_coverage(uow):
    projection = uow.session.execute(select(platform_watermarks.c.projection_watermark)).scalar_one()
    unresolved = uow.session.execute(select(func.min(inference_inputs.c.position)).where(
        inference_inputs.c.position <= projection,
        ~exists(select(coverage.c.event_id).where(coverage.c.event_id == inference_inputs.c.event_id)))).scalar_one()
    prefix = min(projection, unresolved - 1) if unresolved is not None else projection
    uow.session.execute(update(platform_watermarks).values(input_watermark=prefix, inference_watermark=prefix))
    expected = uow.session.execute(select(inference_fence.c.expected_position)).scalar_one()
    resolved = uow.session.execute(select(func.max(inference_inputs.c.position)).where(
        inference_inputs.c.position <= prefix)).scalar_one() or 0
    uow.session.execute(update(inference_fence).values(resolved_position=min(expected, resolved)))


def _after_output(uow, actor, event, receipt):
    if receipt.state != "synced":
        return
    value = uow.session.execute(select(components.c.payload).where(
        components.c.id == event["resource_id"])).scalar_one()
    run_id = UUID(value["run_id"])
    run = uow.session.execute(select(runs.c.payload).where(runs.c.id == run_id)).scalar_one()
    if _terminal_state(uow, run_id) is not None:
        return
    outputs = list(uow.session.execute(select(components).where(
        components.c.payload["run_id"].astext == str(run_id))).mappings())
    if len(outputs) != len(run["components"]):
        raise ValueError("Incomplete component result set")
    from lzo.semantic.models import projection_events
    for output in outputs:
        state = uow.session.execute(select(projection_events.c.state).where(
            projection_events.c.resource_id == output["id"])).scalar_one_or_none()
        if state != "synced":
            return
    try:
        scope, inventory = _validate_run(uow, actor, run)
    except ApiError as error:
        if error.code != "INFERENCE_INPUT_STALE":
            raise
        _record_terminal(uow, actor, run_id, run, "superseded", {"reason": error.code})
        return
    base_id, base = _manifest(uow)
    if base and base.get("run_id") == str(run_id):
        return
    # Rebase disjoint results only while every replaced component is still the
    # exact immutable generation captured at scheduling. Overlap fails closed.
    if _generation_obsolete(uow, run):
        _record_terminal(uow, actor, run_id, run, "superseded",
            {"reason": "INFERENCE_GENERATION_SUPERSEDED"})
        return
    retained = {k: v for k, v in (base["components"].items() if base else ())
                if k not in run["replaced"]}
    if any(set(v["members"]) & {str(p.resource_id) for p in scope.members} for v in retained.values()):
        raise Conflict("INFERENCE_SCOPE_INVALID", "Stored component membership is inconsistent")
    additions = {str(r["id"]): {"members": r["payload"]["members"], "heads": r["payload"]["heads"],
        "next_boundary": r["payload"]["next_boundary"], "bundle_id": run["bundle_id"]} for r in outputs}
    merged = {**retained, **additions}
    membership = [member for entry in merged.values() for member in entry["members"]]
    if len(set(membership)) != len(membership):
        raise Conflict("INFERENCE_SCOPE_INVALID", "Stored component membership is inconsistent")
    if set(membership) != set(inventory["heads"]):
        if set(inventory["heads"]) != set(run["inventory"]["heads"]):
            _record_terminal(uow, actor, run_id, run, "superseded",
                {"reason": "INFERENCE_MEMBERSHIP_CHANGED"})
            return
        raise Conflict("INFERENCE_SCOPE_UNAVAILABLE", "Global component membership is incomplete")
    identifier = uuid4()
    now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
    stale = sorted(key for key, entry in merged.items()
        if entry["bundle_id"] != run["bundle_id"]
        or any(inventory["heads"].get(member) != rev for member, rev in entry["heads"].items())
        or entry["next_boundary"] is not None and now >= datetime.fromisoformat(entry["next_boundary"]))
    pending = uow.session.execute(select(inference_inputs.c.event_id).where(
        ~exists(select(coverage.c.event_id).where(coverage.c.event_id == inference_inputs.c.event_id)),
        inference_inputs.c.event_id.not_in(scope.covered_input_event_ids))).first()
    state = "current" if not stale and pending is None else "assembling"
    manifest = {"run_id": str(run_id), "parent_id": str(base_id) if base_id else None,
                "bundle_id": run["bundle_id"], "components": merged,
                "state": state, "stale_component_ids": stale}
    uow.session.execute(insert(manifests).values(id=identifier, payload=manifest))
    switched = uow.session.execute(update(current).where(
        current.c.assembly_id.is_(None) if base_id is None else current.c.assembly_id == base_id
    ).values(assembly_id=identifier, **({"complete_manifest_id": identifier} if state == "current" else {})))
    if switched.rowcount != 1:
        raise Conflict("INFERENCE_GENERATION_SUPERSEDED", "Global inference generation changed")
    for event_id in scope.covered_input_event_ids:
        uow.session.execute(insert(coverage).values(event_id=event_id, run_id=run_id).on_conflict_do_nothing())
        uow.session.execute(update(inference_requests).where(inference_requests.c.event_id == event_id)
                            .values(state="resolved", error_code=None))
    _advance_coverage(uow)
    # Append-only F02 audit of the actual atomic manifest switch.
    _record_terminal(uow, actor, run_id, run, "published",
        {"manifest_id": str(identifier), "output_component_ids": sorted(additions),
         "replaced_component_ids": run["replaced"], "state": state},
        {UUID(i) for i in additions})


register_handler("inference.compute", _compute, references=_references, action=_action)
register_handler("inference.dispatch", _dispatch, references=_references, action=_dispatch_action)
register_projection("inference.output.ready", _output_content, version="inference-v1",
                    dirty_kind=None, action="view", before_publish=_before_output, after_publish=_after_output)
for _event in ("inference.run.prepared", "inference.manifest.published",
               "inference.manifest.superseded", "inference.run.retired"):
    register_nonsemantic(_event, "inference-v1")
