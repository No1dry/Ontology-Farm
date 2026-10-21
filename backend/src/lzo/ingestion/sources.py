"""Authoritative source configuration and durable upload identity in caller UoW."""
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import insert, select, update, func
from lzo.artifacts.service import ArtifactService, _permission
from lzo.auth.models import resource_scopes
from lzo.auth.permissions import authorized_statement, validate_actor
from lzo.contracts import Page, ResourceScope
from lzo.errors import ApiError, Conflict
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.history import revision_visibility_dependencies
from lzo.ingestion.models import sources, source_uploads, source_records, mapping_profiles
from lzo.ingestion.schemas import SourceCreate, SourceView, SourceKey, UploadBinding, MappingProfile

PARSER_CONTRACT = "file-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def add_scoped(uow, actor, identifier, scope, kind, action):
    if scope.resource_id is not None or not 1 <= len(scope.security_class) <= 64 or len(scope.related_scope_ids) > 64:
        raise ApiError("INVALID_SCOPE", 422, "A new scoped identity is required")
    uow.session.execute(insert(resource_scopes).values(resource_id=identifier,
        scope_id=scope.owner_scope_id, owner_scope_id=scope.owner_scope_id,
        related_scope_ids=list(map(str, scope.related_scope_ids)), security_class=scope.security_class,
        resource_kind="evidence.pending" if kind == "evidence" else kind))
    _permission(uow, actor, identifier, action)
    if kind == "evidence":
        _permission(uow, actor, identifier, "evidence.read")


def bind(uow, ref, dependencies):
    for identifier in set(dependencies):
        uow.session.execute(insert(revision_visibility_dependencies).values(event_id=ref.event_id, resource_id=identifier))


