"""Trusted domain adapters and non-configurable minimum risk rules."""
from dataclasses import dataclass
import hashlib
import json
from typing import Callable
from uuid import UUID

from lzo.contracts import JsonObject
from lzo.errors import ApiError


def digest_change(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class ScopedActionRequirement:
    """Trusted producer declaration, never accepted from an HTTP command."""
    phase: str
    action: str
    target_ids: tuple[UUID, ...]


def scoped_requirements_json(requirements):
    return [{"phase":r.phase, "action":r.action, "target_ids":[str(i) for i in r.target_ids]}
            for r in requirements]


@dataclass(frozen=True)
class OrdinaryImportEvidence:
    """Server-produced proof for the exact ordinary execution kind, never HTTP."""
    evaluator_version: str
    policy_digest: str
    manifest_digest: str
    registered_current_inputs: bool
    deterministic_effects: bool
    explicit_policy_coverage: bool
    bounded_impact: bool
    current_rights: bool
    effect_classes: tuple[str,...]


def system_policy_version(kind):
    return "ordinary-import-v1" if kind=="ingestion.import.execute" else 1


@dataclass(frozen=True)
class ChangeDescriptor:
    kind: str
    target_ids: tuple[UUID, ...]
    command: JsonObject
    baseline: dict[str, int]
    impact: JsonObject
    submit_action: str
    review_action: str
    operation_key: str
    execute_action: str | None = None
    global_security_class: str | None = None
    semantic_change: bool = True
    changed_fields: tuple[str, ...] = ()
    permission_expansion: bool = False
    unresolved_conflicts: bool = False
    policy_version: int = 1
    operation_target_ids: tuple[UUID, ...] | None = None
    scoped_action_requirements: tuple[ScopedActionRequirement, ...] = ()
    ordinary_import: OrdinaryImportEvidence | None = None

    @property
    def change_digest(self):
        content = {"kind": self.kind, "targets": [str(x) for x in self.target_ids],
                   "command": self.command}
        if self.operation_target_ids is not None:
            content["operation_target_ids"] = [str(x) for x in self.operation_target_ids]
        if self.scoped_action_requirements:
            content["scoped_action_requirements"] = scoped_requirements_json(self.scoped_action_requirements)
        return digest_change(content)


policies: dict[str, Callable] = {}


def register_policy(kind: str, resolver: Callable) -> None:
    """Trusted deployment code only; resolver reloads command dependencies in UoW.

    It must resolve every baseline from authoritative storage, enumerate all
    protected references, and reject unknown command/baseline identifiers.
    """
    policies[kind] = resolver


def resolve_change(uow, kind, targets, command):
    resolver = policies.get(kind)
    if resolver is None:
        raise ApiError("APPROVAL_POLICY_UNKNOWN", 422, "No trusted policy for this command")
    try:
        descriptor = resolver(uow, targets, command)
    except (ValueError, TypeError, KeyError):
        raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Invalid command descriptor") from None
    if (descriptor.kind != kind or not descriptor.target_ids or not descriptor.baseline
            or len(set(descriptor.target_ids)) != len(descriptor.target_ids)
            or descriptor.policy_version < 1
            or not descriptor.operation_key or any(type(v) is not int or v < 0 for v in descriptor.baseline.values())):
        raise ApiError("APPROVAL_POLICY_INVALID", 422, "Policy did not resolve a complete command")
    operations = descriptor.operation_target_ids
    if operations is not None and (not operations or any(not isinstance(x, UUID) for x in operations)
            or len(set(operations)) != len(operations)
            or not set(operations) <= set(descriptor.target_ids)
            or descriptor.target_ids[0] not in operations):
        raise ApiError("APPROVAL_POLICY_INVALID", 422, "Invalid operation target scope")
    requirements = descriptor.scoped_action_requirements
    keys = []
    for requirement in requirements:
        if (not isinstance(requirement, ScopedActionRequirement)
                or requirement.phase not in ("submit", "review", "execute")
                or not isinstance(requirement.action, str) or not requirement.action
                or len(requirement.action) > 128 or not requirement.target_ids
                or any(not isinstance(i, UUID) for i in requirement.target_ids)
                or tuple(sorted(set(requirement.target_ids), key=str)) != requirement.target_ids
                or not set(requirement.target_ids) <= set(descriptor.target_ids)):
            raise ApiError("APPROVAL_POLICY_INVALID", 422, "Invalid scoped action requirement")
        keys.append((requirement.phase, requirement.action))
    if keys != sorted(set(keys)):
        raise ApiError("APPROVAL_POLICY_INVALID", 422, "Scoped requirements must be unique and ordered")
    return descriptor


def classify(change: ChangeDescriptor):
    if change.permission_expansion:
        return "high", ["effective_permission_expansion"]
    proof=change.ordinary_import
    if (change.kind=="ingestion.import.execute" and isinstance(proof,OrdinaryImportEvidence)
            and proof.evaluator_version==system_policy_version(change.kind)
            and all(getattr(proof,name) is True for name in ("registered_current_inputs","deterministic_effects",
                "explicit_policy_coverage","bounded_impact","current_rights"))
            and all(len(value)==64 and all(c in "0123456789abcdef" for c in value) for value in (proof.policy_digest,proof.manifest_digest))
            and proof.effect_classes and set(proof.effect_classes)<={"create","facts","replace","withdraw","geometry","type","relation"}
            and not change.unresolved_conflicts):
        return "low", ["published_ordinary_source_execution"]
    if change.kind.startswith("identity."):
        return "low", ["ordinary_authorization_management"]
    if (change.kind == "metadata.update" and not change.semantic_change
            and change.changed_fields and set(change.changed_fields) <= {"label", "description"}):
        return "low", ["nonsemantic_metadata_only"]
    return "high", ["system_minimum_semantic_or_policy_change"]
