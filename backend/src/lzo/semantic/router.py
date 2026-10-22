"""Dynamic object HTTP contract on shared authentication and caller-owned UoW."""
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select
from fastapi.encoders import jsonable_encoder
from lzo.auth.router import AuthRequest, authenticated
from lzo.contracts import Page, RevisionRef, JobRef
from lzo.errors import ApiError, parse_if_match, require_idempotency_key
from lzo.governance.history import HistoryService
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.risk import digest_change
from lzo.ontology.router import setup
from lzo.ontology.releases import get_execution_bundle, get_released_form_schema
from lzo.ontology.schemas import FormSchemaView
from lzo.ontology.models import catalog
from lzo.semantic import facts, geometry
from lzo.semantic.schemas import (AssertionInput, AssertionView, CreateLinkRequest, CreateObjectRequest,
    DeactivateObjectRequest, FactPatch, GeoFeatureCollection, GeometryWriteRequest, LinkView,
    ObjectView, UpdateObjectRequest, ValidateFactsRequest, WithdrawLinkRequest, WriteReceipt)
from lzo.semantic.validation import ValidationReport
from lzo.semantic.query import QueryRequest, QueryResult, GraphNeighborhood, execute_query, neighborhood
from lzo.semantic.proofs import CurrentProofView, current_proof
from lzo.semantic.exports import ExportRequest, start_export
from lzo.gis.schemas import SpatialAssetRequest, LocationObservationRequest, SpatialAssetPage

router = APIRouter(prefix="/api/v1", tags=["Objects"])


