"""Continuation must not reuse an offset after the authorized edge sequence changes."""
from datetime import timedelta
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from test_inference_retraction import inference_case


@pytest.mark.parametrize("transition", ["expiry", "activation"])
def test_neighborhood_cursor_rejects_temporal_edge_shift(inference_case, transition):
    from lzo.auth.models import authorization_state
    from lzo.governance.models import platform_watermarks, resource_heads
    from lzo.ontology.models import catalog
    from lzo.semantic.models import projection_heads

    s = inference_case
    for name in ("a", "b", "c", "d", "hidden"):
        s.create_node(name)
        s.ids_by_name[name] = s.ids[name]
        if name != "hidden":
            s.grant("nobody", "viewer", resource=name)

    def now():
        with s.transaction() as uow:
            return uow.session.execute(select(func.clock_timestamp())).scalar_one()

    boundary = now() + timedelta(seconds=120)
    hidden_boundary = boundary - timedelta(seconds=40)
    edge_ids = [str(UUID(int=i)) for i in (1, 2, 3, 4)]
    additions = []
    for index, target in enumerate(("b", "c", "d", "hidden")):
        # A current assertion already projects this exact SPO. The distinct
        # future assertion can activate without requiring a new RDF projection.
        if index == 0 and transition == "activation":
            target = "c"
        additions.append({
            "assertion_id": edge_ids[index], "resource_id": str(s.ids["a"]),
            "predicate_iri": "urn:connects",
            "object": {"kind": "iri", "value": f"urn:lzo:object:{s.ids[target]}"},
            "state": "accepted", "data_nature": "test",
            "valid_from": boundary.isoformat() if index == 0 and transition == "activation" else "2026-01-01T00:00:00Z",
            "valid_to": hidden_boundary.isoformat() if index == 3 else
                boundary.isoformat() if index == 0 and transition == "expiry" else None,
            "support": {"support_id": str(uuid4()), "raw_value": {"synthetic": True}},
        })
    revision = s.client.get(f"/api/v1/objects/{s.ids['a']}").json()["revision"]
    response = s.client.patch(f"/api/v1/objects/{s.ids['a']}", headers=s.headers(revision),
        json={"bundle_id": str(s.bundle.id), "patch": {"resource_id": str(s.ids["a"]),
            "expected_revision": revision, "additions": additions}})
    assert response.status_code == 200, response.text
    s.drain()  # Real S03 writes and worker/Fuseki projection; no worker runs below.
    s.login("nobody")
    s.client.app.state.settings = s.config.model_copy(update={"list_page_size": 1})

    def durable_state():
        with s.transaction() as uow:
            return (
                tuple(uow.session.execute(select(platform_watermarks)).one()),
                uow.session.execute(select(authorization_state.c.version)).scalar_one(),
                uow.session.execute(select(catalog.c.active_bundle_id)).scalar_one(),
                tuple(uow.session.execute(select(projection_heads).order_by(projection_heads.c.resource_id))),
                tuple(uow.session.execute(select(resource_heads).order_by(resource_heads.c.resource_id))),
            )

    def page(cursor=None):
        params = {"resource_id": str(s.ids["a"]), "hops": 1}
        if cursor is not None:
            params["cursor"] = cursor
        result = s.client.get("/api/v1/graph/neighborhood", params=params)
        assert str(s.ids["hidden"]) not in result.text
        assert edge_ids[3] not in result.text
        assert "internal" not in result.text
        return result

    def ids(result):
        assert result.status_code == 200, result.text
        return [edge["id"] for edge in result.json()["edges"]]

    def wait_until(target):
        while (remaining := (target - now()).total_seconds()) > 0:
            time.sleep(min(remaining + .05, 1))

    baseline = durable_state()
    first = page()
    assert ids(first) == [edge_ids[0 if transition == "expiry" else 1]]
    cursor = first.json()["next_cursor"]
    assert cursor is not None
    second = page(cursor)
    expected_second = [edge_ids[1 if transition == "expiry" else 2]]
    assert ids(second) == expected_second
    assert now() < hidden_boundary, "fixture missed its real pre-transition window"
    wait_until(hidden_boundary)
    assert ids(page(cursor)) == expected_second  # Hidden expiry is not an obsolete-cursor signal.
    assert now() < boundary, "fixture missed its real visible transition window"
    wait_until(boundary)
    obsolete = page(cursor)

    # A fresh traversal must return every current permitted edge exactly once.
    current, next_cursor = [], None
    for _ in range(4):
        result = page(next_cursor)
        current.extend(ids(result))
        next_cursor = result.json()["next_cursor"]
        if next_cursor is None:
            break
    assert next_cursor is None
    assert current == (edge_ids[1:3] if transition == "expiry" else edge_ids[:3])
    assert durable_state() == baseline  # Time alone: no event, ACL, input, projection or head change.
    assert obsolete.status_code == 422, (transition, obsolete.status_code, obsolete.json())
    assert obsolete.json()["code"] == "INVALID_CURSOR"
