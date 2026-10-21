"""Real PostGIS governed writes; missing CRS never silently defaults."""


def test_geometry_missing_crs_and_native_area(object_case):
    s = object_case
    obj = s.create_object().json()["revision"]["resource_id"]
    geometry = {"resource_id": obj, "expected_revision": 1,
        "geojson": {"type": "Polygon", "coordinates": [[[114.5,30.2],[114.501,30.2],
            [114.501,30.201],[114.5,30.201],[114.5,30.2]]]},
        "source_support": s.assertion(obj)["support"], "decision_ref": None}
    url = f"/api/v1/geometry/{obj}"
    assert s.client.put(url, headers=s.headers(1),
        json={"geometry": geometry, "bundle_id": str(s.bundle_id)}).status_code == 422
    geometry["source_crs"] = "EPSG:4490"
    response = s.client.put(url, headers=s.headers(1),
        json={"geometry": geometry, "bundle_id": str(s.bundle_id)})
    assert response.status_code == 200, response.text
    collection = s.client.get(url).json()
    feature = collection["features"][0]
    assert feature["properties"]["source_crs"] == "EPSG:4490"
    assert 10600 < feature["properties"]["area_square_metres"] < 10750
    assert feature["properties"]["geometry_revision"] == 2
    for rejected in ({"type": "Polygon", "coordinates": []},
                     {"type": "Point", "coordinates": "bad"},
                     {"type": "Point", "coordinates": [190, 95]},
                     {"type": "Polygon", "coordinates": [[[114.5,30.2],[114.501,30.201],
                         [114.501,30.2],[114.5,30.201],[114.5,30.2]]]}):
        geometry.update(expected_revision=2, geojson=rejected)
        response = s.client.put(url, headers=s.headers(2),
            json={"geometry": geometry, "bundle_id": str(s.bundle_id)})
        assert response.status_code == 422, response.text
        assert s.client.get(f"/api/v1/objects/{obj}").json()["revision"] == 2
        assert s.client.get(url).json()["features"][0] == feature
    s.login("nobody")
    assert s.client.get(url).status_code == 404
    assert s.client.get("/api/v1/geometry").json()["features"] == []


def test_composed_object_fact_geometry_failure_rolls_back_every_effect(object_case):
    import pytest
    from uuid import uuid4
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.ontology.releases import get_execution_bundle
    from lzo.semantic.facts import register_object, apply_fact_patch
    from lzo.semantic.geometry import write_geometry
    from lzo.semantic.schemas import CreateObjectInput, FactPatch, GeometryInput
    from lzo.governance.models import revisions
    from lzo.semantic.models import objects, projection_events
    s = object_case
    identifier = uuid4()
    with pytest.raises(ApiError, match="GEOMETRY_INVALID"):
        with s.transaction() as uow, permission_context(uow):
            uow.ontology_settings = s.config
            actor = s.actor("alice")
            bundle = get_execution_bundle(actor, s.bundle_id)
            register_object(uow, actor, CreateObjectInput(resource_id=identifier,
                class_iris=("urn:Pond",), display_name="Synthetic rollback",
                owner_scope_id=s.scopes["farm_a"], state="active", data_nature="synthetic"), bundle)
            apply_fact_patch(uow, actor, FactPatch(resource_id=identifier, expected_revision=1,
                additions=(s.assertion(identifier),)), bundle)
            write_geometry(uow, actor, GeometryInput(resource_id=identifier, expected_revision=2,
                geojson={"type": "Polygon", "coordinates": []}, source_crs="EPSG:4326",
                source_support=s.assertion(identifier)["support"]), bundle)
            uow.commit()
    with s.transaction() as uow:
        for table, column in ((objects, objects.c.id), (revisions, revisions.c.resource_id),
                              (projection_events, projection_events.c.resource_id)):
            assert uow.session.execute(select(column).select_from(table).where(column == identifier)).first() is None
