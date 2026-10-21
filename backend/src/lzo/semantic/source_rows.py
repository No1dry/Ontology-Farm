"""S03-owned finite row composition; one validation/persistence/outbox owner.

The private context only substitutes the complete exact effect and prospective
geometry for its enumerated create/patch. It never substitutes authorization,
permits another resource/head, or calls ingestion persistence from S03.
"""
import json
from uuid import UUID
from sqlalchemy import select, insert, update, text
from lzo.errors import ApiError, Conflict
from lzo.auth.permissions import permission_context
from lzo.semantic import facts
from lzo.semantic.geometry import normalize
from lzo.semantic.models import geometry_supports
from lzo.semantic.schemas import SourceRowInput, GeometryInput


def eligible_geometry(uow,entry):
    candidate=entry['payload']
    if not facts.is_effective(candidate,facts.evaluation_time(uow)):return False
    ref=candidate.get('decision_ref')
    if not ref or facts._support_currentness_port is None:return False
    from lzo.contracts import RevisionRef
    result=facts._support_currentness_port.currentness(uow,
        RevisionRef(UUID(ref['resource_id']),ref['revision'],UUID(ref['event_id'])),entry['id'])
    return result is not None and result.eligible


def selected_geometry(uow,identifier):
    payload=uow.session.execute(text("SELECT payload FROM semantic_geometry WHERE resource_id=:id AND active"),{"id":identifier}).scalar_one_or_none()
    if payload:
        support=payload["source_support"]
        if not support.get("source_id") and not support.get("mapping_version_id"):return payload
    entries=uow.session.execute(select(geometry_supports).where(geometry_supports.c.resource_id==identifier,
        geometry_supports.c.active.is_(True))).mappings().all()
    # Accepted support history is authoritative; the spatial index can be empty
    # before a future support starts or still represent an expired interval.
    for entry in sorted(entries,key=lambda e:(e['revision_ref']['revision'],str(e['id'])),reverse=True):
        if eligible_geometry(uow,entry):
            revision=payload['revision'] if payload else uow.session.execute(select(facts.objects.c.revision).where(
                facts.objects.c.id==identifier)).scalar_one()
            return {**entry['payload'],'revision':revision}
    return None


def complete_effect(uow,effect):
    context=getattr(uow,"_semantic_source_row",None)
    if context is None:return effect
    expected=context["inner"]
    if effect!=expected:raise Conflict("SOURCE_ROW_EFFECT_MISMATCH","Effect differs from finite source row")
    return context["effect"]


def withdrawal_only(row):
    """Only exact support removals may retain an incomplete final quality state."""
    return bool(row.patch and (row.patch.withdraw_support_ids or row.withdraw_geometry_support_ids)
        and not row.patch.additions and row.geometry is None)


def _prepare(uow,actor,row,bundle):
    command=row.create or row.patch;identifier=command.resource_id
    expected=0 if row.create else row.patch.expected_revision
    current=selected_geometry(uow,identifier)
    withdrawal=[]
    for sid in row.withdraw_geometry_support_ids:
        entry=uow.session.execute(select(geometry_supports).where(geometry_supports.c.id==sid,
            geometry_supports.c.resource_id==identifier,geometry_supports.c.active.is_(True))).mappings().first()
        if entry is None:raise ApiError("GEOMETRY_SUPPORT_NOT_FOUND",404,"Geometry support not found")
        withdrawal.append(entry)
    replacement=None
    if row.geometry:
        geometry=GeometryInput(resource_id=identifier,expected_revision=max(expected,1),geojson=row.geometry.geojson,
            source_crs=row.geometry.source_crs,source_support=row.geometry.source_support,decision_ref=row.decision_ref)
        replacement={**geometry.model_dump(mode="json"),**normalize(uow,geometry),
            "valid_from":row.geometry.valid_from.isoformat(),"valid_to":row.geometry.valid_to.isoformat() if row.geometry.valid_to else None,
            "revision":expected+1,"data_nature":row.create.data_nature if row.create else
                facts.get_object_row(uow,actor,identifier,"edit")["payload"]["data_nature"]}
    selection=current
    manual_current=(current and not current['source_support'].get('source_id')
        and not current['source_support'].get('mapping_version_id'))
    if replacement and facts.is_effective(replacement,facts.evaluation_time(uow)):
        # Equal source evidence adds its own durable support without erasing the
        # independent selected manual payload. Different geometry still follows
        # the ordinary conflict/decision and replacement path.
        if not (manual_current and current['normalized']==replacement['normalized']):selection=replacement
    elif withdrawal:
        remaining=uow.session.execute(select(geometry_supports).where(geometry_supports.c.resource_id==identifier,
            geometry_supports.c.active.is_(True),geometry_supports.c.id.not_in(row.withdraw_geometry_support_ids))).mappings()
        selection=next((e["payload"] for e in remaining if eligible_geometry(uow,e) and
            current and e["payload"]["normalized"]==current["normalized"]),None)
        if manual_current:selection=current
    return {"row":row,"identifier":identifier,"expected":expected,"replacement":replacement,"selection":selection,"withdrawal":withdrawal}


