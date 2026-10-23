"""One acceptance contract for static files and incremental replay."""
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator, model_serializer
from lzo.ingestion.schemas import DTO, DataNature
from lzo.contracts import RevisionRef, ResourceScope
from lzo.gis.schemas import PositionInput


class Supersedes(DTO):
    source_record_id: str = Field(min_length=1, max_length=256)
    version: int = Field(ge=1)


class MeasurementMethod(DTO):
    name: str = Field(min_length=1, max_length=256)
    version: str | None = Field(default=None, max_length=256)


class Calibration(DTO):
    version: str = Field(min_length=1, max_length=256)
    method: str | None = Field(default=None, max_length=256)


class TimeQuality(DTO):
    state: Literal['synchronized', 'estimated', 'unreliable', 'unknown']
    uncertainty_ms: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class Resolution(DTO):
    width: int = Field(gt=0, le=100000)
    height: int = Field(gt=0, le=100000)


class MediaGap(DTO):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode='after')
    def increasing(self):
        if self.end <= self.start:
            raise ValueError('Gap end must follow start')
        return self


class MediaMetadata(DTO):
    encoding: str | None = Field(default=None, max_length=128)
    resolution: Resolution | None = None
    sequence: int | None = Field(default=None, ge=0)
    gaps: tuple[MediaGap, ...] | None = Field(default=None, max_length=1000)
    verification: Literal['verified', 'unverified', 'failed', 'unknown'] | None = None


class ObservationInput(DTO):
    kind: Literal['observation', 'event', 'media', 'track'] = 'observation'
    source_record_id: str = Field(min_length=1, max_length=256)
    version: int = Field(default=1, ge=1)
    operation: Literal['upsert', 'withdraw'] = 'upsert'
    supersedes: Supersedes | None = None
    object_id: UUID
    device_id: UUID
    point_id: str = Field(min_length=1, max_length=256)
    metric_id: str | None = Field(default=None, min_length=1, max_length=256)
    raw_value: str | None = Field(max_length=4096)
    raw_unit: str | None = Field(default=None, max_length=128)
    normalized_value: Decimal | None = Field(allow_inf_nan=False, max_digits=100, decimal_places=40)
    normalized_unit: str | None = Field(default=None, max_length=128)
    sampled_at: AwareDatetime
    reported_at: AwareDatetime | None = None
    quality: Literal['valid', 'suspect', 'invalid', 'unknown'] = 'valid'
    clock_basis: Literal['device', 'server', 'imported'] = 'device'
    data_nature: DataNature
    evidence_version_ids: tuple[UUID, ...] = Field(default=(), max_length=32)
    artifact_version_id: UUID | None = None
    location_ref: RevisionRef | None = None
    device_binding_ref: RevisionRef | None = None
    model_ref: RevisionRef | None = None
    channel_id: str | None = Field(default=None, max_length=256)
    end_at: AwareDatetime | None = None
    mission_id: str | None = Field(default=None, max_length=256)
    position: PositionInput | None = None
    event_type: str | None = Field(default=None, max_length=256)
    measurement_method: MeasurementMethod | None = None
    calibration: Calibration | None = None
    time_quality: TimeQuality | None = None
    media_metadata: MediaMetadata | None = None

    @model_serializer(mode='wrap')
    def compatible_payload(self, handler):
        value = handler(self)
        # Old accepted records must retain their exact representation/hash.
        # Only these additive fields omit unknown defaults; old defaults stay.
        for key in ('measurement_method', 'calibration', 'time_quality', 'media_metadata'):
            if value.get(key) is None:
                value.pop(key, None)
        return value

    @model_validator(mode='after')
    def consistent(self):
        if not 2000 <= self.sampled_at.year < 2100:
            raise ValueError('Sample time outside supported partition calendar')
        if self.version == 1:
            if self.supersedes is not None or self.operation == 'withdraw':
                raise ValueError('Initial records cannot supersede or withdraw')
        elif (self.supersedes is None or self.supersedes.source_record_id != self.source_record_id
                or self.supersedes.version != self.version - 1):
            raise ValueError('Explicit preceding record/version required')
        if self.kind == 'observation' and self.metric_id is None:
            raise ValueError('Observation metric required')
        if self.kind == 'media' and (self.artifact_version_id is None or not self.channel_id or self.end_at is None):
            raise ValueError('Media requires private artifact, channel and segment end')
        if self.kind == 'track' and (self.position is None or not self.mission_id):
            raise ValueError('Track requires mission and datum-explicit position')
        if self.position is not None and self.position.observed_at != self.sampled_at:
            raise ValueError('Position and sample clocks differ')
        if self.kind == 'event' and not self.event_type:
            raise ValueError('Event type required')
        if self.end_at is not None and self.end_at <= self.sampled_at:
            raise ValueError('Segment end must follow sample time')
        if (self.device_binding_ref is None) != (self.model_ref is None):
            raise ValueError('Historical binding and model versions must both be pinned')
        return self


