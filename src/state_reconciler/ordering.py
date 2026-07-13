"""Ordering strategies - sequence Steps by Step.after: Kahn (readiness waves), DFS (depth-first flat), Priority (best-first flat), Components (independent chains)."""
import heapq
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from graphlib import CycleError, TopologicalSorter

from .step import Step


class Ordering(ABC):
    # Strategy interface for sequencing steps. A Reconciler is handed ONE Ordering and uses it to turn the
    # supplied steps into a run order that honours Step.after, so the algorithm can be swapped without
    # touching converge logic. Contract for every implementation: scope after= to the supplied set, key the
    # graph by class (every instance of a class comes out, in supplied order), and raise ValueError on a
    # cycle or unsatisfiable order.
    @abstractmethod
    def __call__(self, steps: tuple[Step, ...]) -> tuple[Step, ...]:
        ...

    def levels(self, steps: tuple[Step, ...]) -> tuple[tuple[Step, ...], ...]:
        # Topological LEVELS: each inner tuple is a wave of mutually-independent steps, waves in dependency
        # order. Default is ONE wave equal to the flat __call__ order. A level-capable ordering overrides.
        return (self(steps),)

    def chains(self, steps: tuple[Step, ...]) -> tuple[tuple[Step, ...], ...]:
        # Topological CHAINS: dual of levels(). Each inner tuple is one chain run in series, the chains
        # mutually independent so a pipelining executor runs them concurrently. Default is ONE chain of the
        # whole flat order, the sentinel a pipelining Reconciler rejects since it runs nothing concurrently.
        # A chain-capable ordering overrides.
        return (self(steps),)


class Kahn(Ordering):
    """Default Ordering: topological sort over Step.after via stdlib graphlib.TopologicalSorter (Kahn)."""

    @staticmethod
    def __graph(steps: tuple[Step, ...]):
        # Two invariants the sorter needs help with:
        #   1. SCOPE EDGES to the supplied set: a dep naming a step outside the handed-in tuple is dropped,
        #      so a caller may hand in a filtered subset without the sorter materialising a phantom node.
        #   2. KEY NODES BY CLASS: after= names classes and the sorter keys by ==/hash, so run the graph
        #      over type(step) and keep a class -> instances map. Two instances of one class collapse to one
        #      node, but each node maps back to every instance in supplied order, so nothing is dropped.
        present = {type(step) for step in steps}
        instances_of = defaultdict(list)  # class -> [instances], keeps supplied order, never drops a dupe
        for step in steps:
            instances_of[type(step)].append(step)
        sorter = TopologicalSorter()
        for step in steps:
            declared_deps = tuple(dep for dep in step.after if dep in present)  # scope to supplied set
            sorter.add(type(step), *declared_deps)
        return instances_of, sorter

    def __call__(self, steps: tuple[Step, ...]) -> tuple[Step, ...]:
        # Flat linear order. RAISE ValueError, not graphlib.CycleError, naming the stuck steps.
        instances_of, sorter = Kahn.__graph(steps)
        try:
            return tuple(step for kind in sorter.static_order() for step in instances_of[kind])
        except CycleError as cycle:
            cycle_path = cycle.args[1]  # nodes on the cycle, first node repeated at the end
            stuck = ", ".join(kind.__name__ for kind in dict.fromkeys(cycle_path))
            raise ValueError(f"Step dependency cycle or unsatisfiable order among: {stuck}") from cycle

    def levels(self, steps: tuple[Step, ...]) -> tuple[tuple[Step, ...], ...]:
        # Real waves over the SAME graph via prepare/get_ready/done. Flattening the waves equals the
        # static_order() above, so flat-order callers are unaffected.
        instances_of, sorter = Kahn.__graph(steps)
        try:
            sorter.prepare()
        except CycleError as cycle:
            stuck = ", ".join(kind.__name__ for kind in dict.fromkeys(cycle.args[1]))
            raise ValueError(f"Step dependency cycle or unsatisfiable order among: {stuck}") from cycle
        waves = []
        while sorter.is_active():
            ready = sorter.get_ready()
            waves.append(tuple(inst for kind in ready for inst in instances_of[kind]))
            sorter.done(*ready)
        return tuple(waves)


class DFS(Ordering):
    """Swap-in alternative to Kahn: same result contract, a different (still valid) order on branching
    graphs. Recurses into a node's deps and emits post-order, one chain to the bottom before siblings, so it
    agrees with Kahn on a linear chain but differs on a diamond. Recursive (a deep after= chain leans on the
    call stack) and catches a cycle by hitting a node already on the recursion path."""

    def __call__(self, steps: tuple[Step, ...]) -> tuple[Step, ...]:
        instances_of = defaultdict(list)  # class -> [instances], first-seen order, doubles as the node set
        for step in steps:
            instances_of[type(step)].append(step)
        done: set[type[Step]] = set()
        ordered_kinds: list[type[Step]] = []

        def visit(kind: type[Step], path: tuple[type[Step], ...]) -> None:
            if kind in done:
                return
            if kind in path:  # already on the recursion stack - a dependency cycle
                cycle = path[path.index(kind):] + (kind,)
                stuck = ", ".join(k.__name__ for k in dict.fromkeys(cycle))
                raise ValueError(f"Step dependency cycle or unsatisfiable order among: {stuck}")
            for dep in kind.after:
                if dep in instances_of:  # scope to the supplied set, membership never materialises a key
                    visit(dep, path + (kind,))
            done.add(kind)
            ordered_kinds.append(kind)  # post-order: a dep lands before the step that needs it

        for root in instances_of:  # dict keys ARE the node set, in first-seen order
            visit(root, ())
        return tuple(step for kind in ordered_kinds for step in instances_of[kind])


