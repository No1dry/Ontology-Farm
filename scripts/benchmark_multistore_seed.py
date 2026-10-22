"""Explicit synthetic benchmark fixture installation, excluded from latency.

No production ingestion/throughput claim: SQL rows and complete RDF companion
representations are bulk installed into a fresh test schema/UUID namespace.
GIS uses the real handler/inbox transaction and real core acknowledgement.
All requested, committed, projected, counted and budget-limited sizes are saved.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import time
from uuid import uuid4, uuid5, UUID


def install_seed(s, bundle_id, template_id, config, registry, progress=None):
    import httpx
    from sqlalchemy import insert, select, text, update
    from rdflib import URIRef
    from lzo.auth.models import resource_scopes
    from lzo.governance.models import resource_heads, revisions, audit_events, outbox_events, platform_watermarks
    from lzo.governance.history import revision_visibility_dependencies
    from lzo.semantic import models
    from lzo.semantic.rdf import Triple, RdfTerm, INTERNAL
    from lzo.semantic.fuseki import FusekiRepository, generation_digest, graph_iri, ROLES, V, CATALOG, _manifest, _nt
    from lzo.gis.repository import apply_geometry
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization.delivery import _apply, enqueue_delivery, _acknowledge
    started = time.perf_counter()
    namespace = uuid5(UUID('9af65794-c931-49fc-bfe7-d9d30f14b68f'), str(config['seed'])+':'+uuid4().hex)
    ids = [uuid5(namespace, 'entity:'+str(i)) for i in range(config['entities'])]
    events = [uuid5(namespace, 'event:'+str(i)) for i in range(config['entities'])]
    pin = lambda index: {'resource_id': str(ids[index]), 'revision': 1, 'event_id': str(events[index])}
    now = datetime.now(timezone.utc)
    now_json = now.isoformat()
    repository = FusekiRepository(s.config.fuseki_url, timeout_seconds=120)
    with s.transaction() as uow:
        template = uow.session.execute(select(revisions.c.payload).where(revisions.c.resource_id == template_id,
            revisions.c.event_type == 'object.created')).scalar_one()
        start_position = uow.session.execute(update(platform_watermarks).values(
            event_position=platform_watermarks.c.event_position+len(ids)).returning(platform_watermarks.c.event_position)).scalar_one()-len(ids)+1
        # Cross-entity visibility pins have real FK constraints. Reserve the
        # synthetic identities before chunked immutable revisions are installed.
        uow.session.execute(insert(resource_scopes), [{'resource_id': identifier, 'scope_id': s.scopes['farm_a'],
            'owner_scope_id': s.scopes['farm_a'], 'related_scope_ids': [], 'security_class': 'data', 'resource_kind': 'object'} for identifier in ids])
        # As in register_object, revision zero is an unaccepted identity slot.
        # Cross-chunk dependency FKs require all target heads to exist first.
        uow.session.execute(insert(resource_heads), [{'resource_id': identifier, 'revision': 0} for identifier in ids])
        uow.commit()
    # Reservation and fixture rows are owner-controlled setup, not runtime
    # outbox processing; failed setup is explicitly incomplete in the report.
    projected, applied, persisted_relations = 0, 0, 0
    failure = None
    for offset in range(0, len(ids), 100):
        tables = {table: [] for table in (resource_scopes, resource_heads, revisions, audit_events, outbox_events, revision_visibility_dependencies,
            models.objects, models.assertions, models.supports, models.type_declarations, models.object_dependencies,
            models.projection_events, models.projection_heads, models.projection_classifications)}
        rdf_statements, envelopes, revisions_batch = [], [], []
        for index in range(offset, min(offset+100, len(ids))):
            identifier, event = ids[index], events[index]
            position = start_position+index
            ref = pin(index)
            iri = 'urn:lzo:object:'+str(identifier)
            payload = deepcopy(template)
            payload['object'].update(display_name=f'Synthetic benchmark {index}')
            payload.update(effect={'operation': 'benchmark.fixture.install', 'seed': config['seed']},
                authorization={'actor_id': str(s.users['alice']), 'action': 'edit', 'basis': 'owner_installed_synthetic_fixture'})
            assertion_rows = []
            graphs = {'accepted': [Triple(iri, 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type', RdfTerm('iri', 'urn:benchmark:Entity'))],
                'candidate': [], 'provenance': []}
            edges = config['relations']//len(ids) + (index < config['relations'] % len(ids))
            targets = [(index+step+1) % len(ids) for step in range(edges)]
            pins = deepcopy(template['dependency_revisions']) + [pin(target) for target in targets]
            payload['dependency_revisions'] = pins
            for target in targets:
                aid, sid = uuid5(namespace, f'assertion:{index}:{target}'), uuid5(namespace, f'support:{index}:{target}')
                assertion = {'assertion_id': str(aid), 'resource_id': str(identifier), 'predicate_iri': 'urn:benchmark:next',
                    'object': {'kind': 'iri', 'value': 'urn:lzo:object:'+str(ids[target]), 'datatype_iri': None, 'language': None},
                    'state': 'accepted', 'data_nature': 'synthetic', 'valid_from': '2020-01-01T00:00:00+00:00',
                    'valid_to': None, 'decision_ref': None}
                support = {'support_id': str(sid), 'source_id': None, 'record_key': None, 'source_version': 'synthetic-benchmark-v1',
                    'snapshot': None, 'raw_value': {'synthetic_seed': config['seed']}, 'mapping_version_id': None,
                    'evidence_refs': [], 'observed_at': None}
                support_row = {'id': sid, 'assertion_id': aid, 'payload': support, 'active': True, 'recorded_at': now, 'withdrawn_event_id': None}
                tables[models.assertions].append({'id': aid, 'resource_id': identifier, 'payload': assertion, 'revision_ref': ref, 'bundle_id': bundle_id})
                tables[models.supports].append(support_row)
                assertion_rows.append({'id': str(aid), 'resource_id': str(identifier), 'payload': assertion, 'revision_ref': ref,
                    'bundle_id': str(bundle_id), 'supports': [{**support_row, 'id': str(sid), 'assertion_id': str(aid), 'recorded_at': now_json}]})
                graphs['accepted'].append(Triple(iri, 'urn:benchmark:next', RdfTerm('iri', 'urn:lzo:object:'+str(ids[target]))))
                graphs['provenance'].append(Triple('urn:lzo:assertion:'+str(aid), 'urn:lzo:support', RdfTerm('iri', 'urn:lzo:support:'+str(sid))))
            geometry = {'resource_id': str(identifier), 'expected_revision': 1, 'source_crs': 'EPSG:4326',
                'geojson': {'type': 'Point', 'coordinates': [114+(index % 100)*.001, 30+(index//100)*.001]},
                'source_support': {'support_id': str(uuid5(namespace, 'geometry:'+str(index))), 'source_id': None,
                    'record_key': None, 'source_version': 'synthetic-benchmark-v1', 'snapshot': None, 'raw_value': {},
                    'mapping_version_id': None, 'evidence_refs': [], 'observed_at': None}, 'decision_ref': None,
                'revision': 1, 'data_nature': 'synthetic', 'area_square_metres': 0,
                'area_method': 'synthetic point area is zero', 'coordinate_method': 'synthetic EPSG:4326 fixture'}
            geometry['normalized'] = geometry['geojson']
            payload.update(assertions=assertion_rows, geometry=geometry)
            graphs['accepted'].append(Triple(iri, 'urn:lzo:geometry', RdfTerm('iri', f'urn:lzo:geometry:{identifier}:1')))
            payload['graphs'] = {role: [asdict(t) for t in values] for role,values in graphs.items()}
            content_digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            tables[resource_heads].append({'resource_id': identifier, 'revision': 1, 'digest': content_digest, 'event_id': event})
            tables[revisions].append({'event_id': event, 'resource_id': identifier, 'revision': 1, 'previous_revision': 0,
                'previous_digest': None, 'content_digest': content_digest, 'payload': payload, 'event_type': 'object.created',
                'actor_id': s.users['alice'], 'request_id': 'synthetic-benchmark-seed', 'effective_at': now, 'recorded_at': now})
            tables[audit_events].append({'id': uuid4(), 'event_id': event, 'event_type': 'object.created', 'actor_id': s.users['alice'],
                'request_id': 'synthetic-benchmark-seed', 'recorded_at': now})
            tables[outbox_events].append({'id': uuid4(), 'event_id': event, 'position': position, 'event_type': 'object.created',
                'payload': ref, 'recorded_at': now})
            tables[models.objects].append({'id': identifier, 'iri': iri, 'revision': 1, 'payload': payload['object'],
                'bundle_id': bundle_id, 'transition_origin_event_id': event})
            tables[models.type_declarations].append({'resource_id': identifier, 'class_iris': ['urn:benchmark:Entity'],
                'revision_ref': ref, 'dependency_refs': template['dependency_revisions']})
            tables[models.object_dependencies].extend({'resource_id': identifier, 'dependency_id': UUID(p['resource_id'])} for p in pins)
            tables[revision_visibility_dependencies].extend({'event_id': event, 'resource_id': UUID(p['resource_id'])} for p in [ref, *pins])
            envelope = SyncEnvelope(event_id=event, entity_id=identifier, aggregate_revision=1, target='gis', operation='geometry.index',
                payload={'geometry': geometry, 'active': True, 'asset': None, 'observation': None}, data_nature='synthetic',
                source_refs=tuple(sorted(p['resource_id'] for p in pins)))
            envelopes.append(envelope)
            digest = generation_digest(graphs)
            prepared = [repository._pair(graph_iri(identifier, role), graphs.get(role, ())) for role in ROLES]
            generation = URIRef(INTERNAL+f'generation:{identifier}:1:{digest}')
            manifest = _manifest(generation, URIRef('urn:lzo:resource:'+str(identifier)), 1, position, digest,
                {role: pair.metadata_iri for role,(pair,_,_) in zip(ROLES,prepared)})
            rdf_statements.extend(repository._pair_insert(*item) for item in prepared)
            rdf_statements.append(f'GRAPH <{generation}> {{ {_nt(manifest)} }}')
            pointers = ' '.join(f'<{pair.logical_iri}> <{V.current}> <{pair.metadata_iri}>; <{V.managed}> "1" .' for pair,_,_ in prepared)
            rdf_statements.append(f'GRAPH <{CATALOG}> {{ <{generation}> <{V.prepared}> "1" . <urn:lzo:resource:{identifier}> <{V.generation}> <{generation}> . {pointers} }}')
            tables[models.projection_events].append({'event_id': event, 'resource_id': identifier, 'revision': 1, 'position': position,
                'producer_version': 'objects-v1', 'reference_ids': [str(identifier), *[p['resource_id'] for p in pins]],
                'dependency_refs': pins, 'initiator_id': s.users['alice'], 'state': 'synced', 'digest': digest})
            tables[models.projection_heads].append({'resource_id': identifier, 'revision': 1, 'position': position, 'digest': digest})
            tables[models.projection_classifications].append({'position': position, 'event_id': event, 'kind': 'semantic', 'version': 'objects-v1'})
            persisted_relations += edges
        # Core fixture rows persist even when a bounded target seed fails.
        with s.transaction() as uow:
            uow.session.execute(text('UPDATE resource_heads SET revision=1,digest=:digest,event_id=:event '
                'WHERE resource_id=:resource AND revision=0'), [{'resource': row['resource_id'],
                    'digest': row['digest'], 'event': row['event_id']} for row in tables[resource_heads]])
            for table, rows in tables.items():
                if rows and table not in (resource_heads, models.projection_events, models.projection_heads):
                    uow.session.execute(insert(table), rows)
            for envelope in envelopes:
                enqueue_delivery(uow, envelope)
            uow.commit()
        if failure is None and time.perf_counter()-started < config['seed_seconds']:
            try:
                # Raw SPARQL Update body avoids form-parser size limits during
                # fixture installation; no runtime publisher path is changed.
                response = httpx.post(repository.endpoint+'/update',
                    content=('INSERT DATA { '+' '.join(rdf_statements)+' }').encode(),
                    headers={'Content-Type': 'application/sparql-update'}, timeout=120, trust_env=False)
                response.raise_for_status()
                # Verify real retained generation representation, not an HTTP200 alone.
                for envelope in (envelopes[0], envelopes[-1]):
                    repository._verify_generation(repository.current_revision(envelope.entity_id))
                with s.transaction() as uow:
                    uow.session.execute(insert(models.projection_events), tables[models.projection_events])
                    uow.session.execute(insert(models.projection_heads), tables[models.projection_heads])
                    uow.commit()
                projected += len(envelopes)
                for envelope in envelopes:
                    receipt = _apply(registry, envelope, apply_geometry)
                    with registry.engine('core').begin() as connection:
                        _acknowledge(connection, receipt)
                    applied += 1
            except Exception as error:
                failure = type(error).__name__
        elif failure is None:
            failure = 'seed_projection_time_budget_exceeded'
        if progress is not None:
            progress({'committed_fixture_entities': offset+len(envelopes),
                'verified_projected_entities': projected, 'acknowledged_gis_deliveries': applied,
                'elapsed_seconds': time.perf_counter()-started, 'projection_failure_or_limit': failure,
                'scope': 'committed setup rows; final SQL/RDF counts are reported separately'})
    with s.transaction() as uow:
        actual_entities = uow.session.execute(select(models.objects.c.id).where(models.objects.c.id.in_(ids))).all()
        actual_relations = uow.session.execute(text("SELECT count(*) FROM semantic_assertions WHERE payload->>'predicate_iri'='urn:benchmark:next' AND payload->>'state'='accepted'")).scalar_one()
        projection_count = uow.session.execute(select(models.projection_heads.c.resource_id).where(models.projection_heads.c.resource_id.in_(ids))).all()
        receipts = uow.session.execute(text("SELECT count(*) FROM store_deliveries WHERE entity_id=ANY(:ids) AND target='gis' AND status IN ('applied','superseded')"), {'ids': ids}).scalar_one()
    with registry.engine('gis').connect() as connection:
        gis_rows = connection.execute(text('SELECT count(*) FROM gis_geometry_versions WHERE resource_id=ANY(:ids) AND revision=1'), {'ids': ids}).scalar_one()
    rdf_counts = {}
    try:
        values = ' '.join('<urn:lzo:object:'+str(identifier)+'>' for identifier in ids)
        for name, predicate, obj in (('instances', 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type', '<urn:benchmark:Entity>'),
                ('relations', 'urn:benchmark:next', '?target')):
            query = f'SELECT (COUNT(*) AS ?count) WHERE {{ VALUES ?subject {{ {values} }} GRAPH ?graph {{ ?subject <{predicate}> {obj} }} }}'
            response = httpx.post(repository.endpoint+'/query', content=query.encode(),
                headers={'Content-Type': 'application/sparql-query', 'Accept': 'application/sparql-results+json'},
                timeout=120, trust_env=False, verify=str(s.config.ca_bundle) if s.config.ca_bundle else True)
            response.raise_for_status()
            rdf_counts[name] = int(response.json()['results']['bindings'][0]['count']['value'])
    except Exception as error:
        rdf_counts['count_failure'] = type(error).__name__
    # The whole requested baseline is committed/counted even if its projections
    # exceed the configured budget. API errors then remain explicit measurements.
    return ids, {'namespace_uuid': str(namespace), 'first_entity_id': str(ids[0]), 'last_entity_id': str(ids[-1]),
        'requested_entities': len(ids), 'requested_relations': config['relations'], 'persisted_entities': len(actual_entities),
        'persisted_accepted_relations': actual_relations, 'projected_entities': len(projection_count),
        'real_gis_receipts': receipts, 'actual_gis_versions': gis_rows, 'actual_rdf_counts': rdf_counts, 'projection_failure_or_limit': failure,
        'full_baseline_ready': len(actual_entities) == len(ids) and actual_relations == config['relations']
            and len(projection_count) == len(ids) and receipts == len(ids) and gis_rows == len(ids)
            and rdf_counts.get('instances') == len(ids) and rdf_counts.get('relations') == config['relations'],
        'rdf_verification': 'first/last complete generation in each seeded chunk; all SQL projection head counts, actual RDF count below'}
