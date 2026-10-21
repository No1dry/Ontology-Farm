"""S03-owned rollback-only composition with isolated evaluation memory.

The facade shares only the SQL session and immutable service settings. Every
owner-created cache, explicit pin, evaluation field and recursion guard belongs
to the facade; none can mutate or survive on the calling unit of work.
"""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from lzo.auth.permissions import permission_context,validate_actor
from lzo.governance.revisions import lock_ledger


class ProspectiveUnitOfWork(SimpleNamespace):
    def commit(self):raise RuntimeError('Prospective validation cannot commit')
    def rollback(self):raise RuntimeError('Only the prospective owner closes its savepoint')


@contextmanager
def prospective_rows(uow,actor):
    # These locks outlive the savepoint and continue protecting real execution.
    validate_actor(uow,actor);lock_ledger(uow)
    fields={key:deepcopy(value) for key,value in vars(uow).items()
        if key.startswith(('facts_','ingestion_','_semantic_','_ingestion_'))}
    fields.update({key:getattr(uow,key) for key in ('ontology_settings','job_settings') if hasattr(uow,key)})
    scoped=ProspectiveUnitOfWork(session=uow.session,engine=getattr(uow,'engine',None),**fields)
    savepoint=uow.session.begin_nested()
    try:
        with permission_context(scoped):yield scoped
    finally:
        savepoint.rollback()


@contextmanager
def prospective_row(uow):
    fields={key:deepcopy(value) for key,value in vars(uow).items()
        if key not in ('session','engine','ontology_settings','job_settings')}
    try:
        with uow.session.begin_nested():yield
    except BaseException:
        for key in tuple(vars(uow)):
            if key not in ('session','engine','ontology_settings','job_settings'):delattr(uow,key)
        vars(uow).update(fields)
        raise
