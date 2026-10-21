"""Audited platform authorization administration and Keycloak user operations."""
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update

from lzo.auth import models
from lzo.auth.permissions import require_permission
from lzo.auth.schemas import GroupDetail, GrantDetail, MembersDetail, RoleDetail, UserDetail
from lzo.contracts import ResourceScope
from lzo.errors import ApiError, Conflict
from lzo.governance.revisions import append_revision, lock_ledger


class AuthorizationAdmin:
    def __init__(self, uow):
        self.uow = uow

    def _authorize(self, actor):
        # Acquire exclusive version lock first, avoiding concurrent lock upgrades.
        self.uow.session.execute(select(models.authorization_state).with_for_update()).one()
        require_permission(actor, "identity.manage", ResourceScope(None, None, (), "identity"), uow=self.uow)
        lock_ledger(self.uow)

    def _audit(self, actor, resource_id, expected, payload, event, *, approval_id=None):
        if approval_id:
            payload = {**payload, "approval_id": str(approval_id)}
        ref = append_revision(self.uow, actor, resource_id, expected, payload, event)
        if approval_id:
            from lzo.governance.approvals import dependencies
            from lzo.governance.history import revision_visibility_dependencies, revision_metadata
            targets = set(self.uow.session.execute(select(dependencies.c.resource_id).where(
                dependencies.c.approval_id == approval_id)).scalars()) | {approval_id}
            self.uow.session.execute(insert(revision_visibility_dependencies), [
                {"event_id": ref.event_id, "resource_id": target} for target in targets])
            self.uow.session.execute(insert(revision_metadata).values(event_id=ref.event_id,
                evidence_refs=[str(approval_id)], proof_refs=[]))
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        self.uow.session.execute(pg_insert(models.resource_scopes).values(
            resource_id=resource_id, security_class="identity", resource_kind=event.split(".")[0])
            .on_conflict_do_nothing())
        self.uow.session.execute(update(models.authorization_state).values(
            version=models.authorization_state.c.version + 1))

    def _existing(self, table, resource_id, expected):
        row = self.uow.session.execute(select(table).where(table.c.id == resource_id).with_for_update()).mappings().one_or_none()
        if row is None:
            raise ApiError("NOT_FOUND", 404, "Resource not found")
        if row["revision"] != expected:
            raise Conflict("REVISION_CONFLICT", "Resource revision has changed")
        return row

    def create_group(self, actor, body):
        self._authorize(actor)
        result = GroupDetail(id=uuid4(), revision=1, **body.model_dump())
        self.uow.session.execute(insert(models.groups).values(**result.model_dump()))
        self._audit(actor, result.id, 0, result.model_dump(mode="json"), "group.created")
        return result

    def update_group(self, actor, resource_id, expected, body):
        self._authorize(actor)
        self._existing(models.groups, resource_id, expected)
        result = GroupDetail(id=resource_id, revision=expected + 1, **body.model_dump())
        self.uow.session.execute(update(models.groups).where(models.groups.c.id == resource_id).values(**body.model_dump(), revision=result.revision))
        self._audit(actor, resource_id, expected, result.model_dump(mode="json"), "group.updated")
        return result

    def set_members(self, actor, group_id, expected, user_ids, *, approval_id=None):
        self._authorize(actor)
        from lzo.auth.approval_policy import gate, record_footprints
        change = gate(self.uow, actor, "identity.group.members", [group_id],
            {"group_id": str(group_id), "expected_revision": expected,
             "user_ids": sorted(str(x) for x in set(user_ids))}, approval_id)
        self._existing(models.groups, group_id, expected)
        unique = sorted(set(user_ids))
        found = set(self.uow.session.execute(select(models.users.c.id).where(models.users.c.id.in_(unique))).scalars())
        if found != set(unique):
            raise ApiError("NOT_FOUND", 404, "User not found")
        self.uow.session.execute(delete(models.group_members).where(models.group_members.c.group_id == group_id))
        if unique:
            self.uow.session.execute(insert(models.group_members), [{"group_id": group_id, "user_id": value} for value in unique])
        self.uow.session.execute(update(models.groups).where(models.groups.c.id == group_id).values(revision=expected + 1))
        result = MembersDetail(id=group_id, revision=expected + 1, user_ids=unique)
        self._audit(actor, group_id, expected, result.model_dump(mode="json"), "group.members_changed",
                    approval_id=approval_id)
        record_footprints(self.uow, [UUID(x) for x in change.impact["governed_user_ids"]], change.command)
        return result

    def create_role(self, actor, body):
        self._authorize(actor)
        result = RoleDetail(id=uuid4(), revision=1, **body.model_dump(exclude={"approval_id"}))
        self.uow.session.execute(insert(models.roles).values(**result.model_dump(exclude={"approval_id"})))
        self._audit(actor, result.id, 0, result.model_dump(mode="json"), "role.created")
        return result

    def update_role(self, actor, resource_id, expected, body):
        self._authorize(actor)
        from lzo.auth.approval_policy import gate, record_footprints
        change = gate(self.uow, actor, "identity.role.update", [resource_id],
            {"role_id": str(resource_id), "expected_revision": expected,
             "body": body.model_dump(mode="json", exclude={"approval_id"})}, body.approval_id)
        self._existing(models.roles, resource_id, expected)
        result = RoleDetail(id=resource_id, revision=expected + 1, **body.model_dump(exclude={"approval_id"}))
        self.uow.session.execute(update(models.roles).where(models.roles.c.id == resource_id).values(
            **body.model_dump(exclude={"approval_id"}), revision=result.revision))
        self._audit(actor, resource_id, expected, result.model_dump(mode="json"), "role.updated",
                    approval_id=body.approval_id)
        record_footprints(self.uow, [UUID(x) for x in change.impact["governed_user_ids"]], change.command)
        return result

    def create_grant(self, actor, body):
        self._authorize(actor)
        from lzo.auth.approval_policy import gate, record_footprints
        change = gate(self.uow, actor, "identity.grant.create", [body.role_id],
            body.model_dump(mode="json", exclude={"approval_id"}), body.approval_id)
        for table, value in ((models.roles, body.role_id), (models.users, body.user_id), (models.groups, body.group_id)):
            if value is not None and self.uow.session.execute(select(table.c.id).where(table.c.id == value)).first() is None:
                raise ApiError("NOT_FOUND", 404, "Grant reference not found")
        result = GrantDetail(id=uuid4(), revision=1, active=True, **body.model_dump(exclude={"approval_id"}))
        self.uow.session.execute(insert(models.grants).values(**result.model_dump(exclude={"approval_id"})))
        self._audit(actor, result.id, 0, result.model_dump(mode="json"), "grant.created",
                    approval_id=body.approval_id)
        record_footprints(self.uow, [UUID(x) for x in change.impact["governed_user_ids"]], change.command)
        return result

    def revoke_grant(self, actor, resource_id, expected):
        self._authorize(actor)
        row = dict(self._existing(models.grants, resource_id, expected))
        row.update(active=False, revision=expected + 1)
        self.uow.session.execute(update(models.grants).where(models.grants.c.id == resource_id).values(active=False, revision=expected + 1))
        result = GrantDetail(**row)
        self._audit(actor, resource_id, expected, result.model_dump(mode="json"), "grant.revoked")
        return result



