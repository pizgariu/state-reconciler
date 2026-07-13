"""Partition: the resolved run structure the executor walks, as Levels (waves) or Chains (pipelines)."""
import operator
from abc import ABC, abstractmethod
from collections import namedtuple
from enum import Enum


class Placement(namedtuple("Placement", "holds described")):
    # A shape's ordering rule as a value object: holds(dependency_group, dependent_group) is True when a
    # dependency is legally placed for its dependent, and described is that rule in words for verify()'s error.
    __slots__ = ()


class _Placements(Enum):
    # The two placement rules. The set is closed: a dependency sits in an earlier group for waves or the same
    # group for chains, so an enum rather than literals inline on each shape.
    EARLIER = Placement(operator.lt, "in an earlier group")   # waves
    SAME    = Placement(operator.eq, "in the same group")     # chains


class Partition(tuple, ABC):
    """A resolved run structure the executor walks: a tuple of groups, each group a tuple of steps. The two
    shapes are dual - Levels (waves: parallel within a group, sequential between) and Chains (pipelines:
    serial within a group, concurrent between). The Reconciler treats either shape uniformly: it walks the
    groups and inverts them for teardown.

    inverse() is the teardown rule: the same structure walked backwards, both the group order and the steps
    within each group reversed, so a dependent is always torn down before the thing it depends on. It returns
    a plain tuple, not a Partition: dependents-first is the opposite orientation and just runs, never re-verified.

    verify() is the concurrency guard, one walk for both shapes. The shapes differ only in where a dependency
    may sit relative to its dependent, so each shape declares just that rule via _placement. ABC here is
    declarative: a tuple subclass's C-level __new__ skips the abstractmethod instantiate-check, so the hook
    type-checks the contract rather than block a bare Partition at runtime."""
    __slots__ = ()

    def inverse(self) -> tuple:
        return tuple(tuple(reversed(group)) for group in reversed(self))

    def verify(self) -> None:
        # A fanning executor runs a whole group at once, so a step's dependency must be placed where that
        # fan-out still honours Step.after. The Ordering promises the shape but does not prove it, so a
        # mis-split is caught here.
        group_of = {type(step): index for index, group in enumerate(self) for step in group}
        for index, group in enumerate(self):
            for step in group:
                for dependency in step.after:
                    if dependency in group_of and not self._placement.holds(group_of[dependency], index):
                        raise ValueError(
                            f"{type(step).__name__} depends on {dependency.__name__}, but the Ordering "
                            f"placed {dependency.__name__} in group {group_of[dependency]} and "
                            f"{type(step).__name__} in group {index} - a concurrent executor needs a "
                            f"dependency {self._placement.described}, so it would ignore Step.after."
                        )

    @property
    @abstractmethod
    def _placement(self) -> Placement:
        # The one per-shape rule (a Placement: predicate plus its wording). The walk and message are shared.
        ...


class Levels(Partition):
    """A partition as topological LEVELS: each inner tuple is one wave of mutually-independent steps, the
    waves in dependency order. Its placement (a dependency must sit in an EARLIER wave) is the guard a
    level-fanning executor needs, run by the shared verify()."""
    __slots__ = ()
    _placement = _Placements.EARLIER.value


class Chains(Partition):
    """A partition as independent CHAINS: each inner tuple is one chain of steps run in series, the chains
    mutually independent so a chain-fanning executor runs them concurrently. The dual of Levels. Its placement
    (a dependency must sit in the SAME chain, run in series before it) is the guard against a Step.after edge
    crossing between chains, run by the shared verify()."""
    __slots__ = ()
    _placement = _Placements.SAME.value
