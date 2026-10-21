"""Selected semantic exports through the existing F05 queue and private F04 store.

Immutable bindings supply F03's sole generated-read policy. They are producer
evidence, never caller metadata or an alternative grant system.
"""
import hashlib
import io
import json
import zipfile
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from rdflib import Dataset, URIRef
from sqlalchemy import select, insert, exists, and_, cast, String

from lzo.artifacts.models import artifact_versions
from lzo.auth.models import resource_scopes
from lzo.auth.permissions import (authorized_statement, generated_resource_ids, ordinary_grant_statement,
    register_generated_read_policy, require_permission, validate_actor)
from lzo.contracts import ResourceScope
from lzo.errors import ApiError
from lzo.governance.models import revisions
from lzo.governance.revisions import append_revision
from lzo.ontology.drafts import bind_history_dependencies, settings_for, store_for
from lzo.semantic.authorized_dataset import build_authorized_dataset, revalidate, logical_graph_iri, ROLES
from lzo.semantic.files import export_jsonld, nquads
from lzo.semantic.models import export_bindings, export_dependencies, export_outputs
from lzo.semantic.rdf import Triple, term_from_node
from lzo.semantic.schemas import Model
from lzo.worker.models import jobs, job_dependencies
from lzo.worker.service import enqueue, register_handler, register_result_kind, JobResultRef

POLICY = "semantic-export-v1"


class ExportRequest(Model):
    resource_ids: tuple[UUID, ...] = Field(min_length=1, max_length=10000)
    format: Literal["turtle", "json-ld"]
    layout: Literal["single-graph", "dataset"]
    graph_role: Literal["schema", "accepted", "candidate", "derived", "provenance"] | None = None
    include_provenance: bool = False
    include_inferred: bool = False

    @model_validator(mode="after")
    def layer(self):
        if self.layout == "single-graph" and self.graph_role is None:
            raise ValueError("Single graph export needs an explicit role")
        if self.layout == "dataset" and self.graph_role is not None:
            raise ValueError("Dataset export does not select one role")
        if self.graph_role == "provenance" and not self.include_provenance:
            raise ValueError("Provenance role requires include_provenance")
        if self.graph_role == "derived" and not self.include_inferred:
            raise ValueError("Derived role requires include_inferred")
        if len(set(self.resource_ids)) != len(self.resource_ids):
            raise ValueError("Duplicate export root")
        return self


def generated_read_statement(actor):
    """Bounded SQL layers: ordinary leaves -> owner/version -> actual job deps."""
    ordinary = ordinary_grant_statement(actor, "view").subquery()
    candidate = ordinary_grant_statement(actor, "candidate.read").subquery()
    b, d, c, a = export_bindings.alias(), export_dependencies.alias(), export_outputs.alias(), artifact_versions.alias()
    valid_output = exists(select(a.c.id).where(a.c.id == c.c.version_id,
        a.c.resource_id == c.c.export_id, a.c.sha256 == c.c.sha256,
        a.c.version_number == 1, a.c.previous_version_id.is_(None)))
    owners = select(b.c.id).where(b.c.policy_version == POLICY, b.c.state == "prepared",
        exists(select(d.c.resource_id).where(d.c.export_id == b.c.id, d.c.export_root.is_(True))),
        ~exists(select(d.c.resource_id).where(d.c.export_id == b.c.id,
            d.c.resource_id.not_in(select(ordinary.c.resource_id)))),
        ~exists(select(d.c.resource_id).where(d.c.export_id == b.c.id, d.c.candidate.is_(True),
            d.c.resource_id.not_in(select(candidate.c.resource_id)))),
        ~exists(select(c.c.export_id).where(c.c.export_id == b.c.id,
            ~valid_output))).cte()
    versions = select(a.c.id).join(c, c.c.version_id == a.c.id).where(
        c.c.export_id.in_(select(owners.c.id)), a.c.resource_id == c.c.export_id,
        a.c.sha256 == c.c.sha256, c.c.state == "completed", a.c.version_number == 1,
        a.c.previous_version_id.is_(None)).cte()
    leaves = select(ordinary.c.resource_id).where(ordinary.c.resource_id.not_in(generated_resource_ids()))
    nonjobs = leaves.union(select(owners.c.id), select(versions.c.id)).cte()
    j, jd, jb = jobs.alias(), job_dependencies.alias(), export_bindings.alias()
    export_jobs = select(j.c.id).join(jb, cast(jb.c.id, String) == j.c.payload["export_id"].astext).where(
        j.c.kind == "semantic.export", j.c.initiator_id == jb.c.initiator_id,
        jb.c.id.in_(select(owners.c.id)),
        exists(select(jd.c.resource_id).where(jd.c.job_id == j.c.id)),
        ~exists(select(jd.c.resource_id).where(jd.c.job_id == j.c.id,
            jd.c.resource_id.not_in(select(nonjobs.c.resource_id)))))
    return select(owners.c.id).union(select(versions.c.id), export_jobs)