class KeycloakAdmin:
    """Dedicated service account: manage-users only, never realm administration."""
    def __init__(self, settings):
        self.settings = settings

    def _request(self, method, path, **kwargs):
        import httpx
        from lzo.auth.oidc import tls_verify
        if not self.settings.identity_admin_client_secret or not self.settings.identity_admin_url:
            raise ApiError("IDENTITY_ADMIN_UNAVAILABLE", 503, "Identity administration is not configured")
        try:
            with httpx.Client(verify=tls_verify(self.settings), timeout=15) as client:
                token = client.post((self.settings.oidc_backchannel_base or self.settings.oidc_issuer) + "/protocol/openid-connect/token",
                    data={"grant_type": "client_credentials", "client_id": self.settings.identity_admin_client_id,
                          "client_secret": self.settings.identity_admin_client_secret})
                token.raise_for_status()
                response = client.request(method, self.settings.identity_admin_url.rstrip("/") + path,
                    headers={"Authorization": "Bearer " + token.json()["access_token"]}, **kwargs)
                response.raise_for_status()
                return response
        except (httpx.HTTPError, KeyError, ValueError):
            raise ApiError("IDENTITY_ADMIN_UNAVAILABLE", 503, "Identity provider operation failed") from None

    def ensure_invited_identity(self, operation_id, email, display_name):
        username = 'lzo-invite-' + str(operation_id)
        existing = self._request('GET', '/users', params={'username': username, 'exact': 'true'}).json()
        if existing:
            return existing[0]['id']
        response = self._request('POST', '/users', json={'username': username, 'email': email,
            'firstName': display_name, 'enabled': True, 'emailVerified': False,
            'requiredActions': ['VERIFY_EMAIL', 'UPDATE_PASSWORD']})
        return response.headers['Location'].rsplit('/', 1)[-1]

    def send_invitation(self, subject):
        from urllib.parse import quote
        subject = quote(subject, safe="")
        self._request('PUT', f'/users/{subject}/execute-actions-email',
            params={'client_id': self.settings.oidc_client_id, 'lifespan': 900,
                    'redirect_uri': self.settings.public_origin + '/api/v1/session/login'},
            json=['VERIFY_EMAIL', 'UPDATE_PASSWORD'])

    def set_enabled(self, subject, enabled):
        # URL segments come from verified provider subjects or provider Location.
        from urllib.parse import quote
        subject = quote(subject, safe="")
        self._request("PUT", f"/users/{subject}", json={"enabled": enabled})
        if not enabled:
            self._request("POST", f"/users/{subject}/logout")