class SourceService:
    def __init__(self, uow=None):
        self.uow = uow

    def create(self, uow, actor, command: SourceCreate) -> SourceView:
        if command.data_nature == "verified":
            raise ApiError("VERIFIED_PROVENANCE_REQUIRED", 422, "Verified source creation is unavailable")
        identifier = uuid4()
        add_scoped(uow, actor, identifier, command.scope, "source", "source.manage")
        command = command.model_copy(update={"scope": ResourceScope(identifier, command.scope.owner_scope_id,
            command.scope.related_scope_ids, command.scope.security_class)})
        uow.session.execute(insert(sources).values(id=identifier, configuration=command.model_dump(mode="json"), revision=1))
        append_revision(uow, actor, identifier, 0, command.model_dump(mode="json"), "source.created")
        return SourceView(id=identifier, revision=1, configuration=command)

    def read(self, actor, source_id, action="source.read"):
        _permission(self.uow, actor, source_id, action)
        row = self.uow.session.execute(select(sources).where(sources.c.id == source_id)).mappings().first()
        if row is None:
            raise ApiError("NOT_FOUND", 404, "Source not found")
        return SourceView(id=row["id"], revision=row["revision"], configuration=SourceCreate.model_validate(row["configuration"]),
            mapping_version_id=self._active_profile(actor, source_id),**self._sync_metadata(source_id))

    def _sync_metadata(self,source_id):
        from lzo.ingestion.models import source_connections,source_schedules,source_watermarks
        connection=self.uow.session.execute(select(source_connections.c.revision).where(source_connections.c.source_id==source_id)).scalar_one_or_none()
        schedule=self.uow.session.execute(select(source_schedules.c.revision).where(source_schedules.c.source_id==source_id)).scalar_one_or_none()
        water=self.uow.session.execute(select(source_watermarks.c.payload).where(source_watermarks.c.source_id==source_id)).scalar_one_or_none()
        return {'connection_revision':connection,'schedule_revision':schedule,'last_success_at':(water or {}).get('at')}

    def _active_profiles(self):
        from lzo.ontology.models import catalog, bundles
        from lzo.ingestion.models import rule_versions
        value = self.uow.session.execute(select(rule_versions.c.payload).join(bundles,
            bundles.c.mapping_version_id == rule_versions.c.id).join(catalog,
            catalog.c.active_bundle_id == bundles.c.id)).scalar_one_or_none()
        return {UUID(m["source_id"]):UUID(m["profile_id"]) for m in (value or {}).get("members", ())}

    def _active_profile(self, actor, source_id):
        from lzo.ontology.drafts import authorize
        identifier=self._active_profiles().get(source_id)
        if identifier is not None:
            authorize(self.uow, actor, identifier)
            return identifier
        return None

    def list(self, actor, cursor=None, limit=50):
        validate_actor(self.uow, actor)
        from lzo.ontology.drafts import visible_statement
        profiles=self._active_profiles()
        visible=set(self.uow.session.execute(visible_statement(actor,mapping_profiles)
            .with_only_columns(mapping_profiles.c.id).where(mapping_profiles.c.id.in_(profiles.values()))).scalars())
        hidden_sources={source_id for source_id,profile_id in profiles.items() if profile_id not in visible}
        statement = select(sources).where(sources.c.id.in_(authorized_statement(actor, "view")),
            sources.c.id.in_(authorized_statement(actor, "source.read")),sources.c.id.not_in(hidden_sources))
        if cursor:
            try:
                statement = statement.where(sources.c.id > UUID(cursor))
            except ValueError:
                raise ApiError("INVALID_CURSOR", 422, "Invalid source cursor") from None
        rows = self.uow.session.execute(statement.order_by(sources.c.id).limit(limit+1)).mappings().all()
        views = [SourceView(id=r["id"], revision=r["revision"], configuration=r["configuration"],
            mapping_version_id=profiles.get(r["id"]),**self._sync_metadata(r['id'])) for r in rows[:limit]]
        return Page(views, str(views[-1].id) if len(rows)>limit else None)

    def save_mapping(self, uow, actor, profile: MappingProfile) -> MappingProfile:
        from lzo.ontology.drafts import authorize, bind_dependencies
        from lzo.semantic.rdf import iri
        source = SourceService(uow).read(actor, profile.source_id, "source.manage")
        lock_ledger(uow)
        source = SourceService(uow).read(actor, profile.source_id, "source.manage")
        iri(profile.class_iri)
        for field in profile.fields:
            iri(field.predicate_iri)
            if field.datatype_iri: iri(field.datatype_iri)
        from lzo.ingestion.mapping import validate_expression,path_value,convert_unit
        try:
            for path in (*profile.identity_paths,profile.name_path,*(f.source_path for f in profile.fields)):
                path_value({},path)
            if profile.geometry_path: path_value({},profile.geometry_path)
            for field in profile.fields:
                if field.expression: validate_expression(field.expression)
                if bool(field.source_unit)!=bool(field.target_unit): raise ValueError("Both units required")
                if field.source_unit:convert_unit("0",field.source_unit,field.target_unit)
        except ValueError:
            raise ApiError("INVALID_MAPPING_EXPRESSION",422,"Invalid bounded mapping expression or path") from None
        if len({f.predicate_iri for f in profile.fields}) != len(profile.fields):
            raise ApiError("DUPLICATE_MAPPING_FIELD", 422, "A predicate may be mapped only once")
        deps = {profile.source_id}
        if profile.confirmed_identity_rule_id:
            authorize(uow, actor, profile.confirmed_identity_rule_id)
            deps.add(profile.confirmed_identity_rule_id)
        ordinal = (uow.session.execute(select(func.max(mapping_profiles.c.ordinal)).where(
            mapping_profiles.c.source_id == profile.source_id)).scalar_one() or 0) + 1
        identifier = uuid4()
        content = profile.model_dump(mode="json", exclude={"id", "revision", "digest"})
        result = profile.model_copy(update={"id":identifier, "revision":ordinal, "digest":digest(content)})
        scope = source.configuration.scope
        add_scoped(uow, actor, identifier, ResourceScope(None, scope.owner_scope_id,
            scope.related_scope_ids, scope.security_class), "source.mapping", "source.manage")
        event = append_revision(uow, actor, identifier, 0, result.model_dump(mode="json"), "source.mapping.created")
        bind_dependencies(uow, identifier, deps, event.event_id)
        uow.session.execute(insert(mapping_profiles).values(id=identifier, source_id=profile.source_id,
            ordinal=ordinal, payload=result.model_dump(mode="json")))
        source_event = append_revision(uow, actor, source.id, source.revision,
            {"configuration":source.configuration.model_dump(mode="json"), "saved_profile_id":str(identifier)}, "source.mapping.saved")
        bind(uow, source_event, [identifier])
        uow.session.execute(update(sources).where(sources.c.id == source.id).values(revision=source_event.revision))
        return result

    def get_mapping(self, actor, source_id, version_id=None):
        from lzo.ontology.drafts import authorize
        self.read(actor, source_id)
        statement = select(mapping_profiles).where(mapping_profiles.c.source_id == source_id)
        if version_id is not None: statement = statement.where(mapping_profiles.c.id == version_id)
        row = self.uow.session.execute(statement.order_by(mapping_profiles.c.ordinal.desc()).limit(1)).mappings().first()
        if row is None: raise ApiError("NOT_FOUND", 404, "Mapping not found")
        authorize(self.uow, actor, row["id"])
        return MappingProfile.model_validate(row["payload"])


