"""Real authenticated HTTP against the existing isolated core fixture schema."""
from contextlib import contextmanager
from sqlalchemy import text


@contextmanager
def scenario_client(s):
    from fastapi.testclient import TestClient
    from lzo.main import create_app
    from lzo.auth.session import SessionService, SESSION_COOKIE
    with s.app.connect() as c:
        schema=c.execute(text('SELECT current_schema()')).scalar_one()
    settings=s.config.model_copy(update={'database_url':s.app.url.update_query_dict(
        {'options':'-csearch_path='+schema}).render_as_string(hide_password=False)})
    with s.transaction() as uow:
        cookie=SessionService(uow,settings).create(s.users['alice'],{'synthetic':True})
        uow.commit()
    with TestClient(create_app(settings),base_url='https://testserver') as client:
        client.cookies.set(SESSION_COOKIE,cookie)
        csrf=client.get('/api/v1/session')
        assert csrf.status_code==200,csrf.text
        client.headers.update({'X-CSRF-Token':csrf.json()['csrf_token']})
        yield client
