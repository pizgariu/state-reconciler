"""A domain-agnostic reconciliation kernel: Steps own desired state, a Reconciler resolves their
order and converges actual -> desired, self-verifying by re-probing for residual drift.

No domain vocabulary lives here. A drift item is read ONLY through the Drift protocol's two
fields (name + message). Everything richer is the domain's own payload. Two reusable behaviours
live here once and are inherited by every caller: sequencing steps by Step.after (dependencies
declared explicitly, resolved by a pluggable Ordering strategy, graphlib Kahn by default), and
the self-verifying converge (apply -> re-probe -> residual).

The re-probe is only as strong as a step's drift(): a step that mutates but reports no drift is
trusted to have worked, not verified, so a step guarding a real invariant must expose it through
drift(). That limit also decides what belongs here: a domain whose state cannot be probed
completely and cheaply enough for skip-when-clean to stay correct is no fit, however much it
resembles desired-state work.
"""

from .cancellation import (
    AllOf,
    AnyOf,
    Cancellation,
    Cancelled,
    Deadline,
    Every,
    Flag,
    Majority,
    Most,
    Quorum,
    Rule,
    Some,
)
from .convergence import Convergence, Fixpoint, Once
from .drift import Drift, DriftItem
from .executor import Executor, OnError, Parallel, Pipeline, Serial
from .ordering import DFS, Components, Kahn, Ordering, Priority
from .partition import Chains, Levels, Partition, Placement
from .reconciler import Controller, Reconciler, Residual
from .step import Step

__all__ = [
    "Drift", "DriftItem", "Step",
    "Ordering", "Kahn", "DFS", "Priority", "Components",
    "Placement", "Partition", "Levels", "Chains",
    "OnError", "Executor", "Serial", "Parallel", "Pipeline",
    "Cancelled", "Cancellation", "Deadline", "Flag", "Rule", "Some", "Every", "Most", "Quorum", "AnyOf", "AllOf", "Majority",
    "Convergence", "Once", "Fixpoint",
    "Residual", "Reconciler", "Controller",
]