def validate_source_row(uow,actor,row,bundle):
    """Prospective final state without inserting a semantic object or support."""
    with permission_context(uow):
        facts.fact_validation.begin(uow)
        document=facts.effective_bundle(uow,actor,bundle)
        context=_prepare(uow,actor,row,bundle)
        command=row.create or row.patch
        additions=command.assertions if row.create else command.additions
        if row.create:
            classes=row.create.class_iris;combined=additions
        else:
            target=facts.get_object_row(uow,actor,command.resource_id,"edit")
            if target["revision"]!=row.patch.expected_revision:raise Conflict("REVISION_CONFLICT","Object changed")
            classes=facts.current_classes(uow,actor,command.resource_id)
            combined=(*[a for a in facts.assertion_inputs(facts.current_assertions(uow,command.resource_id))
                if a.support.support_id not in row.patch.withdraw_support_ids],*additions)
        uow._semantic_source_row=context
        try:
            return facts.check_graph(uow,actor,command.resource_id,classes,
                facts.effective_inputs(combined,facts.evaluation_time(uow)),document,bundle,
                changed=additions,changed_predicates={a.predicate_iri for a in additions}|
                    ({"urn:lzo:geometry"} if row.geometry or row.withdraw_geometry_support_ids else set()),
                permit_incomplete=withdrawal_only(row))[0]
        finally:del uow._semantic_source_row


def apply_source_row(uow,actor,row,bundle):
    with permission_context(uow):
        if getattr(uow,"_semantic_source_row",None) is not None:raise Conflict("SOURCE_ROW_NESTED","Nested source rows are forbidden")
        facts.effective_bundle(uow,actor,bundle)
        context=_prepare(uow,actor,row,bundle)
        command=(row.create or row.patch).model_copy(update={"decision_ref":row.decision_ref})
        kind="object.create" if row.create else "facts.patch";key="create" if row.create else "patch"
        context.update(effect={"operation":"source.row","row":row.model_dump(mode="json")},
            inner={"operation":kind,key:command.model_dump(mode="json")})
        uow._semantic_source_row=context
        try:
            receipt=(facts.register_object(uow,actor,command,bundle) if row.create else facts.apply_fact_patch(uow,actor,command,bundle))
            if context["selection"] is not None:
                payload={**context["selection"],"revision":receipt.revision.revision}
                uow.session.execute(text("""INSERT INTO semantic_geometry(resource_id,revision,event_id,geom,payload)
                    VALUES (:id,:revision,:event,public.ST_SetSRID(public.ST_GeomFromGeoJSON(:geo),4326),CAST(:payload AS jsonb))
                    ON CONFLICT(resource_id) DO UPDATE SET revision=excluded.revision,event_id=excluded.event_id,geom=excluded.geom,payload=excluded.payload,active=true"""),
                    {"id":context["identifier"],"revision":receipt.revision.revision,"event":receipt.revision.event_id,
                        "geo":json.dumps(payload["normalized"]),"payload":json.dumps(payload)})
            else:
                # Empty current selection is a projection index, not deletion of
                # historical geometry or an ontology entity.
                uow.session.execute(text("UPDATE semantic_geometry SET active=false WHERE resource_id=:id"),{"id":context["identifier"]})
            if context["replacement"]:
                uow.session.execute(insert(geometry_supports).values(id=row.geometry.source_support.support_id,
                    resource_id=context["identifier"],payload=context["replacement"],active=True,revision_ref=facts.json_value(receipt.revision)))
            uow.session.execute(update(geometry_supports).where(geometry_supports.c.id.in_(row.withdraw_geometry_support_ids)).values(
                active=False,withdrawn_event_id=receipt.revision.event_id))
            return receipt
        finally:del uow._semantic_source_row
