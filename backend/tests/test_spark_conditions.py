"""Unit tests for the per-column match-condition combiner.

``build_match_condition`` takes the Spark ``functions`` module so its AND/OR
logic can be exercised with a symbolic fake — no JVM required. We assert the
shape of the combined column expression the engine would hand to Spark.
"""
from processing.spark_engine import (
    CELL_ACTIONS,
    MATCH_FLAG_COLUMN,
    ROW_ACTIONS,
    build_match_condition,
    cell_action_expr,
)


class _Expr:
    def __init__(self, text: str):
        self.text = text

    def __and__(self, other: "_Expr") -> "_Expr":
        return _Expr(f"({self.text} AND {other.text})")

    def __or__(self, other: "_Expr") -> "_Expr":
        return _Expr(f"({self.text} OR {other.text})")


class _Col:
    def __init__(self, name: str):
        self.name = name

    def rlike(self, pattern: str) -> _Expr:
        return _Expr(f"{self.name}~{pattern}")


class _F:
    """Stand-in for pyspark.sql.functions."""

    @staticmethod
    def col(name: str) -> _Col:
        return _Col(name)

    @staticmethod
    def lit(value) -> _Col:
        return _Col(repr(value))

    @staticmethod
    def coalesce(*cols: _Col) -> _Col:
        return _Col("coalesce(" + ",".join(c.name for c in cols) + ")")


NAME_PHONE = [
    {"column": "name", "pattern": "^A"},
    {"column": "phone", "pattern": "^0"},
]


# Each column is null-coalesced to "" before rlike (Spark reads an empty CSV
# field as NULL, and rlike on NULL is NULL) so an "is blank" pattern can fire.
def test_all_combines_with_and():
    out = build_match_condition(_F, NAME_PHONE, "all")
    assert out.text == "(coalesce(name,'')~^A AND coalesce(phone,'')~^0)"


def test_any_combines_with_or():
    out = build_match_condition(_F, NAME_PHONE, "any")
    assert out.text == "(coalesce(name,'')~^A OR coalesce(phone,'')~^0)"


def test_single_predicate_is_unwrapped():
    out = build_match_condition(_F, [{"column": "name", "pattern": "^A"}], "all")
    assert out.text == "coalesce(name,'')~^A"


def test_three_predicates_fold_left():
    preds = [
        {"column": "a", "pattern": "1"},
        {"column": "b", "pattern": "2"},
        {"column": "c", "pattern": "3"},
    ]
    out = build_match_condition(_F, preds, "all")
    assert out.text == (
        "((coalesce(a,'')~1 AND coalesce(b,'')~2) AND coalesce(c,'')~3)"
    )


# --------------------------------------------------------------------------- #
# Per-column cell-action expression (replace / mask / extract)
# --------------------------------------------------------------------------- #
# A slightly richer symbolic Spark so we can assert WHICH function each action
# emits and how it's guarded, without a JVM.
class _SymCol:
    def __init__(self, text: str):
        self.text = text

    def rlike(self, pattern: str) -> "_SymCol":
        return _SymCol(f"{self.text}.rlike({pattern})")

    def __and__(self, other: "_SymCol") -> "_SymCol":
        return _SymCol(f"({self.text} & {other.text})")


class _When:
    def __init__(self, cond: _SymCol, val: _SymCol):
        self.cond, self.val = cond, val

    def otherwise(self, other: _SymCol) -> _SymCol:
        return _SymCol(f"WHEN[{self.cond.text}]->{self.val.text}|ELSE->{other.text}")


class _SymF:
    @staticmethod
    def col(name: str) -> _SymCol:
        return _SymCol(f"col:{name}")

    @staticmethod
    def lit(value) -> _SymCol:
        return _SymCol(repr(value))

    @staticmethod
    def coalesce(*cols: _SymCol) -> _SymCol:
        return _SymCol("coalesce(" + ",".join(c.text for c in cols) + ")")

    @staticmethod
    def regexp_replace(c: _SymCol, pattern: str, sub: str) -> _SymCol:
        return _SymCol(f"regexp_replace({c.text},{pattern},{sub})")

    @staticmethod
    def regexp_extract(c: _SymCol, pattern: str, idx: int) -> _SymCol:
        return _SymCol(f"regexp_extract({c.text},{pattern},{idx})")

    @staticmethod
    def when(cond: _SymCol, val: _SymCol) -> _When:
        return _When(cond, val)


# The matched cell is null-coalesced to "" before the rewrite so a blank pattern
# fires on empty cells (regexp_* return NULL on NULL input otherwise).
def test_replace_uses_regexp_replace_guarded_by_match_flag():
    out = cell_action_expr(_SymF, "email", "@", "replace", "X", "••••")
    # Substitutes the replacement, gated only on the row match flag.
    assert "regexp_replace(coalesce(col:email,''),@,X)" in out.text
    assert f"WHEN[col:{MATCH_FLAG_COLUMN}]" in out.text
    assert "rlike" not in out.text  # replace doesn't re-test the column


def test_mask_writes_the_mask_token_not_the_replacement():
    out = cell_action_expr(_SymF, "card", r"\d", "mask", "X", "••••")
    assert r"regexp_replace(coalesce(col:card,''),\d,••••)" in out.text
    assert ",X)" not in out.text  # the plain replacement value is not used


def test_extract_uses_regexp_extract_and_per_column_guard():
    out = cell_action_expr(_SymF, "email", "@", "extract", "X", "••••")
    assert "regexp_extract(coalesce(col:email,''),@,0)" in out.text
    # Guarded by BOTH the row flag AND this column's own match, so an OR row
    # never blanks a column that had no match of its own.
    assert f"col:{MATCH_FLAG_COLUMN} & coalesce(col:email,'').rlike(@)" in out.text


def test_action_sets_are_disjoint_and_complete():
    assert CELL_ACTIONS == {"replace", "mask", "extract"}
    assert ROW_ACTIONS == {"keep", "drop"}
    assert CELL_ACTIONS.isdisjoint(ROW_ACTIONS)
