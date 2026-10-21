"""Built-in semantic delivery. Domain resolvers register separately at startup."""
from uuid import UUID
from sqlalchemy import select

from lzo.contracts import RevisionRef
from lzo.errors import ApiError
from lzo.semantic.fuseki import FusekiRepository
from lzo.semantic.models import projection_events
from lzo.semantic.projection import ProjectionService, _revision, producers
from lzo.worker import register_handler


def _ref(payload):
    if set(payload) != {"resource_id", "revision", "event_id", "reference_ids"}:
        raise ApiError("JOB_POLICY_CHANGED", 409, "Invalid projection job descriptor")
    if type(payload["revision"]) is not int or payload["revision"] < 1:
        raise ApiError("JOB_POLICY_CHANGED", 409, "Invalid projection revision")
    return RevisionRef(UUID(payload["resource_id"]), payload["revision"], UUID(payload["event_id"]))


def _references(payload):
    ref = _ref(payload)
    refs = tuple(UUID(value) for value in payload["reference_ids"])
    if not refs or refs[0] != ref.resource_id or len(set(refs)) != len(refs):
        raise ApiError("JOB_POLICY_CHANGED", 409, "Invalid projection reference scope")
    return refs


def _action(uow, payload):
    ref = _ref(payload)
    row = _revision(uow, ref)
    pending = uow.session.execute(select(projection_events).where(
        projection_events.c.event_id == ref.event_id)).mappings().first()
    producer = producers.get(row["event_type"])
    if (pending is None or producer is None or pending["producer_version"] != producer.version
            or pending["reference_ids"] != payload["reference_ids"]):
        raise ApiError("JOB_POLICY_CHANGED", 409, "Projection registration changed")
    return producer.action


def _project(ctx):
    with ctx.batch():
        ctx.uow.ontology_settings = ctx.uow.job_settings = ctx.runner.settings
        service = ProjectionService(ctx.uow, FusekiRepository(ctx.runner.settings.fuseki_url),
                                    actor=ctx.actor)
        ref = _ref(ctx.payload)
        receipt = service.project_revision(ref)
        if receipt.state != "failed":
            row = _revision(ctx.uow, ref)
            resolver = producers[row["event_type"]].result_resolver
            if resolver is not None:
                result = resolver(ctx.uow, ctx.actor, row, receipt)
                if result is not None:
                    ctx.set_result(result)
            ctx.checkpoint({"projection_state": receipt.state, "revision": receipt.revision,
                            "digest": receipt.digest})
    # Persist the failed receipt in the batch, then fail the attempt. Throwing
    # inside the batch would erase the failure status together with its effects.
    if receipt.state == "failed":
        raise RuntimeError("RDF projection failed")


register_handler("rdf.project", _project, references=_references, action=_action)

# Trusted producers must be identical in the API and standalone worker.
from lzo.ontology import releases as ontology_releases
from lzo.ontology import bootstrap as ontology_bootstrap
from lzo.semantic import facts as semantic_facts
from lzo.semantic import inference as semantic_inference
from lzo.ingestion import rules as ingestion_rules
from lzo.ingestion import rulings as ingestion_rulings
from lzo.ingestion import scheduler as ingestion_scheduler
from lzo.ingestion import worker as ingestion_worker
from lzo.ingestion import preview_jobs as ingestion_preview_jobs
from lzo.semantic.facts import register_decision_port, register_authority_port, register_support_currentness_port
register_decision_port(ingestion_rulings.RulingDecisionPort())
register_authority_port(ingestion_rules.RuleAuthorityPort())
register_support_currentness_port(ingestion_rulings.RulingService())
from lzo.semantic.merge import register_plan_reader
from lzo.ingestion.identity import MergePlanReader
register_plan_reader(MergePlanReader())
from lzo.semantic import inference_jobs as semantic_inference_jobs
from lzo.semantic import exports as semantic_exports
