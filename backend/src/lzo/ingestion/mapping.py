"""Finite, deterministic data transformations; no executable rule language."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, Inexact, localcontext
import unicodedata
from lzo.ingestion.datetimes import source_datetime
from lzo.ingestion.schemas import RuleExpr
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceBlankReference:
    """Raw-registry reference, never an accepted RDF term."""
    kind: str
    value: str
    datatype_iri: None = None
    language: None = None


def semantic_dispositions(change,profile):
    """Account for every semantic predicate before planning accepted effects."""
    if change.raw_fields.get("contract")!="semantic-v1":return ()
    from lzo.ingestion.schemas import ParseIssue
    from lzo.ingestion.sources import digest
    paths={field.source_path for field in profile.fields if field.term_kind in ("rdf","reference") and field.multiple}
    # Only authenticated normalization can supply these explicit metadata paths;
    # their exact literals remain in raw provenance, not accepted assertions.
    paths.update(change.raw_fields.get("temporal_paths",{}).values())
    issues=[]
    for predicate,values in change.raw_fields["predicates"].items():
        code=None
        if predicate=="http://www.w3.org/2002/07/owl#sameAs":code="SEMANTIC_ALIGNMENT_REVIEW"
        elif predicate=="http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
            if any(value.get("kind")!="iri" or value.get("value")!=profile.class_iri for value in values):code="SEMANTIC_TYPE_UNMAPPED"
        elif "terms."+digest(predicate) not in paths:code="SEMANTIC_TERM_UNMAPPED"
        if code:issues.append(ParseIssue(locator=change.locator,field_path=predicate,code=code,
            message="Semantic predicate requires an explicit governed disposition"))
    return tuple(issues)


def path_value(record, path):
    if not path or len(path) > 512:
        raise ValueError("Invalid mapping path")
    value = record
    for key in path.split("."):
        if not key or key.startswith("__"):
            raise ValueError("Invalid mapping path")
        value = value.get(key) if isinstance(value, dict) else None
    return value


def geometry_value(change, profile):
    """Only an explicit present null withdraws configured source geometry."""
    if change.operation == "delete":
        return None
    path = profile.geometry_path
    if not path:
        return change.geometry
    if len(path) > 512:
        raise ValueError("Invalid geometry path")
    value = change.raw_fields
    for key in path.split("."):
        if not key or key.startswith("__"):
            raise ValueError("Invalid geometry path")
        if not isinstance(value, dict) or key not in value:
            raise ValueError("Configured geometry path is missing")
        value = value[key]
    return value


def number(value):
    # Finite decimal contract: at most 128 coefficient digits, exponent steps,
    # and fixed-point digits. Check compact input before parsing or expansion.
    lexical = str(value)
    if len(lexical) > 256:
        raise ValueError("Decimal limit exceeded")
    try:
        result = Decimal(lexical)
    except InvalidOperation:
        raise ValueError("Invalid decimal") from None
    if not result.is_finite():
        raise ValueError("Invalid decimal")
    _, digits, exponent = result.as_tuple()
    if (len(digits) > 128 or abs(exponent) > 128
            or max(len(digits) + exponent, 1) + max(-exponent, 0) > 128):
        raise ValueError("Decimal limit exceeded")
    return result


def convert_unit(value, source, target):
    units = {"m":("length", "1"), "km":("length", "1000"),
             "m2":("area", "1"), "ha":("area", "10000"),
             "kg":("mass", "1"), "g":("mass", ".001"),
             "m3":("volume", "1"), "L":("volume", ".001")}
    if source not in units or target not in units or units[source][0] != units[target][0]:
        raise ValueError("Incompatible explicit units")
    dec = number(value)
    source_factor, target_factor = Decimal(units[source][1]), Decimal(units[target][1])
    shift = source_factor.adjusted() - target_factor.adjusted()
    sign, digits, exponent = dec.as_tuple()
    number(Decimal((sign, digits, exponent + shift)))
    # All supported factors are powers of ten. This explicit precision covers
    # every bounded operand and factor, with an inexact operation rejected.
    with localcontext() as context:
        context.prec = 144
        context.traps[Inexact] = True
        return number(dec * source_factor / target_factor)


def evaluate(expression: RuleExpr, record):
    validate_expression(expression)
    work = [0]
    def visit(expr, depth):
        work[0] += 1
        if depth > 16 or work[0] > 512:
            raise ValueError("Expression limit exceeded")
        op, args, options = expr.op, expr.args, expr.value or {}
        arity = {"path":(0,0), "literal":(0,0), "coalesce":(1,32), "cast":(1,1),
                 "unit":(1,1), "date":(1,1), "normalize":(1,1), "eq":(2,2), "and":(1,32), "or":(1,32)}
        lo, hi = arity[op]
        if not lo <= len(args) <= hi:
            raise ValueError("Invalid expression arity")
        if op == "path":
            result = path_value(record, expr.path)
        elif op == "literal":
            if set(options) != {"value"}:
                raise ValueError("Literal value required")
            result = options["value"]
        else:
            values = [visit(arg, depth+1) for arg in args]
            if op == "coalesce": result = next((v for v in values if v is not None), None)
            elif op == "eq": result = type(values[0]) == type(values[1]) and values[0] == values[1]
            elif op in ("and", "or"):
                if any(type(v) is not bool for v in values): raise ValueError("Boolean operands required")
                result = all(values) if op == "and" else any(values)
            elif op == "unit":
                if set(options) != {"source", "target"}: raise ValueError("Explicit units required")
                result = str(convert_unit(values[0], options["source"], options["target"]))
            elif op == "normalize":
                if set(options) != {"mode"} or options["mode"] not in ("strip", "casefold", "nfkc"):
                    raise ValueError("Unsupported normalizer")
                if not isinstance(values[0], str): raise ValueError("Text required")
                result = unicodedata.normalize("NFKC", values[0]).strip()
                if options["mode"] == "casefold": result = result.casefold()
            elif op == "cast":
                if set(options) != {"type"}: raise ValueError("Explicit cast required")
                kind, value = options["type"], values[0]
                if kind == "string" and isinstance(value, (str, int, float, bool)): result = str(value)
                elif kind == "decimal": result = str(number(value))
                elif kind == "integer":
                    dec = number(value)
                    if dec != dec.to_integral_value(): raise ValueError("Lossy integer cast")
                    result = int(dec)
                elif kind == "boolean" and (type(value) is bool or type(value) is str and value in ("true", "false")):
                    result = value is True or value == "true"
                else: raise ValueError("Unsupported cast")
            elif op == "date":
                if set(options) - {"timezone"}: raise ValueError("Unsupported date option")
                parsed = source_datetime(values[0],options.get("timezone"))
                result = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(result, str) and len(result.encode()) > 65536:
            raise ValueError("Expression text limit exceeded")
        return result
    return visit(expression, 0)


def validate_expression(expression):
    nodes=[(expression,0)];work=0
    arity={"path":(0,0),"literal":(0,0),"coalesce":(1,32),"cast":(1,1),"unit":(1,1),
        "date":(1,1),"normalize":(1,1),"eq":(2,2),"and":(1,32),"or":(1,32)}
    options={"path":set(),"literal":{"value"},"coalesce":set(),"cast":{"type"},"unit":{"source","target"},
        "date":{"timezone"},"normalize":{"mode"},"eq":set(),"and":set(),"or":set()}
    while nodes:
        expr,depth=nodes.pop();work+=1
        if depth>16 or work>512: raise ValueError("Expression limit exceeded")
        lo,hi=arity[expr.op]
        if not lo<=len(expr.args)<=hi: raise ValueError("Invalid expression arity")
        if set(expr.value or {})!=options[expr.op] and not (expr.op=="date" and not expr.value):
            raise ValueError("Invalid expression options")
        if expr.op=="path": path_value({},expr.path)
        elif expr.path is not None: raise ValueError("Unexpected expression path")
        if expr.value and len(str(expr.value).encode())>65536: raise ValueError("Expression text limit exceeded")
        nodes.extend((arg,depth+1) for arg in expr.args)


def field_terms(field, record):
    """Finite canonical term set; literal lexical forms never pass through str(float)."""
    from dataclasses import asdict
    from lzo.semantic.rdf import RdfTerm
    from lzo.ingestion.sources import canonical
    raw=path_value(record,field.source_path)
    value=evaluate(field.expression,record) if field.expression else raw
    if value is None:return ()
    values=value if field.multiple else [value]
    if not isinstance(values,list) or len(values)>1024:raise ValueError("Repeated term limit exceeded")
    result={}
    for value in values:
        if field.term_kind in ("rdf","reference"):
            if not isinstance(value,dict) or set(value)-{"kind","value","datatype_iri","language"}:
                raise ValueError("Exact typed RDF term required")
            if field.expression or field.source_unit or field.target_unit:raise ValueError("Typed terms cannot be coerced")
            if field.term_kind=='reference' and value.get('kind')=='bnode':
                if (not isinstance(value.get('value'),str) or not value['value'].startswith('urn:lzo:source-blank:')
                        or value.get('datatype_iri') or value.get('language')):
                    raise ValueError('Source-scoped blank reference required')
                term=SourceBlankReference('bnode',value['value'])
            else:term=RdfTerm(**value)
            if field.term_kind=="reference" and term.kind not in ('iri','bnode'):raise ValueError("Source reference required")
        else:
            if isinstance(value,(dict,list)):raise ValueError("Scalar field mapping required")
            if field.source_unit or field.target_unit:value=format(convert_unit(value,field.source_unit,field.target_unit),"f")
            lexical=("true" if value else "false") if type(value) is bool else str(value)
            term=RdfTerm("literal",lexical,field.datatype_iri)
        key=canonical(asdict(term))
        if len(key.encode())>65536:raise ValueError("Term text limit exceeded")
        result[key]=term
    return tuple(result[key] for key in sorted(result))


class MappingService:
    def __init__(self, uow=None): self.uow = uow

    def plan(self, actor, change, profile, bundle, identity):
        from uuid import uuid5, NAMESPACE_URL
        from lzo.errors import Conflict
        from lzo.semantic.rdf import RdfTerm
        from lzo.semantic.schemas import AssertionInput, SourceSupport
        from lzo.semantic.facts import revision_ref,POLICY
        from lzo.ingestion.identity import registered_change, IdentityService
        from lzo.ingestion.rules import profile_for_bundle, authority_rule,RuleAuthorityPort
        from lzo.ingestion.schemas import MappedRecord
        from lzo.ingestion.sources import digest
        source = registered_change(self.uow, actor, change, profile)
        profile_for_bundle(self.uow, actor, source.id, profile.id, bundle)
        actual = IdentityService(self.uow).resolve(self.uow, actor, change, profile, False)
        if actual.resource_id != identity.resource_id or actual.matched_rule_id != identity.matched_rule_id:
            raise Conflict("IDENTITY_BINDING_MISMATCH", "Identity differs from authoritative binding")
        facts = []
        if identity.resource_id is not None and change.operation == "upsert":
            for field in profile.fields:
                rule = authority_rule(self.uow, actor, field.predicate_iri, bundle)
                # Neither import candidates nor later decisions may overwrite
                # ontology-owned notes, derived or read-only values.
                if rule is None or rule.owner_policy != "source_backed" or source.id not in rule.owner_source_ids:
                    continue
                if RuleAuthorityPort().resolve(self.uow,actor,field.predicate_iri,bundle)!=POLICY+"source_backed":continue
                raw = path_value(change.raw_fields, field.source_path)
                terms=field_terms(field,change.raw_fields)
                for term in terms:
                    origin_term=term
                    if field.term_kind=="reference":
                        from lzo.ingestion.models import identity_bindings
                        from sqlalchemy import select
                        candidates=self.uow.session.execute(select(identity_bindings).where(
                            identity_bindings.c.source_id==source.id)).mappings().all()
                        matches=[]
                        for candidate in candidates:
                            import json
                            try:key=json.loads(candidate["canonical_key"])
                            except (ValueError,TypeError):continue
                            subject=key.get('subject',{}) if isinstance(key,dict) else {}
                            if subject.get("value")==term.value and subject.get('kind')==term.kind:
                                matches.append(candidate)
                        # Source reference matching is exact and source scoped.
                        # Multiple graph-scoped identities require explicit review.
                        if len(matches)!=1:raise ValueError("Reference requires one confirmed source binding")
                        from lzo.semantic.facts import object_iri
                        target=matches[0]["resource_id"]
                        # This is only a candidate term. Complete S03 validation
                        # must prove the target's current type and visibility;
                        # reservation alone never creates accepted type evidence.
                        term=RdfTerm("iri",object_iri(target))
                    key_data={"change":str(change.change_id),"profile":str(profile.id),
                        "predicate":field.predicate_iri,"resource":str(identity.resource_id)}
                    if field.term_kind!="scalar" or field.multiple:
                        from dataclasses import asdict
                        key_data["term"]=asdict(origin_term)
                        key_data["graph"]=change.raw_fields.get("graph")
                    key=digest(key_data)
                    support = SourceSupport(support_id=uuid5(NAMESPACE_URL,"support:"+key),source_id=source.id,
                        record_key=change.key.canonical_key,source_version=change.position.source_version,
                        snapshot=change.snapshot,raw_value={"value":raw,"source_unit":field.source_unit,
                            "target_unit":field.target_unit,**({"semantic":{k:change.raw_fields.get(k) for k in
                                ("contract","subject","graph","canonical_digest","original_digest")},"term":key_data["term"]}
                                if "term" in key_data else {})},mapping_version_id=profile.id,
                        evidence_refs=(revision_ref(self.uow,change.change_id),revision_ref(self.uow,profile.id)),
                        observed_at=change.observed_at)
                    facts.append(AssertionInput(assertion_id=uuid5(NAMESPACE_URL,"assertion:"+key),resource_id=identity.resource_id,
                        predicate_iri=field.predicate_iri,object=term,state="candidate",data_nature=change.data_nature,
                        valid_from=change.valid_from or datetime.min.replace(tzinfo=timezone.utc),valid_to=change.valid_to,support=support))
        name = path_value(change.raw_fields,profile.name_path)
        if change.operation=='delete' and identity.resource_id is not None:
            from lzo.semantic.facts import get_object_row
            name=get_object_row(self.uow,actor,identity.resource_id,'edit')['payload']['display_name']
        if not isinstance(name,str) or not name.strip() or len(name)>512: raise ValueError("Mapped name required")
        geometry = geometry_value(change,profile)
        return MappedRecord(change=change,identity=identity,class_iri=profile.class_iri,display_name=name,
            scope=source.configuration.scope,facts=tuple(facts),geometry=geometry,conflicts=(),decision_ref=None)
