"""Map-owned public DTOs contain immutable artifact identities, never paths."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MapResourceCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=200)
    kind: Literal['online', 'mbtiles', 'pmtiles']
    tile_kind: Literal['raster', 'mvt']
    artifact_version_id: UUID | None = None
    style_version_id: UUID | None = None
    online_url_template: str | None = Field(default=None, max_length=512)
    bbox: tuple[float, float, float, float]
    min_zoom: int = Field(ge=0, le=24)
    max_zoom: int = Field(ge=0, le=24)
    attribution: str = Field(min_length=1, max_length=2000)
    license: str = Field(min_length=1, max_length=2000)
    asset_versions: dict[str, UUID] = Field(default_factory=dict, max_length=512)
    owner_scope_id: UUID

    @model_validator(mode='after')
    def coherent(self):
        w, s, e, n = self.bbox
        if not (-180 <= w < e <= 180 and -85.051129 <= s < n <= 85.051129) or self.min_zoom > self.max_zoom:
            raise ValueError('Invalid coverage')
        if self.kind == 'online':
            if not self.online_url_template or self.artifact_version_id is not None:
                raise ValueError('Online maps require a registered template without a package')
        elif self.artifact_version_id is None or self.online_url_template is not None:
            raise ValueError('Offline maps require an artifact version without an online URL')
        return self


class MapResourcePatch(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal['online', 'mbtiles', 'pmtiles'] | None = None
    tile_kind: Literal['raster', 'mvt'] | None = None
    artifact_version_id: UUID | None = None
    style_version_id: UUID | None = None
    online_url_template: str | None = Field(default=None, max_length=512)
    bbox: tuple[float, float, float, float] | None = None
    min_zoom: int | None = Field(default=None, ge=0, le=24)
    max_zoom: int | None = Field(default=None, ge=0, le=24)
    attribution: str | None = Field(default=None, min_length=1, max_length=2000)
    license: str | None = Field(default=None, min_length=1, max_length=2000)
    asset_versions: dict[str, UUID] | None = Field(default=None, max_length=512)
    owner_scope_id: UUID | None = None


class MapResourceView(MapResourceCreate):
    id: UUID
    revision: int
    validation_status: Literal['valid']


class MapResourcePage(BaseModel):
    items: list[MapResourceView]
    next_cursor: str | None