class BatchInput(DTO):
    source_id: UUID
    records: tuple[ObservationInput, ...] = Field(min_length=1, max_length=1000)
    input_artifact_version_id: UUID | None = None
    adapter: Literal['incremental-v1', 'csv-v1', 'json-v1', 'geojson-v1'] = 'incremental-v1'
    mapping_version_id: UUID | None = None


class FileReplayInput(DTO):
    source_id: UUID
    artifact_version_id: UUID
    format: Literal['csv', 'json', 'geojson']
    mapping_version_id: UUID | None = None


class ArchiveInput(DTO):
    object_id: UUID
    source_id: UUID
    before: AwareDatetime
    start: AwareDatetime | None = None
    kind: Literal['observation', 'event', 'media', 'track']
    quality: Literal['valid', 'suspect', 'invalid', 'unknown'] | None = None
    record_ids: tuple[UUID, ...] | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode='after')
    def bounded_selection(self):
        if self.start is not None and self.start >= self.before:
            raise ValueError('Archive interval must increase')
        if self.record_ids is not None and len(set(self.record_ids)) != len(self.record_ids):
            raise ValueError('Archive record selection must be unique')
        return self


class ArchiveReceipt(DTO):
    id: UUID
    artifact_version_id: UUID
    sha256: str
    record_count: int
    policy: Literal['retain-history']
    automatic_deletion: Literal[False]


class ArchiveManifest(DTO):
    archive_version: Literal[1]
    selector: ArchiveInput
    source_scope: ResourceScope
    record_hashes: dict[str, str]
    accepted_events: dict[str, list[UUID]]
    records: list[dict]
    policy: Literal['retain-history']
    automatic_deletion: Literal[False]
    complete_selection: bool


class SourceNamespace(DTO):
    source_id: UUID
    source_system: str
    source_instance: str
    collection: str


class ObservationView(ObservationInput):
    id: UUID
    content_hash: str
    received_at: AwareDatetime
    source_namespace: SourceNamespace
    supersedes_id: UUID | None


class Aggregate(DTO):
    count: int
    numeric_count: int
    min: str | None
    max: str | None
    sum: str | None


class TelemetryPage(DTO):
    status: Literal['current', 'pending', 'failed', 'unavailable', 'incomplete']
    items: list[ObservationView]
    next_cursor: str | None
    watermark: str | None
    aggregate: Aggregate
    aggregate_scope: Literal['page']
    incomplete_reason: Literal['query_budget'] | None = None
    query_url: str | None = None


class BatchAccepted(DTO):
    id: UUID
    event_id: UUID
    accepted_count: int
    record_count: int
    status: Literal['pending']


class BatchStatus(DTO):
    id: UUID
    event_id: UUID
    status: Literal['current', 'pending', 'failed', 'unavailable']
    delivery_status: Literal['pending', 'retry', 'failed', 'applied', 'superseded']
    attempts: int
    error: str | None


class ObjectDataView(DTO):
    identity: dict
    gis: dict
    catalog: dict
    observations: TelemetryPage
    media: TelemetryPage
    tracks: TelemetryPage