def _scope(uow, identifier):
    row = uow.session.execute(select(resource_scopes).where(resource_scopes.c.resource_id == identifier)).mappings().one()
    return ResourceScope(identifier, row["owner_scope_id"], tuple(UUID(v) for v in row["related_scope_ids"]), row["security_class"])


def _selected(actor, request):
    from lzo.auth.permissions import _current_uow
    uow = _current_uow.get()
    review = bool(uow.session.execute(authorized_statement(actor, "candidate.read")).first())
    if request.graph_role == "candidate" and not review:
        raise ApiError("FORBIDDEN", 403, "Candidate review permission is required")
    snapshot = build_authorized_dataset(actor, "review" if review else "current")
    roots = set(request.resource_ids) & (set(snapshot.object_rows) | {snapshot.bundle_id})
    from lzo.ontology.releases import current_execution_bundle
    release = current_execution_bundle(uow, actor).schema_release_id
    roots |= set(request.resource_ids) & {release}
    if not roots:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    for identifier in roots:
        require_permission(actor, "export", _scope(uow, identifier), uow=uow)
    roles = {request.graph_role} if request.layout == "single-graph" else set(ROLES)
    if not request.include_provenance:
        roles.discard("provenance")
    if not request.include_inferred:
        roles.discard("derived")
    data, references, candidates = Dataset(default_union=False), set(roots) | {snapshot.bundle_id, release}, set()
    subjects = {row["iri"] for key,row in snapshot.object_rows.items() if key in roots}
    assertion_subjects = {f"urn:lzo:assertion:{a.id}" for a in snapshot.assertions.values() if a.resource_id in roots}
    for role in sorted(roles):
        graph = snapshot.dataset.graph(URIRef(logical_graph_iri(role)))
        for s,p,o in graph:
            if role != "schema" and str(s) not in subjects | (assertion_subjects if role == "provenance" else set()):
                continue
            triple = Triple(str(s), str(p), term_from_node(o))
            data.graph(URIRef(logical_graph_iri(role))).add((s,p,o))
            references.update(snapshot.triple_references[(role,triple)])
            if role == "candidate":
                candidates.update(key for key,row in snapshot.object_rows.items() if row["iri"] == str(s))
            elif role == "provenance":
                candidates.update(a.resource_id for a in snapshot.assertions.values()
                    if a.state == "candidate" and str(s) == f"urn:lzo:assertion:{a.id}")
    # Preserve ordinary artifact snapshot dependencies beyond its direct scope.
    from lzo.governance.history import revision_visibility_dependencies
    pending = set(references)
    while pending:
        found = set(uow.session.execute(select(revision_visibility_dependencies.c.resource_id).join(
            revisions, revisions.c.event_id == revision_visibility_dependencies.c.event_id).where(
                revisions.c.resource_id.in_(pending), revisions.c.event_type == "artifact.uploaded")).scalars())
        pending = found - references
        references.update(found)
    digest = hashlib.sha256((str(snapshot.bundle_id)+"\n"+str(snapshot.input_watermark)+"\n"+
                            "\n".join(sorted(nquads(data).splitlines()))).encode()).hexdigest()
    revalidate(uow, actor, snapshot)
    return snapshot, data, roots, references, candidates, digest


