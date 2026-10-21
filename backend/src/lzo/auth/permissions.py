"""SQL authorization over complete grants; metadata relationships confer no rights."""
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy import and_, exists, or_, select, cast, String, func, case
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from lzo.auth.models import authorization_state, grants, group_members, resource_scopes, roles, users
from lzo.contracts import ActorContext, ResourceScope, UnitOfWork
from lzo.errors import Forbidden

_current_uow: ContextVar[UnitOfWork] = ContextVar("authorization_uow")
_generated_read_policy = None


def register_generated_read_policy(policy):
    """Trusted startup seam; classification never depends on registration."""
    global _generated_read_policy
    _generated_read_policy = policy


@contextmanager
def permission_context(uow: UnitOfWork):
    token = _current_uow.set(uow)
    try:
        yield
    finally:
        _current_uow.reset(token)


def current_version(uow: UnitOfWork, *, lock=False) -> int:
    statement = select(authorization_state.c.version).where(authorization_state.c.singleton == 1)
    # Readers hold a share lock through commit. Revocations cannot overtake a
    # checked write; a queued write after revocation must observe the new version.
    if lock:
        statement = statement.with_for_update(read=True)
    return uow.session.execute(statement).scalar_one()


def validate_actor(uow: UnitOfWork, actor: ActorContext) -> None:
    if current_version(uow, lock=True) != actor.authorization_version:
        raise Forbidden("AUTHORIZATION_CHANGED", "Authorization changed; reload the session")
    enabled = uow.session.execute(select(users.c.enabled).where(users.c.id == actor.subject_id)).scalar_one_or_none()
    if enabled is not True:
        raise Forbidden("FORBIDDEN", "Operation forbidden")


def principal_predicate(actor):
    return or_(grants.c.user_id == actor.subject_id,
        grants.c.group_id.in_(select(group_members.c.group_id).where(group_members.c.user_id == actor.subject_id)))


def grant_predicate(actor, action):
    return and_(grants.c.active.is_(True), principal_predicate(actor), roles.c.actions.contains([action]))


def ordinary_grant_statement(actor: ActorContext, action: str):
    """Complete ordinary grant leaves, with independent aliases for policy SQL."""
    scope = resource_scopes.alias()
    permitted = exists(select(grants.c.id).select_from(grants.join(roles)).where(
        grant_predicate(actor, action), grants.c.security_class == scope.c.security_class,
        or_(grants.c.global_scope.is_(True), grants.c.resource_id == scope.c.resource_id,
            grants.c.scope_id == scope.c.scope_id)))
    return select(scope.c.resource_id).where(permitted)


def generated_resource_ids():
    """Reserved identities are excluded even if their producer is unavailable."""
    from lzo.artifacts.models import artifact_versions
    from lzo.worker.models import jobs
    owner = resource_scopes.alias()
    return (select(owner.c.resource_id).where(owner.c.resource_kind == "semantic.export")
        .union(select(artifact_versions.c.id).join(owner, owner.c.resource_id == artifact_versions.c.resource_id)
               .where(owner.c.resource_kind == "semantic.export"),
               select(jobs.c.id).where(jobs.c.kind == "semantic.export")))


def _base_authorized_statement(actor: ActorContext, action: str):
    """Compose a resource filter; this function alone does not validate the actor.

    Call validate_actor(uow, actor) before executing the composed statement in
    that same UoW, without an intervening commit/rollback. Its shared version
    lock keeps enabled state and grants valid until the transaction ends.
    require_permission and authorized_resource_ids perform this check themselves.
    """
    ordinary = and_(resource_scopes.c.resource_id.in_(ordinary_grant_statement(actor, action)),
                    resource_scopes.c.resource_id.not_in(generated_resource_ids()))
    if action != "view" or _generated_read_policy is None:
        return select(resource_scopes.c.resource_id).where(ordinary)
    return select(resource_scopes.c.resource_id).where(or_(ordinary,
        and_(resource_scopes.c.resource_id.in_(generated_resource_ids()),
             resource_scopes.c.resource_id.in_(_generated_read_policy(actor)))))


