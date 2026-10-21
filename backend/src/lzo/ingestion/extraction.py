"""Future extractors emit candidates only. Confidence is not fact probability."""
from collections.abc import Iterator
from typing import Protocol
from pydantic import AwareDatetime, Field
from lzo.contracts import BlobRef, JsonObject
from lzo.ingestion.schemas import DTO


class ExtractionCandidate(DTO):
    source_blob: BlobRef
    locator: str
    model_id: str
    model_version: str
    proposed_subject: str | None
    predicate_iri: str
    proposed_object: JsonObject
    confidence: float | None = Field(ge=0, le=1)
    extracted_at: AwareDatetime


class DocumentExtractor(Protocol):
    def extract(self, blob: BlobRef) -> Iterator[ExtractionCandidate]: ...
