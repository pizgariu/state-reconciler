"""Reconciler: resolves Steps once, then reports drift or converges and self-verifies. A Controller drives it in a loop."""
from collections.abc import Callable, Iterable, Iterator

from .cancellation import Cancellation
from .convergence import Convergence, Once
from .drift import Drift
from .executor import Executor, Serial
from .ordering import Kahn, Ordering
from .step import Step


class Residual(list[Drift]):
    """The list converge() returns (empty == verified success), also carrying the applied channel.

    It is a list of the residual Drift, so callers that test truthiness, iterate, or compare
    against [] behave unchanged. `applied` records what apply() changed this run: the transaction
    summary the residual cannot give, since the residual answers "what is STILL wrong", not "what
    did you touch".
    """
    def __init__(self, residual: list[Drift], applied: list[Drift]):
        super().__init__(residual)
        self.applied: list[Drift] = list(applied)

    def __repr__(self) -> str:
        # The inherited list repr hides the applied channel, so show both.
        return f"Residual({super().__repr__()}, applied={self.applied!r})"


class Reconciler:
    """Resolves an explicit, ordered set of Steps once, then either reports drift (read-only) or
    converges actual -> desired (idempotent) and self-verifies by re-probing for the residual.

    No registry, no auto-discovery, no capability probing: the kernel takes the steps it is handed.
    Ordering is an injected strategy (Kahn by default). The self-verifying converge is the core.
    """

    def __init__(self, steps: Iterable[Step], ordering: Ordering | None = None, *,
                 executor: Executor | None = None, convergence: Convergence | None = None,
                 cancellation: Cancellation | None = None):
        # Resolve defaults here, not as mutable default args: a default instance in the signature
        # would be built once at import and shared across every Reconciler, a trap the moment a
        # default holds state (a pool, a flag).
        ordering = ordering or Kahn()
        executor = executor or Serial()
        convergence = convergence or Once()
        cancellation = cancellation or Cancellation()
        # The executor builds and verifies the partition shape it can run (Serial: a serial walk of
        # levels, Parallel: independent waves, Pipeline: independent chains). An executor that cannot
        # run the Ordering it was handed raises from arrange(), naming the fix.
        self.__partition = executor.arrange(ordering, tuple(steps))
        self.__executor = executor
        self.__convergence = convergence
        self.__cancellation = cancellation

    def drift(self) -> list[Drift]:
        # Flatten every step's drift, in resolved order. [] == fully in desired state.
        return self.__probe(self.__partition, lambda step: step.drift())

    def plan(self) -> list[Drift]:
        # The dry run: what converge WOULD do, without doing it. Flatten every step's plan() preview
        # in resolved order, reusing the Drift channel. [] == nothing to do. Read-only, so it is safe
        # to call before converge() to show the work.
        return self.__probe(self.__partition, lambda step: step.plan())

    def audit(self) -> list[Drift]:
        # The advisory read: flatten every step's audit() (findings about a system that meets desired
        # state yet still deserves attention) in resolved order, through the same read engine as drift
        # and plan. [] == nothing to advise. converge() never calls this and its findings never enter
        # the residual, so the empty residual stays the proof desired state was reached. A consumer
        # opts in by calling audit() itself, typically alongside drift().
        return self.__probe(self.__partition, lambda step: step.audit())

    def footprint(self) -> list[Drift]:
        # The teardown preview: everything the steps own that exists now, flattened in the order
        # prune() would tear it down. Read-only through the same engine as the other reads, and
        # prune() never consults it.
        return self.__probe(self.__partition.inverse(), lambda step: step.footprint())

    def converge(self) -> Residual:
        # Apply every step and re-probe for what is STILL out of desired state. The returned residual
        # is the proof it worked, `applied` the record of what changed.
        #
        # CQS: a command returning its own outcome (this run's failures + a fresh re-probe + applied),
        # none reconstructible by a later read. Splitting into a query would need a persistent store to
        # read the post-state back, which this kernel deliberately lacks: persistence is a consumer
        # concern. The pure queries are drift/plan/audit.
        #
        # The injected Convergence strategy decides how many times to repeat the apply -> re-probe
        # cycle: Once (default) runs it a single time, Fixpoint loops until the residual settles, for
        # steps that only come good once an earlier step's apply() has cleared the way.
        #
        # A step's apply() may RAISE (FailFast propagates, BestEffort records it as residual Drift) and
        # may optionally return what it changed. The executor hands those changes back as its first
        # list, apart from the failures, so we accumulate them into `applied` here on the calling thread
        # across every pass and keep them OUT of the residual, preserving empty-list == verified-success.
        # Collecting on this thread (not in the executor's workers) keeps applied race-free and in
        # resolved order under a fanning executor. A report-only step returns None and contributes
        # nothing here, so the re-probe still surfaces it in the residual.
        applied: list[Drift] = []

        def cycle() -> list[Drift]:
            applied_this_pass, failures = self.__execute(self.__partition, lambda step: step.apply())
            applied.extend(applied_this_pass)
            return failures + self.drift()

        return Residual(self.__convergence(cycle), applied)

    def prune(self) -> list[Drift]:
        # The deletion half, the mirror of converge: run every step's prune() in REVERSE resolved order
        # (tear a dependent down before the thing it depends on) through the same executor, so the
        # OnError policy and the serial/parallel choice apply identically.
        #
        # CQS: like converge, a command returning its own outcome, the residue that survived teardown
        # ([] == everything gone). Even less splittable, since prune has no re-probe.
        #
        # Self-verifying: each prune() removes its artifact and returns what SURVIVED, which the executor
        # collects, so the returned residual is the proof teardown worked. drift() is deliberately NOT
        # re-probed here: it measures deviation from the should-EXIST state, so after teardown it would
        # report everything as "missing" (noise, not proof). Concatenate the survived residue with any
        # hard executor failures under BestEffort. The residual is a (name, message) proof set, so the
        # order of the two groups within it does not matter.
        residue, failures = self.__execute(self.__partition.inverse(), lambda step: step.prune())
        return residue + failures

    @staticmethod
    def __probe(groups: tuple[tuple[Step, ...], ...], read: Callable[[Step], list[Drift]]) -> list[Drift]:
        # The single READ engine: flatten a read-only per-step query over every step. drift, plan and
        # audit walk the resolved partition, footprint walks the teardown order. All four are one shape
        # (a pure read returning Drift), so they share this flattener and differ only in the method and
        # the direction the caller hands in. A group is a level (waves) or a chain (pipelines).
        return [item for group in groups for step in group for item in read(step)]

    def __execute(self, groups: tuple[tuple[Step, ...], ...], do: Callable[[Step], list[Drift] | None]) -> tuple[list[Drift], list[Drift]]:
        # The single WRITE engine: sequence steps, funnelling both converge (forward partition,
        # do = apply) and prune (reversed partition, do = prune) through the injected Executor. The
        # Executor owns HOW (serial, level-parallel or chain-pipelined) and the OnError policy, and
        # returns two lists apart: (do-returns, failures). The direction is the caller's.
        return self.__executor.execute(groups, do, self.__cancellation)


