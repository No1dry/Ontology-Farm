"""Stable SHACL result contracts; errors are exceptions, never conformance."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from lzo.contracts import BlobRef
from lzo.semantic.rdf import RdfTerm


@dataclass(frozen=True)
class ValidationIssue:
    focus_iri: str
    path_iri: str | None
    shape_iri: str | None
    constraint_iri: str
    severity: Literal["Violation", "Warning", "Info"]
    message: str
    value: RdfTerm | None


@dataclass(frozen=True)
class ValidationReport:
    conforms: bool
    violations: tuple[ValidationIssue, ...]
    report_artifact: BlobRef | None
    bundle_id: UUID
    focus_iris: tuple[str, ...] | None = None
    invocation_scope: Literal["dataset", "focused"] = "dataset"
    original_shapes_digest: str | None = None
    root_selection: dict[str, tuple[str, ...]] | None = None
    evaluation_time: datetime | None = None
