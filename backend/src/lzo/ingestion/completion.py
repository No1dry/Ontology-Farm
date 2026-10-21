"""Read S04's historical publication evidence for exact import inputs."""
from uuid import UUID
from sqlalchemy import select
from lzo.governance.models import revisions
from lzo.semantic.inference_jobs import coverage,runs,terminals,manifests,components
from lzo.semantic.models import projection_events


def published_inputs(uow,event_ids):
    wanted=set(event_ids)
    evidence=uow.session.execute(select(coverage.c.event_id,coverage.c.run_id,
        runs.c.payload.label('run'),terminals.c.audit_event_id).join(runs,runs.c.id==coverage.c.run_id)
        .join(terminals,terminals.c.run_id==coverage.c.run_id).where(
            coverage.c.event_id.in_(wanted),terminals.c.state=='published')).mappings().all()
    if {row['event_id'] for row in evidence}!=wanted:return False
    checked=set()
    for row in evidence:
        run_id=row['run_id'];run=row['run']
        if str(row['event_id']) not in run['scope']['covered_input_event_ids']:return False
        if run_id in checked:continue
        audit=uow.session.execute(select(revisions).where(revisions.c.event_id==row['audit_event_id'],
            revisions.c.resource_id==run_id,revisions.c.event_type=='inference.manifest.published')).mappings().one_or_none()
        if audit is None:return False
        payload=audit['payload']
        manifest=uow.session.execute(select(manifests.c.payload).where(
            manifests.c.id==UUID(payload['manifest_id']))).scalar_one_or_none()
        output_ids={UUID(value) for value in payload['output_component_ids']}
        if (manifest is None or manifest['run_id']!=str(run_id)
                or not {str(value) for value in output_ids}<=set(manifest['components'])):return False
        outputs=uow.session.execute(select(components).where(
            components.c.payload['run_id'].astext==str(run_id))).mappings().all()
        if {output['id'] for output in outputs}!=output_ids or len(outputs)!=len(run['components']):return False
        projected=uow.session.execute(select(projection_events.c.resource_id).where(
            projection_events.c.resource_id.in_(output_ids),projection_events.c.state=='synced')).scalars().all()
        if set(projected)!=output_ids:return False
        checked.add(run_id)
    return True