class Controller:
    """A continuous control loop composed over a Reconciler (has-a, not is-a).

    A Reconciler converges once against one observation. A Controller drives it repeatedly, re-running
    the whole converge() across ticks and RE-OBSERVING the world each time, so it catches external drift
    that reappears after a fix. (Contrast Fixpoint, which repeats apply -> re-probe within a single
    converge() against the same observation.)

    Driven, not self-timing: run()/settle() walk an injected `ticks` iterable, converging once per tick,
    so the kernel stays clock-free.
    """

    def __init__(self, reconciler: Reconciler, *, on_residual: Callable[[Residual], None] | None = None):
        # on_residual, if given, is called with each pass's residual as it happens: the hook a
        # long-running controller uses to log, alert, or export metrics.
        self.__reconciler = reconciler
        self.__on_residual = on_residual

    def __tick(self) -> Residual:
        residual = self.__reconciler.converge()
        if self.__on_residual is not None:
            self.__on_residual(residual)
        return residual

    def run(self, ticks: Iterable) -> Iterator[Residual]:
        # Converge once per tick, yielding each pass's residual as it happens. Lazy on purpose: an
        # infinite `ticks` (itertools.count()) makes this a forever-loop the caller drives one tick at
        # a time. Wrap a finite run in list() for every residual, or just drive it for the side effects.
        for _ in ticks:
            yield self.__tick()

    def settle(self, ticks: Iterable) -> Residual:
        # The bounded twin of run(): converge each tick until one comes back CLEAN (empty residual) or
        # the ticks run out, then return the final residual ([] == reached desired state). With zero
        # ticks it reports the current drift, wrapped so the return is a Residual on every path.
        residual = Residual(self.__reconciler.drift(), [])  # opening status, in case ticks is empty
        for _ in ticks:
            residual = self.__tick()
            if not residual:
                break
        return residual
