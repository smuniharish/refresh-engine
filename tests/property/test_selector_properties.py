from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given
from hypothesis import strategies as st

from refresh_engine import (
    IDSelector,
    PredicateSelector,
    Resource,
    ResourceSelectorABC,
    TagSelector,
)

IDS = ("a", "b", "c", "d")
TAGS = ("red", "green", "blue")


@dataclass(frozen=True)
class Expression:
    """A selector expression and an independent evaluator for it."""

    operator: str
    operands: tuple[Expression, ...] = ()
    values: frozenset[str] = frozenset()
    match_all: bool = True

    def build(self) -> ResourceSelectorABC:
        if self.operator == "ids":
            return IDSelector(self.values)
        if self.operator == "tags":
            return TagSelector(self.values, match_all=self.match_all)
        if self.operator == "even":
            return PredicateSelector(
                lambda resource: resource.resource_id in {"b", "d"}
            )
        built = [operand.build() for operand in self.operands]
        if self.operator == "not":
            return ~built[0]
        if self.operator == "and":
            return built[0] & built[1]
        return built[0] | built[1]

    def evaluate(self, resource: Resource) -> bool:
        tags = set(resource.metadata.get("tags", ()))
        if self.operator == "ids":
            return resource.resource_id in self.values
        if self.operator == "tags":
            return self.values <= tags if self.match_all else bool(self.values & tags)
        if self.operator == "even":
            return resource.resource_id in {"b", "d"}
        results = [operand.evaluate(resource) for operand in self.operands]
        if self.operator == "not":
            return not results[0]
        if self.operator == "and":
            return results[0] and results[1]
        return results[0] or results[1]


leaves = st.one_of(
    st.frozensets(st.sampled_from(IDS), min_size=1).map(
        lambda values: Expression("ids", values=values)
    ),
    st.tuples(st.frozensets(st.sampled_from(TAGS), min_size=1), st.booleans()).map(
        lambda pair: Expression("tags", values=pair[0], match_all=pair[1])
    ),
    st.just(Expression("even")),
)
expressions = st.recursive(
    leaves,
    lambda children: st.one_of(
        children.map(lambda child: Expression("not", (child,))),
        st.tuples(children, children).map(lambda pair: Expression("and", pair)),
        st.tuples(children, children).map(lambda pair: Expression("or", pair)),
    ),
    max_leaves=8,
)
resources = st.builds(
    lambda resource_id, tags: Resource(resource_id, metadata={"tags": sorted(tags)}),
    st.sampled_from(IDS),
    st.frozensets(st.sampled_from(TAGS)),
)


@given(expressions, resources)
def test_selectors_match_their_reference_semantics(
    expression: Expression, resource: Resource
) -> None:
    assert expression.build().matches(resource) is expression.evaluate(resource)


@given(expressions, expressions, resources)
def test_de_morgan_laws_hold(
    first: Expression, second: Expression, resource: Resource
) -> None:
    left, right = first.build(), second.build()
    assert (~(left | right)).matches(resource) == (~left & ~right).matches(resource)
    assert (~(left & right)).matches(resource) == (~left | ~right).matches(resource)


@given(expressions, resources)
def test_double_negation_is_identity(
    expression: Expression, resource: Resource
) -> None:
    selector = expression.build()
    assert (~~selector).matches(resource) == selector.matches(resource)
