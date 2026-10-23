"""Small measured-role visibility fixture; never invokes the benchmark runner."""
import importlib.util
import io
from pathlib import Path
from uuid import uuid4
from sqlalchemy import insert, update
from test_timeseries import telemetry_case


def test_future_measured_role_reads_seeded_observation_and_media(telemetry_case):
    from lzo.auth import models
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.synchronization.delivery import poll_deliveries
    path=Path(__file__).resolve().parents[3]/'scripts/benchmark_multistore.py'
    spec=importlib.util.spec_from_file_location('future_benchmark_visibility',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    s=telemetry_case
    with s.transaction() as uow:
        store=get_blob_store(s.config)
        artifact=ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],
            store.put(io.BytesIO(b'synthetic nonplayable media'),'application/octet-stream'),filename='media.bin',metadata={'synthetic':True})
        role=uuid4()
        uow.session.execute(insert(models.roles).values(id=role,name='future-measured',actions=module.MEASURED_ACTIONS,revision=1))
        s.roles['future-measured']=role
        uow.commit()
    s.grant('nobody','future-measured',scope='farm_a')
    obs=s.observation('measured-observation')
    media=s.observation('measured-media').model_copy(update={'kind':'media','channel_id':'synthetic',
        'artifact_version_id':artifact.version_id,'end_at':obs.sampled_at.replace(minute=1)})
    s.accept([obs,media]);poll_deliveries(s.registry)
    with s.transaction() as uow:
        svc=s.telemetry(uow)
        pages={label:svc.query(s.actor('nobody'),kind=kind) for label,kind in (('observations','observation'),('media','media'))}
        from lzo.telemetry.service import record_id
        expected={'observations':{str(record_id(s.source.id,obs.source_record_id,1))},
            'media':{str(record_id(s.source.id,media.source_record_id,1))}}
        module.require_professional_visibility(pages,expected)
