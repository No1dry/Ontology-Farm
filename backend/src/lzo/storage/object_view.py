"""Object data composition with independent domain receipts and current authority."""
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from lzo.artifacts.service import _permission
from lzo.catalog.repository import device_models
from lzo.catalog.service import CatalogService
from lzo.errors import ApiError
from lzo.governance.models import revisions
from lzo.gis.repository import GisRepository
from lzo.synchronization.delivery import require_deliveries, DeliveriesPending, DeliveriesFailed
from lzo.telemetry.service import TelemetryService


class ObjectDataService:
    def __init__(self, uow, registry):
        self.uow, self.registry = uow, registry
        self.telemetry = TelemetryService(uow, registry)

    def get(self, actor, identifier):
        self.telemetry._object(actor, identifier)
        row = self.uow.session.execute(select(revisions).where(revisions.c.resource_id == identifier)
            .order_by(revisions.c.revision.desc()).limit(1)).mappings().one()
        identity = {'id': str(identifier), 'iri': 'urn:lzo:object:'+str(identifier), 'revision': row['revision'],
            'event_id': str(row['event_id']), 'status': 'current'}
        semantic = row['payload'].get('object', {})
        identity.update({key: semantic[key] for key in ('display_name', 'class_iris', 'data_nature') if key in semantic})
        gis = {'status': 'current', 'version': None, 'event_id': None, 'geometry': None}
        try:
            require_deliveries(self.uow, row['event_id'], ('gis',))
            gis.update(version=row['revision'], event_id=str(row['event_id']))
            values = GisRepository(self.registry).batch([identifier], limit=1)
            if values and values[0]['event_id'] == row['event_id']:
                gis.update(version=values[0]['revision'], geometry=values[0]['payload'])
            elif values:
                gis['status'] = 'pending'
        except DeliveriesFailed:
            gis['status'] = 'failed'
        except DeliveriesPending:
            gis['status'] = 'pending'
        except DBAPIError:
            gis['status'] = 'unavailable'
        catalog = {'status': 'current', 'models': [], 'documents': []}
        service = CatalogService(self.uow, self.registry)
        for association in self.uow.session.execute(select(device_models).where(device_models.c.entity_id == identifier)).mappings():
            try:
                _permission(self.uow, actor, association['resource_id'], 'view')
                value = service.get(actor, association['model_id'], kind='model')
                catalog['models'].append(value.model_dump(mode='json'))
                if value.projection_status != 'current':
                    catalog['status'] = value.projection_status
            except ApiError as error:
                if error.status not in (403, 404):
                    raise
        page = service.list(actor, 'document', entity_id=identifier, limit=min(100, service.settings.list_max_page_size))
        for value in page.items:
            if identifier in value.metadata.entity_ids or 'urn:lzo:object:'+str(identifier) in value.metadata.entity_iris:
                catalog['documents'].append(value.model_dump(mode='json'))
                if value.projection_status != 'current':
                    catalog['status'] = value.projection_status
        catalog['next_document_cursor'] = page.next_cursor
        catalog['watermarks'] = [value['revision'] for value in (*catalog['models'], *catalog['documents'])]
        statuses = {value['projection_status'] for value in (*catalog['models'], *catalog['documents'])}
        catalog['status'] = next((state for state in ('failed', 'unavailable', 'pending') if state in statuses), 'current')
        observations = self.telemetry.query(actor, object_id=identifier, latest=True, limit=100)
        media = self.telemetry.query(actor, object_id=identifier, kind='media', limit=20)
        tracks = self.telemetry.query(actor, object_id=identifier, kind='track', limit=20)
        return {'identity': identity, 'gis': gis, 'catalog': catalog,
            'observations': observations, 'media': media, 'tracks': tracks}