from lzo.semantic.projection import register_nonsemantic
register_nonsemantic("source.mapping.created", "ingestion-v1")
register_nonsemantic("source.mapping.saved", "ingestion-v1")


class ConnectionService:
    def __init__(self,uow):self.uow=uow

    def get(self,actor,source_id):
        from lzo.ingestion.models import source_connections
        from lzo.ingestion.schemas import ConnectionConfig
        source=SourceService(self.uow).read(actor,source_id)
        row=self.uow.session.execute(select(source_connections.c.payload).where(source_connections.c.source_id==source_id)).scalar_one_or_none()
        if row is None:raise ApiError('NOT_FOUND',404,'Connection not configured')
        return ConnectionConfig.model_validate(row).model_copy(update={'expected_revision':source.revision})

    def configure(self,uow,actor,command):
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from lzo.ingestion.models import source_connections,source_credentials
        SourceService(uow).read(actor,command.source_id,'source.manage')
        lock_ledger(uow)
        source=SourceService(uow).read(actor,command.source_id,'source.manage')
        if source.revision!=command.expected_revision:raise Conflict('REVISION_CONFLICT','Source changed')
        if source.configuration.kind not in ('postgresql','mysql'):raise ApiError('SOURCE_CONFIGURATION_MISMATCH',422,'Database source required')
        if not uow.session.execute(select(source_credentials.c.ref).where(source_credentials.c.ref==command.credential_ref,
            source_credentials.c.source_id==source.id)).first():raise ApiError('CREDENTIAL_NOT_FOUND',404,'Source credential unavailable')
        event=append_revision(uow,actor,source.id,source.revision,command.model_dump(mode='json'),'source.connection.changed')
        value=command.model_copy(update={'expected_revision':event.revision})
        statement=pg_insert(source_connections).values(source_id=source.id,revision=event.revision,payload=value.model_dump(mode='json'))
        uow.session.execute(statement.on_conflict_do_update(index_elements=['source_id'],set_={'revision':event.revision,'payload':value.model_dump(mode='json')}))
        uow.session.execute(update(sources).where(sources.c.id==source.id).values(revision=event.revision))
        return value


register_nonsemantic('source.connection.changed','ingestion-v1')


def current_source_change(uow,old):
    """Persisted currentness only. Original event reads never call this selector."""
    from lzo.ingestion.models import source_positions
    from lzo.ingestion.schemas import SourceChange
    from lzo.governance.models import revisions,outbox_events
    kind=uow.session.execute(select(sources.c.configuration).where(sources.c.id==old.key.source_id)).scalar_one()['kind']
    if kind!='file':
        row=uow.session.execute(select(source_positions).where(source_positions.c.source_id==old.key.source_id,
            source_positions.c.canonical_key==old.key.canonical_key)).mappings().first()
        if row is None or row['payload'].get('conflict'):raise ValueError('Current source evidence requires reconciliation')
        value=uow.session.execute(select(source_records.c.payload).where(source_records.c.id==row['change_id'])).scalar_one()
        return SourceChange.model_validate(value)
    value=uow.session.execute(select(source_records.c.payload).join(outbox_events,
        outbox_events.c.event_id.in_(select(revisions.c.event_id).where(revisions.c.resource_id==source_records.c.id))).where(
        source_records.c.payload['key']['source_id'].astext==str(old.key.source_id),
        source_records.c.payload['key']['canonical_key'].astext==old.key.canonical_key).order_by(outbox_events.c.position.desc()).limit(1)).scalar_one_or_none()
    if value is None:raise ValueError('Registered evidence unavailable')
    return SourceChange.model_validate(value)


