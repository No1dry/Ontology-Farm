"""Scoped, authenticated encryption. Keys come only from deployment configuration.

Backups require source_credentials ciphertext plus separately controlled recovery
of source_encryption_key. A missing/wrong key fails closed, never uses session keys.
The resolver is an internal capability bound to an authorized connector task.
"""
import base64
import hashlib
import hmac
import json
import os
from uuid import UUID,uuid4
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import insert,select,update
from lzo.errors import ApiError,Conflict
from lzo.governance.revisions import append_revision,lock_ledger
from lzo.ingestion.models import source_credentials,sources
from lzo.ingestion.schemas import CredentialRef,SourceCredentialInput
from lzo.contracts import UnitOfWork,ActorContext
from lzo.ingestion.sources import SourceService,canonical


class SourceCredentialStore:
    def __init__(self,settings,uow=None,*,authorize=None):
        self.settings,self.uow,self.authorize=settings,uow,authorize

    def _key(self):
        try:
            key=base64.b64decode(self.settings.source_encryption_key or '',altchars=b'-_',validate=True)
            if len(key)!=32:raise ValueError()
            return key
        except (ValueError,TypeError):
            raise ApiError('SOURCE_KEY_UNAVAILABLE',503,'Source credential key is unavailable') from None

    def command_digest(self,source_id,command):
        value={'operation':'source.credentials','source_id':str(source_id),'expected':command.expected_revision,
               'username':command.username,'password':command.password.get_secret_value()}
        return hmac.new(self._key(),b'credential-command-v1\0'+canonical(value).encode(),hashlib.sha256).hexdigest()

    def set(self,uow:UnitOfWork,actor:ActorContext,source_id:UUID,command:SourceCredentialInput)->CredentialRef:
        SourceService(uow).read(actor,source_id,'sources.credentials.manage')
        lock_ledger(uow)
        source=SourceService(uow).read(actor,source_id,'sources.credentials.manage')
        if source.revision!=command.expected_revision:raise Conflict('REVISION_CONFLICT','Source changed')
        ref=str(uuid4());revision=source.revision+1
        aad=f'{source_id}:{ref}:{revision}'.encode();nonce=os.urandom(12)
        raw=canonical([command.username,command.password.get_secret_value()]).encode()
        ciphertext=nonce+AESGCM(self._key()).encrypt(nonce,raw,aad)
        uow.session.execute(insert(source_credentials).values(ref=ref,source_id=source_id,revision=revision,ciphertext=ciphertext))
        result=CredentialRef(ref=ref,source_id=source_id,revision=revision)
        event=append_revision(uow,actor,source_id,source.revision,result.model_dump(mode='json'),'source.credentials.changed')
        uow.session.execute(update(sources).where(sources.c.id==source_id).values(revision=event.revision))
        return result

    def resolve(self,credential_ref:str)->tuple[str,SecretStr]:
        if self.authorize is None or self.uow is None:raise ApiError('CREDENTIAL_CONTEXT_REQUIRED',403,'Trusted connector context required')
        source_id=self.authorize() # fresh scoped task/lease authorization BEFORE every decryption
        row=self.uow.session.execute(select(source_credentials).where(source_credentials.c.ref==credential_ref,
            source_credentials.c.source_id==source_id)).mappings().first()
        if row is None:raise ApiError('CREDENTIAL_NOT_FOUND',404,'Source credential unavailable')
        try:
            encrypted=row['ciphertext'];aad=f"{source_id}:{row['ref']}:{row['revision']}".encode()
            username,password=json.loads(AESGCM(self._key()).decrypt(encrypted[:12],encrypted[12:],aad))
            return username,SecretStr(password)
        except ApiError:raise
        except Exception:raise ApiError('SOURCE_KEY_INVALID',503,'Source credential recovery failed') from None


from lzo.semantic.projection import register_nonsemantic
register_nonsemantic('source.credentials.changed','ingestion-v1')