def _binding(uow, identifier):
    value = uow.session.execute(select(export_bindings).where(export_bindings.c.id == identifier,
        export_bindings.c.policy_version == POLICY)).mappings().first()
    if value is None:
        raise ApiError("EXPORT_BINDING_INVALID", 409, "Export binding is unavailable")
    return value


def create_binding(uow, actor, identifier, request, snapshot, roots, references, candidates, digest):
    """Trusted selected footprint; seal once, never inherit an original head."""
    validate_actor(uow, actor)
    if not roots or not roots <= references or not candidates <= roots or identifier in references:
        raise ApiError("EXPORT_BINDING_INVALID", 409, "Invalid selected export footprint")
    forbidden = set(uow.session.execute(select(resource_scopes.c.resource_id).where(
        resource_scopes.c.resource_id.in_(references), resource_scopes.c.resource_kind.in_(("job","semantic.export")))).scalars())
    forbidden.update(uow.session.execute(generated_resource_ids()).scalars())
    if references & forbidden:
        raise ApiError("EXPORT_BINDING_INVALID", 409, "Export dependencies must be ordinary protected resources")
    for ref in references:
        require_permission(actor, "view", _scope(uow, ref), uow=uow)
    for ref in roots:
        require_permission(actor, "export", _scope(uow, ref), uow=uow)
    for ref in candidates:
        require_permission(actor, "candidate.read", _scope(uow, ref), uow=uow)
    uow.session.execute(insert(resource_scopes).values(resource_id=identifier,
        security_class="data", resource_kind="semantic.export"))
    uow.session.execute(insert(export_dependencies), [{"export_id":identifier,"resource_id":ref,
        "export_root":ref in roots,"candidate":ref in candidates} for ref in sorted(references,key=str)])
    uow.session.execute(insert(export_bindings).values(id=identifier,initiator_id=actor.subject_id,
        policy_version=POLICY,state="prepared",request=request.model_dump(mode="json"),bundle_id=snapshot.bundle_id,
        input_watermark=snapshot.input_watermark,input_digest=digest))
    event=append_revision(uow,actor,identifier,0,{"request":request.model_dump(mode="json"),
        "scope":"authorized_subset","bundle_id":str(snapshot.bundle_id),"input_watermark":snapshot.input_watermark},"semantic.export.prepared")
    bind_history_dependencies(uow,event.event_id,references)


def start_export(uow, actor, request: ExportRequest):
    snapshot,data,roots,references,candidates,digest = _selected(actor,request)
    selected=request.model_copy(update={"resource_ids":tuple(sorted(roots,key=str))})
    identifier=uuid4()
    create_binding(uow,actor,identifier,selected,snapshot,roots,references,candidates,digest)
    refs=[min(roots,key=str), identifier, *sorted(references,key=str)]
    payload={"export_id":str(identifier),"reference_ids":list(dict.fromkeys(map(str,refs)))}
    uow.job_settings=settings_for(uow)
    return enqueue(uow,actor,"semantic.export",payload,snapshot.bundle_id)


def _references(payload):
    if set(payload)!={"export_id","reference_ids"}:
        raise ApiError("EXPORT_BINDING_INVALID",409,"Invalid export job")
    values=tuple(UUID(v) for v in payload["reference_ids"])
    if UUID(payload["export_id"]) not in values or len(set(values))!=len(values):
        raise ApiError("EXPORT_BINDING_INVALID",409,"Invalid export job")
    return values


def authorize_publication(uow, actor, identifier):
    value=_binding(uow,identifier)
    if value["initiator_id"] != actor.subject_id or uow.session.execute(select(export_outputs).where(export_outputs.c.export_id==identifier)).first():
        raise ApiError("EXPORT_ALREADY_COMPLETED",409,"Export publication is immutable")
    require_permission(actor,"view",_scope(uow,identifier),uow=uow)
    for ref in uow.session.execute(select(export_dependencies.c.resource_id).where(
            export_dependencies.c.export_id==identifier,export_dependencies.c.export_root.is_(True))).scalars():
        require_permission(actor,"export",_scope(uow,ref),uow=uow)
    return value


def complete_publication(uow, identifier, blob):
    uow.session.execute(insert(export_outputs).values(export_id=identifier,version_id=blob.version_id,
        sha256=blob.sha256,state="completed"))


