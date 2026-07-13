"""Step - one reconciliation concern that owns its desired state, with read/apply/prune hooks."""
from abc import ABC

from .drift import Drift


class Step(ABC):
    """One reconciliation concern that owns its desired state.

    drift() and apply() default to no-ops (not abstract), so a probe-only step
    overrides just drift(), a cleanup-only step just apply(). The kernel adds
    nothing domain-shaped here: mode, registry, and progress belong in a
    caller-side Step subclass.
    """

    # Step classes (not instances) that must converge before this one. Reconciler orders on it.
    after: tuple[type["Step"], ...] = ()

    def drift(self) -> list[Drift]:
        # Read-only, never mutates. [] means already in desired state. Any deviation belongs here,
        # even one apply() cannot fix. A finding about a system that already meets desired state is
        # advice, not drift - route it to audit().
        return []

    def plan(self) -> list[Drift]:
        # Dry-run preview of the actions apply() would take, without mutating. Where drift() is the
        # deviation (what is wrong), plan() is the intended action. Defaults to drift() since for a
        # fixable step the deviation is the work. Override when they diverge.
        return self.drift()

    def audit(self) -> list[Drift]:
        # Advisory findings about a step that is already in desired state (a stale runtime behind a
        # correct config, a better mode the domain chooses not to force). Read-only, never mutates.
        # [] means nothing to advise. Not defaulted to drift() like plan(): advice is what remains
        # when there is no deviation, so echoing drift here would report every deviation twice. If
        # apply() could fix it, it is drift, not advice.
        return []

    def footprint(self) -> list[Drift]:
        # What this step owns that a teardown would remove: the preview an uninstall shows before
        # pruning, in the same (name, message) shape as every other read. Read-only, never mutates.
        # [] means owns nothing worth listing. What ownership means is the domain's business.
        return []

    def apply(self) -> list[Drift] | None:
        # Idempotent converge toward desired state. Re-running a satisfied step is a no-op. May
        # return what it changed this run (name + message), which converge() collects on its applied
        # channel. None (the default, and every no-op) contributes nothing.
        return None

    def prune(self) -> list[Drift]:
        # The deletion half of apply(): remove what this step owns, and return what survived (its
        # residue) so Reconciler.prune() self-verifies the teardown. [] means clean. Reconciler runs
        # it in reverse order (a dependent down before what it depends on).
        return []