def _by_class_name(step_class: type) -> str:
    # Default Priority key: lexicographic by class name, a canonical order independent of input.
    return step_class.__name__


class Priority(Ordering):
    """Best-first Ordering: the ready set is a PRIORITY QUEUE, so at each step the ready class with the
    smallest key is emitted next. The key maps a Step class to a sort key and is injected: the default is
    the class name (reproducible output independent of input), or pass a domain priority to run more
    important ready steps first. Flat-only like DFS, so it pairs with Serial, not a fanning executor."""

    def __init__(self, key: Callable[[type], object] | None = None):
        # Resolved via None so no shared default leaks across instances. The key sees the Step CLASS, never
        # an instance.
        self.__key = key or _by_class_name

    def __call__(self, steps: tuple[Step, ...]) -> tuple[Step, ...]:
        # Kahn with a PRIORITY-QUEUE frontier (heapq): pop the ready class with the smallest key, emit it,
        # unlock its dependents, repeat. Keyed by CLASS, every instance out in supplied order. The heap tuple
        # carries an integer tiebreak so equal keys keep first-ready order and the class is never compared.
        # RAISE ValueError on a cycle, naming the stuck steps.
        instances_of = defaultdict(list)  # class -> [instances], keeps supplied order, never drops a dupe
        for step in steps:
            instances_of[type(step)].append(step)
        present = set(instances_of)
        sorter = TopologicalSorter()
        for step in steps:
            sorter.add(type(step), *(dep for dep in type(step).after if dep in present))  # scope to supplied set
        try:
            sorter.prepare()
        except CycleError as cycle:
            stuck = ", ".join(kind.__name__ for kind in dict.fromkeys(cycle.args[1]))
            raise ValueError(f"Step dependency cycle or unsatisfiable order among: {stuck}") from cycle
        frontier: list = []   # min-heap of (key, tiebreak, class): the ready set as a priority queue
        tiebreak = 0          # stable order for equal keys, keeps a non-comparable key from comparing classes
        ordered_kinds = []
        ready = sorter.get_ready()
        while ready or frontier:
            for kind in ready:
                heapq.heappush(frontier, (self.__key(kind), tiebreak, kind))
                tiebreak += 1
            _, _, kind = heapq.heappop(frontier)
            ordered_kinds.append(kind)
            sorter.done(kind)
            ready = sorter.get_ready()
        return tuple(step for kind in ordered_kinds for step in instances_of[kind])


class Components(Ordering):
    """Chain-capable Ordering for a pipelining executor: partitions the steps into weakly-connected
    components (maximal groups with no Step.after edge crossing between them), each component internally in
    Kahn topological order. The components share no edge so they are mutually independent, letting a Pipeline
    run each as its own serial chain with all chains concurrent. The flat __call__ concatenates the
    components into a valid topological order.

    Weakly-connected components, not a minimum path cover: path-cover chains still share edges (a diamond's
    two sides both depend on the fork and feed the join), so running them concurrently would ignore those
    edges. A component is the largest group genuinely independent of every other."""

    def __call__(self, steps: tuple[Step, ...]) -> tuple[Step, ...]:
        return tuple(step for chain in self.chains(steps) for step in chain)

    def chains(self, steps: tuple[Step, ...]) -> tuple[tuple[Step, ...], ...]:
        # Keyed by CLASS like Kahn. Union-find joins each step's class with every present after= dep into one
        # component, then each component is Kahn-sorted over its own sub-graph. Component order and
        # within-component instance order both follow first-seen, so the result is deterministic.
        instances_of = defaultdict(list)  # class -> [instances], first-seen order, doubles as the node set
        for step in steps:
            instances_of[type(step)].append(step)
        present = set(instances_of)
        parent = {kind: kind for kind in instances_of}  # union-find over classes

        def root(kind):
            while parent[kind] != kind:
                parent[kind] = parent[parent[kind]]  # path halving
                kind = parent[kind]
            return kind

        for step in steps:
            for dep in type(step).after:
                if dep in present:  # scope to supplied set, never materialise an absent dep
                    parent[root(type(step))] = root(dep)

        members_of = defaultdict(list)  # component root -> [classes], first-seen order
        for kind in instances_of:
            members_of[root(kind)].append(kind)

        chains = []
        for component in dict.fromkeys(root(kind) for kind in instances_of):  # components in first-seen order
            member_classes = members_of[component]
            member_set = set(member_classes)
            sorter = TopologicalSorter()
            for kind in member_classes:
                sorter.add(kind, *(dep for dep in kind.after if dep in member_set))
            try:
                ordered_kinds = tuple(sorter.static_order())
            except CycleError as cycle:
                stuck = ", ".join(kind.__name__ for kind in dict.fromkeys(cycle.args[1]))
                raise ValueError(f"Step dependency cycle or unsatisfiable order among: {stuck}") from cycle
            chains.append(tuple(inst for kind in ordered_kinds for inst in instances_of[kind]))
        return tuple(chains)