@router.post("/spatial-assets",response_model=RevisionRef,status_code=201,operation_id="registerSpatialAsset")
def spatial_asset_post(body:SpatialAssetRequest,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.gis.service import register_spatial_asset
    setup(auth)
    version(request,body.asset.expected_revision)
    snapshot=bundle(auth,body.bundle_id)
    return write(auth,request,"spatial.asset.register",body.model_dump(mode="json"),
        lambda:register_spatial_asset(auth.uow,auth.actor,body.asset,snapshot))


@router.get("/objects/{object_id}/spatial-assets",response_model=SpatialAssetPage,operation_id="listSpatialAssets")
def spatial_assets_get(object_id:UUID,limit:int|None=Query(default=None,ge=1,le=1000),cursor:str|None=None,auth:AuthRequest=Depends(authenticated)):
    from lzo.gis.service import list_spatial_assets
    return list_spatial_assets(setup(auth),auth.actor,object_id,limit=limit,cursor=cursor)


@router.post("/location-observations",response_model=RevisionRef,status_code=201,operation_id="registerLocationObservation")
def location_observation_post(body:LocationObservationRequest,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.gis.service import register_location_observation
    setup(auth)
    version(request,body.observation.expected_revision)
    snapshot=bundle(auth,body.bundle_id)
    return write(auth,request,"location.observation.register",body.model_dump(mode="json"),
        lambda:register_location_observation(auth.uow,auth.actor,body.observation,snapshot))


@router.get("/objects/{object_id}/location-observations",operation_id="listLocationObservations")
def location_observations_get(object_id:UUID,start:datetime|None=None,end:datetime|None=None,
                              cursor:str|None=None,auth:AuthRequest=Depends(authenticated)):
    import json
    from lzo.gis.service import list_location_observations
    parsed=None
    try:
        if any(value is not None and value.utcoffset() is None for value in (start,end)):raise ValueError()
        if start and end and start>=end:raise ValueError()
        if cursor:
            value=json.loads(cursor)
            if not isinstance(value,list) or len(value)!=3:raise ValueError()
            parsed=(datetime.fromisoformat(value[0]),UUID(value[1]),UUID(value[2]))
            if parsed[0].utcoffset() is None:raise ValueError()
    except (ValueError,TypeError):raise ApiError("INVALID_TIME_CURSOR",422,"Invalid observation time range or cursor") from None
    return list_location_observations(setup(auth),auth.actor,object_id,start=start,end=end,cursor=parsed)


@router.post("/query/exports", response_model=JobRef, status_code=202, operation_id="createSemanticExport")
def semantic_export(body: ExportRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    result = execute_idempotent(auth.uow, auth.actor,
        require_idempotency_key(request.headers.get("Idempotency-Key")),
        digest_change({"operation":"semantic.export", **body.model_dump(mode="json")}),
        lambda: jsonable_encoder(start_export(auth.uow, auth.actor, body)))
    from lzo.worker.service import JobService
    JobService(auth.uow, auth.service.settings).get(auth.actor, UUID(result["id"]))
    auth.uow.commit()
    return result


@router.post("/query/sparql", response_model=QueryResult, operation_id="executeSparql")
async def sparql_query(body: QueryRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    # A companion task observes disconnects through a thread-safe Event; the
    # query service uses short dedicated transactions around the owned child.
    import asyncio
    from threading import Event
    from starlette.concurrency import run_in_threadpool
    setup(auth)
    cancelled = Event()
    async def watch():
        while not cancelled.is_set():
            if await request.is_disconnected():
                cancelled.set()
                return
            await asyncio.sleep(.02)
    watcher = asyncio.create_task(watch())
    def session_check(uow):
        from lzo.auth.session import SessionService, SESSION_COOKIE
        row = SessionService(uow, auth.service.settings).read(request.cookies.get(SESSION_COOKIE))
        if row["user_id"] != auth.actor.subject_id:
            raise ApiError("UNAUTHENTICATED", 401, "Authentication required")
    try:
        return await run_in_threadpool(execute_query, auth.actor, body,
            cancelled=cancelled.is_set, session_check=session_check)
    finally:
        cancelled.set()
        watcher.cancel()


@router.get("/graph/proofs/{assertion_id}", response_model=CurrentProofView, operation_id="getAssertionProof")
def assertion_proof(assertion_id: UUID, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    return current_proof(auth.actor, assertion_id)


@router.get("/graph/neighborhood", response_model=GraphNeighborhood, operation_id="getGraphNeighborhood")
def graph_neighborhood(resource_id: UUID, hops: int = 1, cursor: str | None = None,
                       auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    return neighborhood(auth.actor, resource_id, hops, cursor)


def write(auth, request, operation, payload, command):
    """A replayed revision receipt retains that event's current ALL visibility."""
    result = execute_idempotent(setup(auth), auth.actor,
        require_idempotency_key(request.headers.get("Idempotency-Key")),
        digest_change({"operation": operation, **payload}), lambda: jsonable_encoder(command()))
    reference = result["revision"] if isinstance(result["revision"], dict) else result
    HistoryService(auth.uow).get(auth.actor, UUID(reference["resource_id"]), reference["revision"])
    auth.uow.commit()
    return result


def version(request, expected=None):
    actual = parse_if_match(request.headers.get("If-Match"))
    if expected is not None and expected != actual:
        raise ApiError("REVISION_HEADER_MISMATCH", 422, "Body and If-Match revisions must agree")
    return actual


def bundle(auth, identifier):
    setup(auth)
    return get_execution_bundle(auth.actor, identifier)


@router.get("/objects", response_model=Page[ObjectView], operation_id="listObjects")
def list_objects(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return facts.list_objects(setup(auth), auth.actor, cursor)


@router.post("/objects", response_model=WriteReceipt, status_code=201, operation_id="createObject")
def create_object(body: CreateObjectRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    snapshot = bundle(auth, body.bundle_id)
    # Replays remain subject to current ALL-reference visibility.
    if auth.uow.session.execute(select(facts.objects.c.id).where(
            facts.objects.c.id == body.create.resource_id)).first():
        facts.get_object_row(auth.uow, auth.actor, body.create.resource_id, "edit")
    return write(auth, request, "object.create", body.model_dump(mode="json"),
                 lambda: facts.register_object(auth.uow, auth.actor, body.create, snapshot))


@router.get("/objects/{id}", response_model=ObjectView, operation_id="getObject")
def get_object(id: UUID, auth: AuthRequest = Depends(authenticated)):
    return facts.object_view(setup(auth), auth.actor, facts.get_object_row(auth.uow, auth.actor, id))


@router.patch("/objects/{id}", response_model=WriteReceipt, operation_id="updateObject")
def update_object(id: UUID, body: UpdateObjectRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    if id != body.patch.resource_id:
        raise ApiError("SUBJECT_MISMATCH", 422, "Path and patch subjects must agree")
    setup(auth)
    facts.get_object_row(auth.uow, auth.actor, id, "edit")
    version(request, body.patch.expected_revision)
    snapshot = bundle(auth, body.bundle_id)
    return write(auth, request, "facts.patch", body.model_dump(mode="json"),
                 lambda: facts.apply_fact_patch(auth.uow, auth.actor, body.patch, snapshot))


@router.post("/objects/{id}/deactivate", response_model=WriteReceipt, operation_id="deactivateObject")
def deactivate(id: UUID, body: DeactivateObjectRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    facts.get_object_row(auth.uow, auth.actor, id, "edit")
    expected = version(request)
    return write(auth, request, "object.deactivate",
        {"resource_id": str(id), "expected_revision": expected, **body.model_dump(mode="json")},
        lambda: facts.deactivate_object(auth.uow, auth.actor, id, expected, body))


@router.get("/objects/{id}/form-schema", response_model=FormSchemaView, operation_id="getObjectFormSchema")
def object_form(id: UUID, class_iri: str | None = None, auth: AuthRequest = Depends(authenticated)):
    row = facts.get_object_row(setup(auth), auth.actor, id)
    release_id = auth.uow.session.execute(select(catalog.c.active_release_id)).scalar_one()
    target = class_iri or row["payload"]["class_iris"][0]
    if target not in row["payload"]["class_iris"]:
        raise ApiError("OBJECT_CLASS_REQUIRED", 422, "Requested class is not an object class")
    return get_released_form_schema(auth.uow, auth.actor, release_id, target)


@router.get("/objects/{id}/assertions", response_model=Page[AssertionView], operation_id="listObjectAssertions")
def object_assertions(id: UUID, cursor: UUID | None = None, auth: AuthRequest = Depends(authenticated)):
    facts.get_object_row(setup(auth), auth.actor, id)
    rows = [r for r in facts.current_assertions(auth.uow, id) if cursor is None or r["id"].int > cursor.int]
    size = auth.service.settings.list_page_size
    return Page([facts.assertion_view(auth.uow, r) for r in rows[:size]],
                str(rows[size-1]["id"]) if len(rows) > size else None)


@router.get("/links", response_model=Page[LinkView], operation_id="listLinks")
def list_links(cursor: UUID | None = None, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    facts.validate_actor(auth.uow, auth.actor)
    links = []
    for row in auth.uow.session.execute(facts.visible_objects(auth.actor)).mappings():
        if row["payload"]["state"] == "inactive":
            continue
        for assertion in facts.current_assertions(auth.uow, row["id"]):
            value = assertion["payload"]
            if (value["state"] != "accepted" or value["object"]["kind"] != "iri"
                    or not facts.is_effective(value, facts.evaluation_time(auth.uow))):
                continue
            target = auth.uow.session.execute(select(facts.objects.c.id).where(
                facts.objects.c.iri == value["object"]["value"])).scalar_one_or_none()
            if target is not None and (cursor is None or assertion["id"].int > cursor.int):
                links.append(LinkView(id=assertion["id"], source_id=row["id"], predicate_iri=value["predicate_iri"],
                    target_id=target, assertion=facts.assertion_view(auth.uow, assertion)))
    links.sort(key=lambda x: x.id.int)
    size = auth.service.settings.list_page_size
    return Page(links[:size], str(links[size-1].id) if len(links) > size else None)


@router.post("/links", response_model=WriteReceipt, status_code=201, operation_id="createLink")
def create_link(body: CreateLinkRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    facts.get_object_row(auth.uow, auth.actor, body.source_id, "edit")
    facts.confirmed_types(auth.uow, auth.actor, body.target_id)
    expected = version(request)
    assertion = AssertionInput(assertion_id=body.assertion_id, resource_id=body.source_id,
        predicate_iri=body.predicate_iri, object=facts.RdfTerm("iri", facts.object_iri(body.target_id)),
        state=body.state, data_nature=body.data_nature, valid_from=body.valid_from,
        valid_to=body.valid_to, support=body.support)
    patch = FactPatch(resource_id=body.source_id, expected_revision=expected,
                      additions=(assertion,), decision_ref=body.decision_ref)
    snapshot = bundle(auth, body.bundle_id)
    return write(auth, request, "link.create", {"expected_revision": expected, **body.model_dump(mode="json")},
                 lambda: facts.apply_fact_patch(auth.uow, auth.actor, patch, snapshot))


@router.post("/links/{id}/withdraw", response_model=WriteReceipt, operation_id="withdrawLink")
def withdraw_link(id: UUID, body: WithdrawLinkRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    row = auth.uow.session.execute(select(facts.assertions).where(facts.assertions.c.id == id)).mappings().first()
    if row is None or row["payload"]["object"]["kind"] != "iri":
        raise ApiError("NOT_FOUND", 404, "Link not found")
    facts.get_object_row(auth.uow, auth.actor, row["resource_id"], "edit")
    owned = set(auth.uow.session.execute(select(facts.supports.c.id).where(facts.supports.c.assertion_id == id)).scalars())
    if not set(body.support_ids) <= owned:
        raise ApiError("SUPPORT_NOT_FOUND", 404, "Support not found")
    expected = version(request)
    patch = FactPatch(resource_id=row["resource_id"], expected_revision=expected,
                      withdraw_support_ids=body.support_ids, decision_ref=body.decision_ref)
    snapshot = bundle(auth, body.bundle_id)
    return write(auth, request, "link.withdraw", {"id": str(id), "expected_revision": expected,
        **body.model_dump(mode="json")}, lambda: facts.apply_fact_patch(auth.uow, auth.actor, patch, snapshot))


@router.post("/validation", response_model=ValidationReport, operation_id="validateFacts")
def validate(body: ValidateFactsRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    require_idempotency_key(request.headers.get("Idempotency-Key"))
    snapshot = bundle(auth, body.bundle_id)
    return (facts.validate_object(auth.actor, body.create, snapshot) if body.create is not None else
            facts.validate_facts(auth.actor, body.facts, snapshot))


@router.get("/geometry", response_model=GeoFeatureCollection, operation_id="listGeometry")
def geometry_list(bbox: str | None = None, scope_id: UUID | None = None, cursor: str | None = None,
                  auth: AuthRequest = Depends(authenticated)):
    return geometry.list_geometry(setup(auth), auth.actor, bbox=bbox, scope_id=scope_id, cursor=cursor)


@router.get("/geometry/{object_id}", response_model=GeoFeatureCollection, operation_id="getObjectGeometry")
def geometry_get(object_id: UUID, auth: AuthRequest = Depends(authenticated)):
    return geometry.list_geometry(setup(auth), auth.actor, identifier=object_id)


@router.put("/geometry/{object_id}", response_model=RevisionRef, operation_id="updateObjectGeometry")
def geometry_put(object_id: UUID, body: GeometryWriteRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    facts.get_object_row(auth.uow, auth.actor, object_id, "edit")
    if object_id != body.geometry.resource_id:
        raise ApiError("SUBJECT_MISMATCH", 422, "Path and geometry subjects must agree")
    version(request, body.geometry.expected_revision)
    snapshot = bundle(auth, body.bundle_id)
    return write(auth, request, "geometry.write", body.model_dump(mode="json"),
                 lambda: geometry.write_geometry(auth.uow, auth.actor, body.geometry, snapshot))