def serialize_export(dataset, request, max_bytes):
    """N-Triples is the exact lexical Turtle subset; JSON-LD uses S01's seam."""
    def checked(value):
        if len(value)>max_bytes:
            raise ApiError("EXPORT_BUDGET_EXCEEDED",422,"Export exceeds artifact byte budget")
        return value
    if request.layout=="single-graph":
        selected=Dataset()
        for triple in dataset.graph(URIRef(logical_graph_iri(request.graph_role))):
            selected.default_graph.add(triple)
        raw=(selected.default_graph.serialize(format="nt") if request.format=="turtle" else export_jsonld(selected)).encode()
        return checked(raw), "text/turtle" if request.format=="turtle" else "application/ld+json", "export.ttl" if request.format=="turtle" else "export.jsonld"
    if request.format=="json-ld":
        return checked(export_jsonld(dataset).encode()),"application/ld+json","export.jsonld"
    output=io.BytesIO();manifest={"scope":"authorized_subset","blank_node_scope":"dataset","graphs":[]}
    with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED) as archive:
        for graph in sorted(dataset.graphs(),key=lambda g:str(g.identifier)):
            if graph.identifier==dataset.default_graph.identifier or not len(graph):
                continue
            role=str(graph.identifier).rsplit(":",1)[-1];filename=role+".ttl"
            raw=checked(graph.serialize(format="nt").encode())
            if output.tell()+len(raw)>max_bytes:
                raise ApiError("EXPORT_BUDGET_EXCEEDED",422,"Export exceeds artifact byte budget")
            archive.writestr(filename,raw)
            manifest["graphs"].append({"role":role,"iri":str(graph.identifier),"file":filename,
                "sha256":hashlib.sha256(raw).hexdigest(),"blank_node_scope":"dataset"})
        archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False).encode())
    return checked(output.getvalue()),"application/zip","export.zip"


def _run(ctx):
    with ctx.batch():
        ctx.uow.ontology_settings=ctx.runner.settings
        identifier=UUID(ctx.payload["export_id"])
        value=_binding(ctx.uow,identifier)
        output=ctx.uow.session.execute(select(export_outputs).where(export_outputs.c.export_id==identifier)).mappings().first()
        if output is None:
            authorize_publication(ctx.uow,ctx.actor,identifier)
            request=ExportRequest.model_validate(value["request"])
            snapshot,data,roots,references,candidates,digest=_selected(ctx.actor,request)
            dependencies=ctx.uow.session.execute(select(export_dependencies).where(export_dependencies.c.export_id==identifier)).mappings().all()
            if (digest!=value["input_digest"] or snapshot.bundle_id!=value["bundle_id"] or
                    references!={d["resource_id"] for d in dependencies} or
                    candidates!={d["resource_id"] for d in dependencies if d["candidate"]}):
                raise ApiError("EXPORT_INPUT_CHANGED",409,"Export inputs changed; request a fresh export")
            raw,media,filename=serialize_export(data,request,ctx.runner.settings.artifact_max_upload_bytes)
            revalidate(ctx.uow,ctx.actor,snapshot)
            store=store_for(ctx.uow);blob=store.put(io.BytesIO(raw),media)
            from lzo.artifacts.service import ArtifactService
            detail=ArtifactService(ctx.uow,store,ctx.runner.settings).register_semantic_export(
                ctx.actor,identifier,blob,filename=filename,
                metadata={"scope":"authorized_subset","format":request.format,"layout":request.layout,
                          "input_watermark":snapshot.input_watermark,"bundle_id":str(snapshot.bundle_id)})
            version_id=detail.version_id
        else:
            version_id=output["version_id"]
        ctx.set_result(JobResultRef(kind="semantic.export.artifact",resource_id=version_id,
            api_path=f"/api/v1/artifacts/{version_id}"))
        ctx.checkpoint({"scope":"authorized_subset","published":True})


register_generated_read_policy(generated_read_statement)
register_handler("semantic.export",_run,references=_references,action="export")
register_result_kind("semantic.export.artifact","/api/v1/artifacts/{resource_id}")
from lzo.semantic.projection import register_nonsemantic
register_nonsemantic("semantic.export.prepared",POLICY)
