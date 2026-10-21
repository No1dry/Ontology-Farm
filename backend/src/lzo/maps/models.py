"""Current map projection and explicit artifact reference dependencies."""
from sqlalchemy import Column, ForeignKey, Integer, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID

from lzo.auth.models import metadata

map_resources = Table('map_resources', metadata,
    Column('id', UUID(as_uuid=True), ForeignKey('resource_scopes.resource_id'), primary_key=True),
    Column('revision', Integer, nullable=False), Column('payload', JSONB, nullable=False))
map_artifacts = Table('map_artifacts', metadata,
    Column('resource_id', UUID(as_uuid=True), ForeignKey('map_resources.id'), primary_key=True),
    Column('version_id', UUID(as_uuid=True), ForeignKey('artifact_versions.id'), primary_key=True))
