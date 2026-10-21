"""Immutable evidence acceptance against a real database and private file volume."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import select

from lzo.errors import ApiError


def test_same_content_does_not_share_permissions(artifacts):
    from lzo.artifacts.service import ArtifactService

    s = artifacts
    s.grant("admin", "editor", scope="farm_b")
    content = b"%PDF-1.7\nprivate evidence\n%%EOF"
    a = s.upload("alice", "object_a", content)
    b = s.upload("admin", "object_b", content)
    assert a.sha256 == b.sha256
    assert a.version_id != b.version_id
    with s.transaction() as uow:
        service = ArtifactService(uow, s.store, s.limits)
        assert service.get(s.actor("alice"), a.version_id).version_id == a.version_id
        with pytest.raises(ApiError) as error:
            service.get(s.actor("alice"), b.version_id)
        assert error.value.status == 404
    assert len(list((s.store.root / "objects").rglob(content.hex()))) == 0
    assert len(list((s.store.root / "objects").rglob(a.sha256))) == 1


@pytest.mark.parametrize("media_type,content", [
    ("application/pdf", b"%PDF-1.7\noriginal\r\n%%EOF"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK\x03\x04word\x00"),
    ("image/png", b"\x89PNG\r\n\x1a\nimage\x00"),
])
def test_original_bytes_and_readonly_seekable_stream(artifacts, media_type, content):
    ref = artifacts.upload("alice", "object_a", content, media_type)
    with artifacts.store.open(ref) as stream:
        assert stream.read(3) == content[:3]
        assert stream.seek(0) == 0
        assert stream.read() == content
        assert stream.writable() is False


def test_interrupted_or_oversize_upload_leaves_no_completed_object(artifacts):
    store = artifacts.store

    class Interrupted(io.BytesIO):
        def read(self, n=-1):
            if self.tell():
                raise OSError("client disconnected")
            return super().read(1)

    with pytest.raises(OSError):
        store.put(Interrupted(b"unfinished"), "application/pdf")
    with pytest.raises(ApiError) as error:
        store.put(io.BytesIO(b"x" * (1024 * 1024 + 1)), "application/pdf")
    assert error.value.status == 413
    assert list((store.root / "staging").iterdir()) == []
    assert list((store.root / "objects").rglob("*")) == []


def test_revocation_hides_content_and_history_reference(artifacts):
    from lzo.artifacts.service import ArtifactService
    from lzo.governance.history import HistoryService

    s = artifacts
    ref = s.upload("alice", "object_a", b"private")
    with s.transaction() as uow:
        detail = ArtifactService(uow, s.store, s.limits).get(s.actor("alice"), ref.version_id)
        assert detail.sha256 == ref.sha256
        history = HistoryService(uow).get(s.actor("alice"), s.ids_by_name["object_a"], 2)
        assert history.evidence_refs == [ref.version_id]
    grant = s.grant("alice", "viewer", scope="farm_a")
    # The editor grant was created by the fixture and is the first active data grant.
    from lzo.auth.models import grants
    with s.transaction() as uow:
        editor_id = uow.session.execute(select(grants.c.id).where(
            grants.c.user_id == s.users["alice"], grants.c.role_id == s.roles["editor"],
            grants.c.active.is_(True))).scalar_one()
    from lzo.auth.schemas import GrantDetail
    with s.transaction() as uow:
        editor = uow.session.execute(select(grants).where(grants.c.id == editor_id)).mappings().one()
    s.revoke(GrantDetail(**editor))
    s.revoke(grant)
    with s.transaction() as uow:
        with pytest.raises(ApiError) as error:
            ArtifactService(uow, s.store, s.limits).get(s.actor("alice"), ref.version_id)
        assert error.value.status == 404
        with pytest.raises(ApiError) as history_error:
            HistoryService(uow).get(s.actor("alice"), s.ids_by_name["object_a"], 2)
        assert history_error.value.status == 404


def test_user_and_scope_quota_charge_each_version_even_on_dedup(artifacts):
    from lzo.artifacts.service import ArtifactService

    s = artifacts
    s.limits = s.limits.model_copy(update={"artifact_user_quota_bytes": 8,
                                           "artifact_scope_quota_bytes": 8})
    first = s.upload("alice", "object_a", b"12345")
    assert first.size_bytes == 5
    with pytest.raises(ApiError) as error:
        s.upload("alice", "object_a", b"12345")
    assert error.value.status == 413
    with s.transaction() as uow:
        assert len(uow.session.execute(select(ArtifactService.versions_table)).all()) == 1


def test_version_review_and_link_are_append_only_without_entity_acceptance(artifacts):
    from lzo.artifacts.service import ArtifactService
    from lzo.governance.history import HistoryService

    s = artifacts
    first = s.upload("alice", "object_a", b"v1")
    second = s.upload("alice", "object_a", b"v2", previous_version_id=first.version_id)
    with s.transaction() as uow:
        service = ArtifactService(uow, s.store, s.limits)
        detail = service.get(s.actor("alice"), second.version_id)
        assert detail.previous_version_id == first.version_id
        assert detail.version_number == 2
        review = service.review(s.actor("alice"), second.version_id, "accepted", "readable", detail.revision)
        link = service.link(s.actor("alice"), second.version_id, s.ids_by_name["object_a"],
                            "supports", review.revision)
        uow.commit()
    with s.transaction() as uow:
        service = ArtifactService(uow, s.store, s.limits)
        detail = service.get(s.actor("alice"), second.version_id)
        assert len(detail.reviews) == 1 and detail.reviews[0].decision == "accepted"
        assert len(detail.links) == 1 and detail.links[0].relation == "supports"
        # Artifact review does not alter the source entity revision or accept it.
        assert HistoryService(uow).get(s.actor("alice"), s.ids_by_name["object_a"], 3).ref.revision == 3


def test_store_rejects_untrusted_ref_path_and_tampered_content(artifacts):
    from lzo.contracts import BlobRef
    store = artifacts.store
    with pytest.raises(ApiError):
        store.open(BlobRef("../secret", 1, "application/pdf", uuid4()))
    ref = store.put(io.BytesIO(b"safe"), "application/pdf")
    with pytest.raises(ApiError):
        store.open(BlobRef(ref.sha256, 5, ref.media_type, ref.version_id))


@pytest.fixture
def artifact_http(artifacts, tmp_path):
    from fastapi.testclient import TestClient
    from lzo.auth.session import SESSION_COOKIE, SessionService
    from lzo.main import create_app

    s = artifacts
    settings = s.limits.model_copy(update={"file_root": tmp_path / "http-private"})
    app = create_app(settings)
    app.state.engine.dispose()
    app.state.engine = s.app
    cookies = {}
    with s.transaction() as uow:
        service = SessionService(uow, settings)
        for name in ("alice", "admin"):
            cookie = service.create(s.users[name], {"access_token": "synthetic"})
            from lzo.auth.session import digest
            from lzo.auth.models import sessions
            csrf = uow.session.execute(select(sessions.c.csrf_token).where(
                sessions.c.id_hash == digest(cookie))).scalar_one()
            cookies[name] = (cookie, csrf)
        uow.commit()
    with TestClient(app, base_url="https://testserver") as client:
        def headers(name="alice", *, key="upload-one", revision=1):
            cookie, csrf = cookies[name]
            return {"Cookie": f"{SESSION_COOKIE}={cookie}", "X-CSRF-Token": csrf,
                    "Origin": settings.public_origin, "Idempotency-Key": key,
                    "If-Match": f'"{revision}"'}
        s.http = client
        s.http_settings = settings
        s.headers = headers
        yield s


def test_http_upload_download_and_same_bytes_cross_scope_are_private(artifact_http):
    s = artifact_http
    s.grant("admin", "editor", scope="farm_b")
    content = b"%PDF-1.7\nsecret\n%%EOF"
    url_a = f"/api/v1/artifacts?resource_id={s.ids_by_name['object_a']}&filename=original.pdf"
    url_b = f"/api/v1/artifacts?resource_id={s.ids_by_name['object_b']}&filename=original.pdf"
    first = s.http.post(url_a, content=content, headers={**s.headers(), "Content-Type": "application/pdf"})
    assert first.status_code == 201, first.text
    second = s.http.post(url_b, content=content, headers={**s.headers("admin", key="upload-b"),
        "Content-Type": "application/pdf"})
    assert second.status_code == 201, second.text
    a, b = first.json(), second.json()
    assert a["sha256"] == b["sha256"] and a["version_id"] != b["version_id"]
    own = s.http.get(f"/api/v1/artifacts/{a['version_id']}/content", headers=s.headers())
    assert own.status_code == 200 and own.content == content
    assert own.headers["content-disposition"].startswith("attachment;")
    assert own.headers["x-content-type-options"] == "nosniff"
    assert s.http.get(f"/api/v1/artifacts/{b['version_id']}", headers=s.headers()).status_code == 404
    assert s.http.get(f"/api/v1/artifacts/{b['version_id']}/content", headers=s.headers()).status_code == 404
    assert s.http.get("/api/v1/artifacts/../secret/content", headers=s.headers()).status_code in {400, 404, 422}


def test_http_upload_replay_and_preconditions_do_not_duplicate_versions(artifact_http):
    from lzo.artifacts.models import artifact_versions

    s = artifact_http
    url = f"/api/v1/artifacts?resource_id={s.ids_by_name['object_a']}&filename=field.png"
    headers = {**s.headers(), "Content-Type": "image/png",
               "X-Artifact-Metadata": json.dumps({"source": "camera"})}
    assert s.http.post(url, content=b"image", headers={k: v for k, v in headers.items()
        if k != "If-Match"}).status_code == 428
    first = s.http.post(url, content=b"image", headers=headers)
    assert first.status_code == 201, first.text
    replay = s.http.post(url, content=b"image", headers=headers)
    assert replay.status_code == 201 and replay.json() == first.json()
    mismatch = s.http.post(url, content=b"other", headers=headers)
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "IDEMPOTENCY_CONFLICT"
    stale = s.http.post(url, content=b"new", headers={**headers, "Idempotency-Key": "stale"})
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    with s.transaction() as uow:
        assert len(uow.session.execute(select(artifact_versions)).all()) == 1


def test_http_revalidates_revoked_rights_after_streaming(artifact_http):
    from lzo.artifacts.models import artifact_versions
    from lzo.auth.models import grants
    from lzo.auth.schemas import GrantDetail

    s = artifact_http
    url = f"/api/v1/artifacts?resource_id={s.ids_by_name['object_a']}&filename=revoked.pdf"
    def chunks():
        yield b"part-one"
        with s.transaction() as uow:
            row = uow.session.execute(select(grants).where(
                grants.c.user_id == s.users["alice"], grants.c.role_id == s.roles["editor"],
                grants.c.active.is_(True))).mappings().one()
        s.revoke(GrantDetail(**row))
        yield b"part-two"
    response = s.http.post(url, content=chunks(), headers={**s.headers(),
        "Content-Type": "application/pdf"})
    assert response.status_code in {403, 404}
    with s.transaction() as uow:
        assert uow.session.execute(select(artifact_versions)).all() == []
    assert list((s.http_settings.file_root / "artifacts" / "staging").iterdir()) == []
    assert [p for p in (s.http_settings.file_root / "artifacts" / "objects").rglob("*") if p.is_file()] == []


def test_concurrent_users_cannot_exceed_one_scope_quota(artifacts):
    from lzo.artifacts.service import ArtifactService

    s = artifacts
    s.grant("admin", "editor", scope="farm_a")
    s.limits = s.limits.model_copy(update={"artifact_user_quota_bytes": 100,
                                           "artifact_scope_quota_bytes": 8})
    ready = Barrier(2)
    def upload(name, content):
        blob = s.store.put(io.BytesIO(content), "application/pdf")
        ready.wait(timeout=10)
        try:
            with s.transaction() as uow:
                result = ArtifactService(uow, s.store, s.limits).register_upload(
                    s.actor(name), s.ids_by_name["object_a"], blob,
                    filename="same-scope.pdf", metadata={})
                uow.commit()
                return result.version_id
        except ApiError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: upload(*args),
                                [("alice", b"aaaaa"), ("admin", b"bbbbb")]))
    assert sum(result == "ARTIFACT_QUOTA_EXCEEDED" for result in results) == 1
    assert sum(result != "ARTIFACT_QUOTA_EXCEEDED" for result in results) == 1


def test_http_review_and_link_replay_preserve_revision_and_hide_revoked_target(artifact_http):
    from lzo.auth.models import grants
    from lzo.auth.schemas import GrantDetail

    s = artifact_http
    s.grant("alice", "editor", scope="farm_b")
    url = f"/api/v1/artifacts?resource_id={s.ids_by_name['object_a']}&filename=review.pdf"
    upload = s.http.post(url, content=b"evidence", headers={**s.headers(),
        "Content-Type": "application/pdf"})
    assert upload.status_code == 201
    version_id = upload.json()["version_id"]
    review_url = f"/api/v1/artifacts/{version_id}/reviews"
    review = s.http.post(review_url, json={"decision": "accepted", "comment": "legible"},
                         headers=s.headers(key="review-one"))
    assert review.status_code == 201 and review.json()["revision"] == 2
    replay = s.http.post(review_url, json={"decision": "accepted", "comment": "legible"},
                         headers=s.headers(key="review-one"))
    assert replay.status_code == 201 and replay.json() == review.json()
    link_url = f"/api/v1/artifacts/{version_id}/links"
    link = s.http.post(link_url, json={"target_resource_id": str(s.ids_by_name["object_b"]),
        "relation": "supports"}, headers=s.headers(key="link-one", revision=2))
    assert link.status_code == 201 and link.json()["revision"] == 3
    stale = s.http.post(link_url, json={"target_resource_id": str(s.ids_by_name["object_b"]),
        "relation": "supports"}, headers=s.headers(key="link-two", revision=2))
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    detail = s.http.get(f"/api/v1/artifacts/{version_id}", headers=s.headers())
    assert detail.status_code == 200 and len(detail.json()["links"]) == 1
    with s.transaction() as uow:
        row = uow.session.execute(select(grants).where(
            grants.c.user_id == s.users["alice"], grants.c.role_id == s.roles["editor"],
            grants.c.scope_id == s.scopes["farm_b"], grants.c.active.is_(True))).mappings().one()
    s.revoke(GrantDetail(**row))
    hidden = s.http.get(f"/api/v1/artifacts/{version_id}", headers=s.headers())
    assert hidden.status_code == 200 and hidden.json()["links"] == []


def test_attach_evidence_ref_cannot_cross_registered_scope(artifacts):
    from lzo.artifacts.service import attach_evidence
    from lzo.contracts import BlobRef

    s = artifacts
    s.grant("admin", "editor", scope="farm_b")
    detail = s.upload("alice", "object_a", b"farm-a-secret")
    ref = BlobRef(detail.sha256, detail.size_bytes, detail.media_type, detail.version_id)
    with s.transaction() as uow:
        with pytest.raises(ApiError) as error:
            attach_evidence(uow, s.actor("admin"), s.ids_by_name["object_b"], ref, {})
        assert error.value.status in {403, 404, 409}


def test_artifact_openapi_has_stable_operations_and_typed_failures(artifact_http):
    paths = artifact_http.http.get("/openapi.json").json()["paths"]
    operations = [paths["/api/v1/artifacts"]["post"],
                  paths["/api/v1/artifacts/{version_id}"]["get"],
                  paths["/api/v1/artifacts/{version_id}/content"]["get"],
                  paths["/api/v1/artifacts/{version_id}/links"]["post"],
                  paths["/api/v1/artifacts/{version_id}/reviews"]["post"]]
    assert [operation["operationId"] for operation in operations] == [
        "uploadArtifact", "getArtifact", "downloadArtifact", "linkArtifact", "reviewArtifact"]
    upload = operations[0]
    assert upload["requestBody"]["content"]["application/octet-stream"]["schema"]["format"] == "binary"
    for operation in operations:
        assert "401" in operation["responses"]
        assert "404" in operation["responses"]
        assert "ApiErrorDetail" in operation["responses"]["404"]["content"]["application/json"]["schema"]["$ref"]


def test_attach_evidence_replay_returns_original_revision(artifacts):
    from lzo.artifacts.service import attach_evidence
    from lzo.contracts import BlobRef
    from lzo.governance.history import HistoryService

    s = artifacts
    detail = s.upload("alice", "object_a", b"once")
    blob = BlobRef(detail.sha256, detail.size_bytes, detail.media_type, detail.version_id)
    with s.transaction() as uow:
        first = attach_evidence(uow, s.actor("alice"), s.ids_by_name["object_a"], blob,
                                {"source": "synthetic acceptance"})
        uow.commit()
    with s.transaction() as uow:
        second = attach_evidence(uow, s.actor("alice"), s.ids_by_name["object_a"], blob,
                                 {"source": "synthetic acceptance"})
        assert first == second
        with pytest.raises(ApiError) as error:
            attach_evidence(uow, s.actor("alice"), s.ids_by_name["object_a"], blob,
                            {"source": "changed"})
        assert error.value.status == 409
        with pytest.raises(ApiError):
            HistoryService(uow).get(s.actor("alice"), s.ids_by_name["object_a"], 3)


def test_download_does_not_hold_authorization_lock_while_streaming(artifact_http, monkeypatch):
    from lzo.artifacts.store import ArtifactStore

    s = artifact_http
    view_grant = s.grant("alice", "viewer", scope="farm_a")
    url = f"/api/v1/artifacts?resource_id={s.ids_by_name['object_a']}&filename=slow.pdf"
    upload = s.http.post(url, content=b"read while revoked", headers={**s.headers(),
        "Content-Type": "application/pdf"})
    assert upload.status_code == 201
    version_id = upload.json()["version_id"]
    original_open = ArtifactStore.open

    def open_with_revocation(store, blob):
        stream = original_open(store, blob)
        class Stream:
            def read(self, size):
                if not hasattr(self, "checked"):
                    self.checked = True
                    done = Event()
                    def revoke():
                        try:
                            s.revoke(view_grant)
                        finally:
                            done.set()
                    Thread(target=revoke, daemon=True).start()
                    assert done.wait(3), "download held the authorization lock during transfer"
                return stream.read(size)
            def close(self):
                stream.close()
        return Stream()

    monkeypatch.setattr(ArtifactStore, "open", open_with_revocation)
    response = s.http.get(f"/api/v1/artifacts/{version_id}/content", headers=s.headers())
    assert response.status_code == 200 and response.content == b"read while revoked"


def test_history_detail_and_diff_hide_revoked_link_target(artifacts):
    from lzo.artifacts.service import ArtifactService
    from lzo.governance.history import HistoryQuery, HistoryService
    from lzo.governance.models import revisions

    s = artifacts
    target_grant = s.grant("alice", "editor", scope="farm_b")
    detail = s.upload("alice", "object_a", b"linked evidence")
    with s.transaction() as uow:
        ArtifactService(uow, s.store, s.limits).link(s.actor("alice"), detail.version_id,
            s.ids_by_name["object_b"], "supports", 1)
        uow.commit()
    with s.transaction() as uow:
        history = HistoryService(uow)
        assert history.get(s.actor("alice"), detail.version_id, 2).payload[
            "target_resource_id"] == str(s.ids_by_name["object_b"])
    s.revoke(target_grant)
    with s.transaction() as uow:
        history = HistoryService(uow)
        assert [ref.revision for ref in history.list(s.actor("alice"),
            HistoryQuery(resource_id=detail.version_id), None).items] == [1]
        with pytest.raises(ApiError) as hidden:
            history.get(s.actor("alice"), detail.version_id, 2)
        assert hidden.value.status == 404
        with pytest.raises(ApiError) as hidden_diff:
            history.diff(s.actor("alice"), detail.version_id, 1, 2)
        assert hidden_diff.value.status == 404
        raw = uow.session.execute(select(revisions.c.payload).where(
            revisions.c.resource_id == detail.version_id,
            revisions.c.revision == 2)).scalar_one()
        assert raw["target_resource_id"] == str(s.ids_by_name["object_b"])


def test_concurrent_uploads_with_same_if_match_have_one_winner(artifacts, monkeypatch):
    from lzo.artifacts.models import artifact_quotas, artifact_versions
    from lzo.artifacts.service import ArtifactService

    s = artifacts
    gate = Barrier(2)
    original_charge = ArtifactService._charge
    def charge_after_both_checked(self, kind, owner_id, size, limit):
        if kind == "user":
            # A ledger-first implementation serializes before this hook;
            # the first caller then proceeds when the short barrier expires.
            try:
                gate.wait(timeout=1)
            except BrokenBarrierError:
                pass
        return original_charge(self, kind, owner_id, size, limit)
    monkeypatch.setattr(ArtifactService, "_charge", charge_after_both_checked)

    def upload(content):
        blob = s.store.put(io.BytesIO(content), "application/pdf")
        try:
            with s.transaction() as uow:
                detail = ArtifactService(uow, s.store, s.limits).register_upload(
                    s.actor("alice"), s.ids_by_name["object_a"], blob,
                    filename="concurrent.pdf", metadata={}, expected_resource_revision=1)
                uow.commit()
                return detail.version_id
        except ApiError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, (b"one", b"two")))
    assert sum(result == "REVISION_CONFLICT" for result in results) == 1
    assert sum(result != "REVISION_CONFLICT" for result in results) == 1
    with s.transaction() as uow:
        rows = uow.session.execute(select(artifact_versions.c.size_bytes)).scalars().all()
        quota = uow.session.execute(select(artifact_quotas.c.used_bytes).where(
            artifact_quotas.c.kind == "user", artifact_quotas.c.owner_id == s.users["alice"])).scalar_one()
    assert len(rows) == 1 and quota == rows[0]


def test_public_put_attach_registers_in_caller_transaction(artifacts):
    from lzo.artifacts.models import artifact_quotas, artifact_versions
    from lzo.artifacts.service import attach_evidence
    from lzo.governance.history import HistoryService

    s = artifacts
    blob = s.store.put(io.BytesIO(b"fresh-public-protocol"), "application/pdf")
    with s.transaction() as uow:
        uow.artifact_store = s.store
        uow.artifact_settings = s.limits
        ref = attach_evidence(uow, s.actor("alice"), s.ids_by_name["object_a"], blob,
                              {"filename": "public.pdf", "source": "field"})
        assert ref.revision == 2
        assert uow.session.execute(select(artifact_versions.c.id).where(
            artifact_versions.c.id == blob.version_id)).scalar_one() == blob.version_id
        # No commit: the caller controls the transaction.
    with s.transaction() as uow:
        assert uow.session.execute(select(artifact_versions.c.id).where(
            artifact_versions.c.id == blob.version_id)).first() is None
    with s.transaction() as uow:
        uow.artifact_store = s.store
        uow.artifact_settings = s.limits
        ref = attach_evidence(uow, s.actor("alice"), s.ids_by_name["object_a"], blob,
                              {"filename": "public.pdf", "source": "field"})
        uow.commit()
    with s.transaction() as uow:
        history = HistoryService(uow).get(s.actor("alice"), s.ids_by_name["object_a"], 2)
        assert history.evidence_refs == [blob.version_id]
        assert ref.event_id == history.ref.event_id
        assert uow.session.execute(select(artifact_quotas.c.used_bytes).where(
            artifact_quotas.c.kind == "user", artifact_quotas.c.owner_id == s.users["alice"])).scalar_one() == blob.size_bytes


def test_public_attach_rejects_missing_content_before_registration(artifacts):
    from lzo.artifacts.models import artifact_versions
    from lzo.artifacts.service import attach_evidence
    from lzo.contracts import BlobRef

    s = artifacts
    forged = BlobRef("a" * 64, 5, "application/pdf", uuid4())
    with s.transaction() as uow:
        uow.artifact_store = s.store
        with pytest.raises(ApiError) as error:
            attach_evidence(uow, s.actor("alice"), s.ids_by_name["object_a"], forged, {})
        assert error.value.code == "BLOB_MISSING"
    with s.transaction() as uow:
        assert uow.session.execute(select(artifact_versions.c.id)).all() == []


def test_composed_ledger_then_upload_cannot_deadlock_quota(artifacts, monkeypatch):
    from lzo.artifacts.models import artifact_versions
    from lzo.artifacts.service import ArtifactService
    from lzo.governance.revisions import append_revision

    s = artifacts
    a_has_ledger = Event()
    b_started = Event()
    original_charge = ArtifactService._charge
    def signal_quota(self, kind, owner_id, size, limit):
        result = original_charge(self, kind, owner_id, size, limit)
        if kind == "user" and getattr(self, "_test_competing", False):
            b_started.set()
        return result
    monkeypatch.setattr(ArtifactService, "_charge", signal_quota)
    blob_a = s.store.put(io.BytesIO(b"caller-a"), "application/pdf")
    blob_b = s.store.put(io.BytesIO(b"caller-b"), "application/pdf")

    def competing_upload():
        a_has_ledger.wait(timeout=10)
        try:
            with s.transaction() as uow:
                service = ArtifactService(uow, s.store, s.limits)
                service._test_competing = True
                service.register_upload(s.actor("alice"), s.ids_by_name["object_a"],
                    blob_b, filename="b.pdf", metadata={})
                uow.commit()
            return "committed"
        except Exception as error:
            return type(error).__name__

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(competing_upload)
        with s.transaction() as uow:
            append_revision(uow, s.actor("alice"), s.ids_by_name["object_a"], 1,
                {"caller": "a"}, "object.edited")
            a_has_ledger.set()
            # Before the fix B can lock quota and wait for A's ledger. After
            # the fix B waits for the ledger before reaching quota.
            b_started.wait(0.5)
            ArtifactService(uow, s.store, s.limits).register_upload(
                s.actor("alice"), s.ids_by_name["object_a"], blob_a,
                filename="a.pdf", metadata={})
            uow.commit()
        assert future.result(timeout=10) == "committed"
    with s.transaction() as uow:
        assert len(uow.session.execute(select(artifact_versions.c.id)).all()) == 2


def test_visibility_migration_backfills_existing_raw_link_events(settings):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, insert, text
    from lzo.auth.models import resource_scopes
    from lzo.contracts import ActorContext
    from lzo.db import PgUnitOfWork
    from lzo.governance.revisions import append_revision

    root = Path(__file__).resolve().parents[3]
    schema = "f04_backfill_" + uuid4().hex
    owner = create_engine(settings.migration_database_url)
    app = create_engine(settings.database_url, connect_args={"options": f"-csearch_path={schema}"})
    with owner.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION lzo_owner'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO lzo_app'))
    config = Config(str(root / "database/alembic.ini"))
    config.attributes.update(schema=schema, connection_url=settings.migration_database_url)
    try:
        command.upgrade(config, "0003_artifacts")
        artifact_id, target_id = uuid4(), uuid4()
        actor = ActorContext(uuid4(), "backfill", 1)
        with PgUnitOfWork(app) as uow:
            uow.session.execute(insert(resource_scopes).values(resource_id=artifact_id,
                security_class="data", resource_kind="artifact", related_scope_ids=[]))
            uow.session.execute(insert(resource_scopes).values(resource_id=target_id,
                security_class="data", resource_kind="object", related_scope_ids=[]))
            append_revision(uow, actor, artifact_id, 0, {"kind": "artifact"}, "artifact.uploaded")
            linked = append_revision(uow, actor, artifact_id, 1,
                {"target_resource_id": str(target_id), "relation": "supports"}, "artifact.linked")
            uow.commit()
        command.upgrade(config, "head")
        with PgUnitOfWork(app) as uow:
            found = uow.session.execute(text("""SELECT resource_id FROM revision_visibility_dependencies
                WHERE event_id = :event_id"""), {"event_id": linked.event_id}).scalar_one()
            assert found == target_id
    finally:
        app.dispose()
        with owner.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        owner.dispose()
