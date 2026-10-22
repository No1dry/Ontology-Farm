"""Map writes share the caller's ledger/UoW; all reads recheck F03/F04."""
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, insert, or_, select, update

from lzo.artifacts.models import artifact_versions
from lzo.artifacts.service import ArtifactService, _permission
from lzo.storage.blobs import BlobStore, get_blob_store
from lzo.auth.models import grants, resource_scopes, roles
from lzo.auth.permissions import authorized_statement, grant_predicate, require_permission, validate_actor
from lzo.config import Settings
from lzo.contracts import ActorContext, BlobRef, ResourceScope, RevisionRef, UnitOfWork
from lzo.errors import ApiError, Forbidden
from lzo.governance.history import revision_metadata, revision_visibility_dependencies
from lzo.governance.models import revisions
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.maps.models import map_artifacts, map_resources
from lzo.maps.online import provider
from lzo.maps.schemas import MapResourceCreate, MapResourcePatch, MapResourcePage, MapResourceView
from lzo.maps.tiles import (asset_path, bounded_json, inspect_package, package_tile,
    resolve_style, tile_bytes, tile_in_bbox, verify_digest)


def referenced_versions(payload):
    return set(v for v in [payload.artifact_version_id, payload.style_version_id,
        *payload.asset_versions.values()] if v is not None)