def authorized_statement(actor: ActorContext, action: str):
    """Evidence uses fixed actions and ALL transitive reference visibility.

    Classification comes from the registered owner/version relationship, never
    caller JSON. The recursive UNION terminates even for cyclic history links.
    """
    if action != "view":
        return _base_authorized_statement(actor, action)
    from lzo.artifacts.models import artifact_versions
    from lzo.governance.models import revisions
    from lzo.governance.history import revision_visibility_dependencies as deps
    owner, version = resource_scopes.alias(), artifact_versions.alias()
    classified = select(owner.c.resource_id.label("id"), owner.c.resource_id.label("owner_id")).where(
        owner.c.resource_kind == "evidence").union_all(
        select(version.c.id, version.c.resource_id).join(owner, owner.c.resource_id == version.c.resource_id)
        .where(owner.c.resource_kind == "evidence")).cte()
    base = _base_authorized_statement(actor, "view").cte()
    readable = ordinary_grant_statement(actor, "evidence.read").cte()
    pins = func.jsonb_array_elements(case(
        (func.jsonb_typeof(version.c.metadata["evidence"]["target_refs"]) == "array",
            version.c.metadata["evidence"]["target_refs"]), else_=cast([],JSONB))).table_valued("value").alias()
    pin_value = cast(pins.c.value,JSONB)
    source_pin = version.c.metadata["source_revision"]
    historical = revisions.alias()
    def real_pin(value):
        return exists(select(historical.c.event_id).where(
            cast(historical.c.resource_id,String)==value["resource_id"].astext,
            cast(historical.c.revision,String)==value["revision"].astext,
            cast(historical.c.event_id,String)==value["event_id"].astext))
    def bound_reference(value):
        event = revisions.alias()
        return exists(select(deps.c.resource_id).join(event,event.c.event_id==deps.c.event_id).where(
            event.c.resource_id==version.c.id, event.c.event_type=="artifact.uploaded",
            cast(deps.c.resource_id,String)==value))
    valid = select(version.c.id, version.c.resource_id).where(
        version.c.metadata["evidence_policy"].astext == "evidence-v1",
        version.c.metadata["high_risk"].astext == "true",
        func.jsonb_typeof(version.c.metadata["evidence"]) == "object",
        version.c.metadata["evidence"].has_all(cast(
            ["source_id","scope","target_refs","title","locator","data_nature","replaces_id"], ARRAY(String))),
        func.jsonb_typeof(version.c.metadata["evidence"]["target_refs"]) == "array",
        func.jsonb_typeof(version.c.metadata["evidence"]["title"]) == "string",
        version.c.metadata["evidence"]["scope"]["resource_id"].astext == cast(version.c.resource_id, String),
        version.c.metadata["evidence"]["data_nature"].astext.in_(("synthetic","test","imported_unverified","verified")),
        ~exists(select(pins.c.value).where(~real_pin(pin_value) |
            ~bound_reference(pin_value["resource_id"].astext))),
        or_(and_(version.c.metadata["evidence"]["source_id"].astext.is_(None),
                 version.c.metadata["evidence"]["data_nature"].astext=="imported_unverified",
                 func.jsonb_typeof(source_pin)=="null"),
            and_(version.c.metadata["evidence"]["source_id"].astext==source_pin["resource_id"].astext,
                 real_pin(source_pin),bound_reference(source_pin["resource_id"].astext)))).cte()
    eligible = select(base.c.resource_id).where(or_(
        base.c.resource_id.not_in(select(classified.c.id)),
        base.c.resource_id.in_(select(classified.c.id).where(
            classified.c.owner_id.in_(select(valid.c.resource_id)),
            or_(classified.c.id == classified.c.owner_id, classified.c.id.in_(select(valid.c.id))),
            classified.c.owner_id.in_(select(readable.c.resource_id)),
            classified.c.owner_id.in_(select(base.c.resource_id)))))).cte()
    # Selected generated footprints must obey the current evidence leaf policy.
    # Do not traverse arbitrary old object history into an export's selected
    # footprint. Actual job dependencies remain authoritative if jobs evolve.
    from lzo.semantic.models import export_dependencies, export_outputs
    from lzo.worker.models import job_dependencies
    protected = select(classified.c.id).union(generated_resource_ids()).cte()
    edges = select(revisions.c.resource_id.label("root"), deps.c.resource_id.label("dependency")).join(
        deps, deps.c.event_id == revisions.c.event_id).where(or_(
            revisions.c.resource_id.in_(select(classified.c.id)),
            revisions.c.event_type == "artifact.uploaded")).union(
        select(export_dependencies.c.export_id, export_dependencies.c.resource_id),
        select(export_outputs.c.version_id, export_outputs.c.export_id),
        select(job_dependencies.c.job_id, job_dependencies.c.resource_id)).cte()
    closure = select(protected.c.id.label("root"), protected.c.id.label("dependency")).cte(recursive=True)
    closure = closure.union(select(closure.c.root, edges.c.dependency).join(
        edges, edges.c.root == closure.c.dependency))
    hidden = select(closure.c.root).where(closure.c.dependency.not_in(select(eligible.c.resource_id)))
    return select(resource_scopes.c.resource_id).where(
        resource_scopes.c.resource_id.in_(select(eligible.c.resource_id)),
        resource_scopes.c.resource_id.not_in(hidden))


def require_permission(actor: ActorContext, action: str, resource: ResourceScope, *, uow: UnitOfWork | None = None) -> None:
    transaction = uow or _current_uow.get()
    validate_actor(transaction, actor)
    if resource.resource_id is not None:
        statement = authorized_statement(actor, action).where(
            resource_scopes.c.resource_id == resource.resource_id,
            resource_scopes.c.security_class == resource.security_class)
    else:
        # Non-object operations require an explicit global grant of that class.
        statement = select(grants.c.id).select_from(grants.join(roles)).where(
            grant_predicate(actor, action), grants.c.global_scope.is_(True),
            grants.c.security_class == resource.security_class)
    if transaction.session.execute(statement.limit(1)).first() is None:
        raise Forbidden("FORBIDDEN", "Operation forbidden")


def authorized_resource_ids(actor: ActorContext, action: str, *, uow: UnitOfWork | None = None) -> Iterator[UUID]:
    transaction = uow or _current_uow.get()
    validate_actor(transaction, actor)
    result = transaction.session.execute(authorized_statement(actor, action).order_by(
        resource_scopes.c.resource_id).execution_options(yield_per=200))
    yield from result.scalars()
