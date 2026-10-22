"""Datum-explicit positions and artifact-backed spatial asset registration."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PositionInput(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    observed_at: datetime
    horizontal_datum: Literal["EPSG:4326","EPSG:4490"]
    longitude: float=Field(ge=-180,le=180)
    latitude: float=Field(ge=-90,le=90)
    height_metres: float | None=None
    vertical_datum: str | None=None
    accuracy_metres: float | None=Field(default=None,ge=0)

    @model_validator(mode="after")
    def datum(self):
        if self.observed_at.utcoffset() is None:
            raise ValueError("Observation time requires timezone")
        if self.height_metres is not None and not self.vertical_datum:
            raise ValueError("Known height requires vertical datum")
        return self


class SpatialAssetInput(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    resource_id: UUID
    expected_revision: int=Field(ge=1)
    asset_id: UUID
    artifact_version_id: UUID
    sha256: str=Field(pattern=r"^[0-9a-f]{64}$")
    footprint: dict
    horizontal_datum: str=Field(min_length=1)
    vertical_datum: str | None=None
    captured_at: datetime
    accuracy_metres: float | None=Field(default=None,ge=0)
    source: str=Field(min_length=1)
    quality: Literal["unverified","display_only","verified"]="unverified"
    survey_evidence_version_id: UUID | None=None

    @model_validator(mode="after")
    def survey(self):
        if self.captured_at.utcoffset() is None:
            raise ValueError("Capture time requires timezone")
        if self.quality=="verified" and (not self.survey_evidence_version_id or self.accuracy_metres is None):
            raise ValueError("Verified assets require survey evidence and stated accuracy")
        return self


class LocationObservationInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    resource_id: UUID
    expected_revision: int=Field(ge=1)
    observation_id: UUID
    position: PositionInput
    source: str=Field(min_length=1)


class SpatialAssetRequest(BaseModel):
    asset: SpatialAssetInput
    bundle_id: UUID


class LocationObservationRequest(BaseModel):
    observation: LocationObservationInput
    bundle_id: UUID