class UploadService:
    def __init__(self, uow, store, settings):
        self.uow, self.store, self.settings = uow, store, settings

    def register(self, actor, source_id, blob, options, idempotency_key):
        source = SourceService(self.uow).read(actor, source_id, "source.manage")
        if source.configuration.kind != "file" or options.data_nature != source.configuration.data_nature:
            raise ApiError("SOURCE_CONFIGURATION_MISMATCH", 422, "Upload does not match source configuration")
        artifact = ArtifactService(self.uow, self.store, self.settings).get(actor, blob.version_id)
        if (artifact.sha256, artifact.size_bytes, artifact.media_type) != (blob.sha256, blob.size_bytes, blob.media_type):
            raise Conflict("BLOB_REF_CONFLICT", "Registered artifact does not match")
        from lzo.ingestion.schemas import SemanticFileOptions
        parser_contract="semantic-v1" if isinstance(options,SemanticFileOptions) else "file-v2" if options.temporal_fields is not None else PARSER_CONTRACT
        config_digest = digest({"parser": parser_contract, "options": options.model_dump(mode="json")})
        command_digest = digest({"source": str(source_id), "sha256": blob.sha256, "config": config_digest})
        def register():
            # Global ledger lock follows the existing F02 order; uniqueness also
            # protects concurrent registration under different actor command keys.
            lock_ledger(self.uow)
            existing = self.uow.session.execute(select(source_uploads).where(
                source_uploads.c.source_id == source_id, source_uploads.c.sha256 == blob.sha256,
                source_uploads.c.config_digest == config_digest)).mappings().first()
            if existing:
                old = UploadBinding.model_validate(existing["binding"])
                ArtifactService(self.uow, self.store, self.settings).get(actor, old.blob.version_id)
                return old.model_dump(mode="json")
            identifier = uuid4()
            config = source.configuration
            binding = UploadBinding(id=identifier, parser_contract=parser_contract, source_key=SourceKey(source_id=source_id,
                source_system=config.source_system, source_instance=config.source_instance,
                collection=config.collection, canonical_key=""), source_revision=source.revision,
                blob=blob, options=options, epoch=uuid4(), received_at=datetime.now(timezone.utc))
            scope = ResourceScope(None, config.scope.owner_scope_id, config.scope.related_scope_ids, config.scope.security_class)
            add_scoped(self.uow, actor, identifier, scope, "source.upload", "source.manage")
            self.uow.session.execute(insert(source_uploads).values(id=identifier, source_id=source_id,
                sha256=blob.sha256, config_digest=config_digest, version_id=blob.version_id, binding=binding.model_dump(mode="json")))
            event = append_revision(self.uow, actor, identifier, 0, binding.model_dump(mode="json"), "source.upload.registered")
            bind(self.uow, event, [source_id, blob.version_id])
            return binding.model_dump(mode="json")
        result = execute_idempotent(self.uow, actor, idempotency_key, command_digest, register)
        binding = UploadBinding.model_validate(result)
        ArtifactService(self.uow, self.store, self.settings).get(actor, binding.blob.version_id)
        return binding

    def persist(self, actor, binding, parsed):
        """Reparse registered bytes to authenticate normalized events before save."""
        from lzo.ingestion.files import FileParser, ParserLimits
        actual = self.uow.session.execute(select(source_uploads.c.binding).where(source_uploads.c.id == binding.id)).scalar_one_or_none()
        if actual is None or UploadBinding.model_validate(actual) != binding:
            raise ApiError("UPLOAD_BINDING_MISMATCH", 422, "Registered upload required")
        SourceService(self.uow).read(actor, binding.source_key.source_id, "source.manage")
        ArtifactService(self.uow, self.store, self.settings).get(actor, binding.blob.version_id)
        if not parsed.complete or parsed.issues:
            raise ApiError("INCOMPLETE_PARSE", 422, "Incomplete files cannot be registered")
        if binding.parser_contract=="semantic-v1":
            from lzo.ingestion.semantic_files import SemanticFileParser
            verified=SemanticFileParser(self.store,binding).parse()
        else:
            verified = FileParser(self.store, binding, limits=ParserLimits.from_settings(self.settings)).parse(
                binding.blob, binding.source_key, binding.options)
        if verified != parsed:
            raise ApiError("PARSE_BINDING_MISMATCH", 422, "Normalized events do not match registered bytes")
        lock_ledger(self.uow)
        scope = SourceService(self.uow).read(actor, binding.source_key.source_id).configuration.scope
        for change in parsed.changes:
            if self.uow.session.execute(select(source_records.c.id).where(source_records.c.id == change.change_id)).first():
                continue
            add_scoped(self.uow, actor, change.change_id,
                ResourceScope(None, scope.owner_scope_id, scope.related_scope_ids, scope.security_class), "source.record", "source.manage")
            self.uow.session.execute(insert(source_records).values(id=change.change_id, upload_id=binding.id,
                ordinal=change.position.ordinal, payload=change.model_dump(mode="json")))
            event = append_revision(self.uow, actor, change.change_id, 0, change.model_dump(mode="json"), "source.record.registered")
            bind(self.uow, event, [binding.source_key.source_id, binding.blob.version_id, binding.id])
