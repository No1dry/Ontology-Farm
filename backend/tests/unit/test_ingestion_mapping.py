"""Bounded deterministic mapping expressions; no script execution."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from lzo.ingestion.schemas import RuleExpr
from lzo.ingestion.mapping import evaluate, convert_unit


def test_mapping_expression_normalizes_without_code_execution():
    expr = RuleExpr(op="normalize", args=(RuleExpr(op="path", path="pond.code"),), value={"mode":"casefold"})
    assert evaluate(expr, {"pond":{"code":"  Ａ-12  "}}) == "a-12"
    assert evaluate(RuleExpr(op="coalesce", args=(RuleExpr(op="path", path="missing"), RuleExpr(op="literal", value={"value":0}))), {}) == 0
    with pytest.raises(ValidationError):
        RuleExpr(op="exec", value={"value":"__import__('os')"})
    with pytest.raises(ValidationError):
        RuleExpr(op="path", path="pond.__class__", surprise=True)


def test_unit_and_time_conversion_are_explicit_and_exact():
    assert convert_unit("2.5", "ha", "m2") == Decimal("25000")
    assert evaluate(RuleExpr(op="date", args=(RuleExpr(op="literal", value={"value":"2026-01-01T08:00:00"}),), value={"timezone":"Asia/Shanghai"}), {}) == "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        convert_unit("1", "m", "kg")
    with pytest.raises(ValueError):
        evaluate(RuleExpr(op="cast", args=(RuleExpr(op="literal", value={"value":"NaN"}),), value={"type":"decimal"}), {})


def test_rule_expression_has_independent_depth_and_work_limits():
    expr = RuleExpr(op="literal", value={"value":"x"})
    for _ in range(18):
        expr = RuleExpr(op="coalesce", args=(expr,))
    with pytest.raises(ValueError, match="limit"):
        evaluate(expr, {})
    with pytest.raises(ValueError):
        evaluate(RuleExpr(op="normalize", args=(), value={"mode":"casefold"}), {})


@pytest.mark.parametrize("expression",[
    RuleExpr(op="path",path="code",value={"script":"ignored"}),
    RuleExpr(op="literal",path="unexpected",value={"value":"x"}),
    RuleExpr(op="coalesce",args=(RuleExpr(op="literal",value={"value":"x"}),),value={"unexpected":True}),
    RuleExpr(op="cast",args=(RuleExpr(op="literal",value={"value":1}),),value={"type":"boolean"}),
])
def test_malformed_options_and_lossy_boolean_are_rejected(expression):
    with pytest.raises(ValueError): evaluate(expression,{"code":"x"})


@pytest.mark.parametrize("value", ["1e129", "1e-129", "0e100000", "1" * 129])
def test_numeric_expansion_is_bounded_before_cast_and_unit_conversion(value):
    from lzo.ingestion.mapping import number
    with pytest.raises(ValueError): number(value)
    with pytest.raises(ValueError): convert_unit(value, "m", "m")
    expr = RuleExpr(op="cast", args=(RuleExpr(op="path", path="number"),), value={"type":"integer"})
    with pytest.raises(ValueError): evaluate(expr, {"number":value})


def test_unit_conversion_is_exact_independent_of_ambient_precision():
    from decimal import localcontext
    value = "123456789012345678901234567890"
    with localcontext() as context:
        context.prec = 3
        assert convert_unit(value, "m", "m") == Decimal(value)
        assert convert_unit(value, "km", "m") == Decimal(value + "000")
        assert convert_unit(value, "g", "kg") == Decimal(value[:-3] + "." + value[-3:])
    with pytest.raises(ValueError): convert_unit("1e127", "km", "m")