class MapService:
    def __init__(self, uow: UnitOfWork, store: BlobStore, settings: Settings):
        self.uow, self.store, self.settings = uow, store, settings
        self.artifacts = ArtifactService(uow, store, settings)

    def get(self, actor, resource_id, action='view') -> MapResourceView:
        _permission(self.uow, actor, resource_id, 'view')
        row = self.uow.session.execute(select(map_resources).where(map_resources.c.id == resource_id)).mappings().one_or_none()
        if row is None:
            raise ApiError('NOT_FOUND', 404, 'Map resource not found')
        view = MapResourceView(**row['payload'], id=row['id'], revision=row['revision'], validation_status='valid')
        self.artifacts.authorize_versions(actor, referenced_versions(view))
        if action != 'view':
            _permission(self.uow, actor, resource_id, action)
        return view

    def list(self, actor, cursor=None, limit=None):
        validate_actor(self.uow, actor)
        try:
            after = UUID(cursor) if cursor else UUID(int=0)
        except ValueError:
            raise ApiError('INVALID_CURSOR', 422, 'Invalid map cursor') from None
        size = min(limit or self.settings.list_page_size, self.settings.list_max_page_size)
        visible = []
        # Keyset scan; reference filtering occurs before selecting the page or next cursor.
        while len(visible) <= size:
            ids = self.uow.session.execute(select(map_resources.c.id).where(
                map_resources.c.id > after,
                map_resources.c.id.in_(authorized_statement(actor, 'view'))).order_by(map_resources.c.id).limit(64)).scalars().all()
            if not ids:
                break
            for ident in ids:
                after = ident
                try:
                    item = self.get(actor, ident)
                except ApiError as error:
                    if error.status == 404:
                        continue
                    raise
                visible.append(item)
                if len(visible) > size:
                    break
        return MapResourcePage(items=visible[:size], next_cursor=str(visible[size - 1].id) if len(visible) > size else None)

    def _scope(self, actor, scope_id, resource_id):
        # Authorize the exact new owner via F03's existing scope grant predicate.
        # This uncommitted projection disappears on every rejected command.
        self.uow.session.execute(insert(resource_scopes).values(resource_id=resource_id,
            scope_id=scope_id, owner_scope_id=scope_id, related_scope_ids=[],
            security_class='data', resource_kind='map.resource'))
        require_permission(actor, 'edit', ResourceScope(resource_id, scope_id, (), 'data'), uow=self.uow)
        require_permission(actor, 'view', ResourceScope(resource_id, scope_id, (), 'data'), uow=self.uow)

    def _destination_scope(self, actor, scope_id):
        validate_actor(self.uow, actor)
        for action in ('view', 'edit'):
            permitted = self.uow.session.execute(select(grants.c.id).select_from(grants.join(roles)).where(
                grant_predicate(actor, action), grants.c.security_class == 'data',
                or_(grants.c.global_scope.is_(True), grants.c.scope_id == scope_id)).limit(1)).first()
            if not permitted:
                raise Forbidden('FORBIDDEN', 'Map destination scope is not permitted')

    def _blob(self, actor, version_id):
        self.artifacts.authorize_version(actor, version_id)
        row = self.uow.session.execute(select(artifact_versions).where(artifact_versions.c.id == version_id)).mappings().one()
        return BlobRef(row['sha256'], row['size_bytes'], row['media_type'], row['id'])

    def _validate(self, actor, payload):
        for path in payload.asset_versions:
            asset_path(path)
            if not path.endswith(('.png', '.json', '.pbf', '.jpg', '.jpeg', '.webp')):
                raise ApiError('MAP_ASSET_INVALID', 422, 'Unsupported local map asset')
        # The complete reference set is checked even when one digest is identical.
        for version in referenced_versions(payload):
            blob = self._blob(actor, version)
            with self.store.open(blob) as stream:
                verify_digest(stream, blob)
        if payload.kind == 'online':
            provider(payload.online_url_template)
        else:
            with self.artifacts.content(actor, payload.artifact_version_id) as stream:
                info = inspect_package(stream, payload.kind, payload.tile_kind)
            bbox = info['bbox']
            if (len(bbox) != 4 or not all(bbox[i] <= payload.bbox[i] + 1e-7 for i in (0, 1)) or
                    not all(bbox[i] >= payload.bbox[i] - 1e-7 for i in (2, 3)) or
                    payload.min_zoom < info['min_zoom'] or payload.max_zoom > info['max_zoom']):
                raise ApiError('MAP_COVERAGE_INVALID', 422, 'Declared coverage exceeds package coverage')
        if payload.style_version_id:
            with self.artifacts.content(actor, payload.style_version_id) as stream:
                style = bounded_json(stream)
            resolve_style(style, payload.asset_versions, self._base(UUID(int=0)), payload.kind, payload.tile_kind)

    def _record(self, actor, resource_id, expected, payload, event_type):
        data = payload.model_dump(mode='json')
        ref = append_revision(self.uow, actor, resource_id, expected, data, event_type)
        if expected == 0:
            self.uow.session.execute(insert(map_resources).values(id=resource_id, revision=ref.revision, payload=data))
        else:
            self.uow.session.execute(update(map_resources).where(map_resources.c.id == resource_id).values(revision=ref.revision, payload=data))
            self.uow.session.execute(delete(map_artifacts).where(map_artifacts.c.resource_id == resource_id))
        refs = referenced_versions(payload)
        self.uow.session.execute(insert(revision_metadata).values(event_id=ref.event_id,
            evidence_refs=[str(v) for v in sorted(refs)], proof_refs=[]))
        dependencies = set(refs)
        for version in refs:
            self.uow.session.execute(insert(map_artifacts).values(resource_id=resource_id, version_id=version))
            owner = self.uow.session.execute(select(artifact_versions.c.resource_id).where(artifact_versions.c.id == version)).scalar_one()
            dependencies.add(owner)
            dependencies.update(self.uow.session.execute(select(revision_visibility_dependencies.c.resource_id).join(
                revisions, revisions.c.event_id == revision_visibility_dependencies.c.event_id).where(
                revisions.c.resource_id == version, revisions.c.event_type == 'artifact.uploaded')).scalars())
        for dependency in dependencies:
            self.uow.session.execute(insert(revision_visibility_dependencies).values(event_id=ref.event_id, resource_id=dependency))
        return ref

    def register(self, actor, payload) -> RevisionRef:
        validate_actor(self.uow, actor)
        lock_ledger(self.uow)
        ident = uuid4()
        self._scope(actor, payload.owner_scope_id, ident)
        self._validate(actor, payload)
        return self._record(actor, ident, 0, payload, 'map.created')

    def patch(self, actor, resource_id, payload: MapResourcePatch, expected):
        current = self.get(actor, resource_id, 'edit')
        lock_ledger(self.uow)
        data = current.model_dump(exclude={'id', 'revision', 'validation_status'})
        data.update(payload.model_dump(exclude_unset=True))
        try:
            merged = MapResourceCreate.model_validate(data)
        except ValidationError:
            raise ApiError('MAP_RESOURCE_INVALID', 422, 'Map configuration is invalid') from None
        if merged.owner_scope_id != current.owner_scope_id:
            # Temporary distinct identity checks the destination grant, never an old-object grant.
            self._destination_scope(actor, merged.owner_scope_id)
        self._validate(actor, merged)
        ref = self._record(actor, resource_id, expected, merged, 'map.updated')
        self.uow.session.execute(update(resource_scopes).where(resource_scopes.c.resource_id == resource_id).values(
            scope_id=merged.owner_scope_id, owner_scope_id=merged.owner_scope_id))
        return ref

    def _base(self, resource_id):
        return self.settings.public_origin.rstrip('/') + '/api/v1/maps/resources/' + str(resource_id)

    def style(self, actor, resource_id):
        view = self.get(actor, resource_id)
        if view.style_version_id:
            with self.artifacts.content(actor, view.style_version_id) as stream:
                style = bounded_json(stream)
        else:
            style = {'version': 8, 'sources': {'basemap': {'type': 'vector' if view.tile_kind == 'mvt' else 'raster',
                'tiles': ['tiles/{z}/{x}/{y}'], 'minzoom': view.min_zoom, 'maxzoom': view.max_zoom,
                'attribution': view.attribution}}, 'layers': []}
            if view.tile_kind == 'raster':
                style['sources']['basemap']['tileSize'] = 256
                style['layers'] = [{'id': 'basemap', 'type': 'raster', 'source': 'basemap'}]
        return resolve_style(style, view.asset_versions, self._base(resource_id), view.kind, view.tile_kind)

    def tile(self, actor, resource_id, z, x, y):
        view = self.get(actor, resource_id)
        if (not view.min_zoom <= z <= view.max_zoom or not 0 <= x < 1 << z or not 0 <= y < 1 << z or
                not tile_in_bbox(z, x, y, view.bbox)):
            raise ApiError('MAP_OUT_OF_COVERAGE', 404, 'Tile address is outside map coverage')
        if view.kind == 'online':
            try:
                data = provider(view.online_url_template)(z, x, y)
            except ApiError:
                raise
            except Exception:
                raise ApiError('MAP_PROVIDER_UNAVAILABLE', 503, 'Map provider is unavailable') from None
            return tile_bytes(data, view.tile_kind)
        with self.artifacts.content(actor, view.artifact_version_id) as stream:
            return package_tile(stream, view.kind, view.tile_kind, z, x, y)


def _context_service(uow):
    settings = getattr(uow, 'map_settings', None) or Settings()
    store = getattr(uow, 'artifact_store', None) or get_blob_store(settings)
    return MapService(uow, store, settings)


def register_map_resource(uow: UnitOfWork, actor: ActorContext, payload: MapResourceCreate) -> RevisionRef:
    return _context_service(uow).register(actor, payload)


def read_tile(actor: ActorContext, resource_id: UUID, z: int, x: int, y: int) -> bytes:
    from lzo.auth.permissions import _current_uow
    return _context_service(_current_uow.get()).tile(actor, resource_id, z, x, y)[0]
