"""Executor strategies - HOW a Partition is run: Serial, plus pooled Parallel (waves) and Pipeline (chains)."""
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum
from multiprocessing.dummy import Pool

from .cancellation import Cancellation, Cancelled
from .drift import Drift, DriftItem
from .ordering import Ordering
from .partition import Chains, Levels, Partition
from .step import Step


class OnError(Enum):
    # What the Executor does when a step's apply() (or prune()) raises.
    FailFast = "FailFast"      # let it propagate and abort the run
    BestEffort = "BestEffort"  # catch it, record it as Drift, keep going with the rest of the run


class Executor(ABC):
    # Strategy for HOW a resolved set of steps is run. Serial walks them on one thread, Parallel fans each
    # level to a pool, Pipeline fans each independent chain. The Executor is the ONLY thing that invokes a
    # step, so it is the ONLY thing that can catch, which is why the OnError policy lives here. It checks the
    # injected cancellation before each unit of work and raises Cancelled if it fires.
    #
    # execute() returns TWO lists, kept apart on purpose: (returns, failures). `returns` is everything do()
    # itself handed back (converge's applied items, prune's surviving residue), `failures` is the exception
    # drift (empty under FailFast, which re-raises instead of collecting). Returning them apart lets converge
    # route its applied items to their own channel, and lets prune concatenate them since for a teardown both
    # are residual.

    def arrange(self, ordering: Ordering, steps: tuple[Step, ...]) -> Partition:
        # The executor builds the Partition SHAPE it can run, from the injected Ordering. It alone knows how
        # it fans out, so it alone knows what shape it needs and how to verify it. The DEFAULT is serial-safe:
        # a level partition (real waves from Kahn, the one-wave fallback from DFS/Components) walked in order
        # on one thread, honouring Step.after with no verification required. A fanning executor OVERRIDES this
        # to demand its shape (waves for Parallel, chains for Pipeline), reject an Ordering that cannot produce
        # it, and verify the partition upfront.
        return Levels(ordering.levels(steps))

    @abstractmethod
    def execute(self, groups: tuple[tuple[Step, ...], ...], do: Callable[[Step], list[Drift] | None], cancellation: Cancellation) -> tuple[list[Drift], list[Drift]]:
        ...


class Serial(Executor):
    """Default executor: every level in order, every step within a level in order, on one thread.
    FailFast (default) lets an apply() exception propagate."""

    def __init__(self, on_error: OnError = OnError.FailFast):
        self.__on_error = on_error

    def execute(self, levels: tuple[tuple[Step, ...], ...], do: Callable[[Step], list[Drift] | None], cancellation: Cancellation) -> tuple[list[Drift], list[Drift]]:
        returns: list[Drift] = []
        failures: list[Drift] = []
        for level in levels:
            for step in level:
                if cancellation.cancelled():
                    raise Cancelled(f"Run cancelled by {type(cancellation).__name__}")
                try:
                    produced = do(step)
                except Exception as exception:
                    if self.__on_error is OnError.FailFast:
                        raise
                    failures.append(DriftItem(type(step).__name__, f"step failed: {type(exception).__name__}: {exception}"))
                else:
                    if produced:  # prune's residue (what survived), or converge's applied items - apply's no-op returns None
                        returns.extend(produced)
        return returns, failures


class _PooledExecutor(Executor):
    """Shared thread-pool plumbing for the two fanning executors: Parallel fans the steps within a level,
    Pipeline fans the independent chains. One Pool per instance (multiprocessing.dummy.Pool, the threaded
    twin of multiprocessing.Pool), created lazily on first run and reused across runs, released by close()
    or the context manager.

    Threads, not processes: a Step's apply() is almost always I/O (files, subprocesses, network) where the
    GIL is released and threads genuinely overlap, and Steps are ordinary live objects that need not pickle.
    The fence: this makes the fan I/O-parallel, not CPU-parallel. A domain whose steps grind pure-Python
    computation serializes on the GIL (until a free-threaded build changes that), and should inject its own
    Executor (a process pool needs picklable steps and a module-level do)."""

    def __init__(self, on_error: OnError = OnError.FailFast, *, width: int | None = None):
        # width is the pool size (None defaults to os.cpu_count()). on_error matches Serial.
        self._on_error = on_error  # protected: each subclass's execute() reads it
        self.__width = width
        self.__pool = None

    def _ensure_pool(self):
        # One pool per instance, created on first use and reused across execute() calls: a Fixpoint converge
        # calls execute() once per pass, so a fresh pool each pass would spin worker threads up and down
        # repeatedly. Closed by close() or the context manager, else finalised on garbage collection.
        if self.__pool is None:
            self.__pool = Pool(self.__width)
        return self.__pool

    def close(self) -> None:
        # Release the worker threads deterministically (close then join). A short-lived caller can skip this
        # and let the pool finalise on garbage collection, but a long-lived owner (a Controller looping for
        # hours) should close it, which is what the context-manager protocol below wraps.
        if self.__pool is not None:
            self.__pool.close()
            self.__pool.join()
            self.__pool = None

    def __enter__(self):
        return self

    def __exit__(self, *exception) -> None:
        self.close()


class Parallel(_PooledExecutor):
    """Runs each topo LEVEL concurrently on the pool, with the levels themselves still walked in dependency
    order. The steps WITHIN one level are mutually independent by construction, so fanning them out is safe,
    and the barrier between levels preserves every Step.after edge."""

    def arrange(self, ordering: Ordering, steps: tuple[Step, ...]) -> Levels:
        # Demands real waves: an Ordering that does not override levels() only yields the one-wave fallback
        # (its whole graph in a single wave), which fanned out would ignore Step.after. Reject it as a class
        # (the unbound override check against the ABC names no concrete strategy), then verify the waves the
        # Ordering actually produced are independent before running.
        if type(ordering).levels is Ordering.levels:
            raise ValueError(
                f"{type(self).__name__} needs a level-aware Ordering that splits steps into independent "
                f"waves. {type(ordering).__name__} only yields a flat order (its levels() is the one-wave "
                f"fallback), so fanning it out would ignore Step.after. Use Kahn."
            )
        partition = Levels(ordering.levels(steps))
        partition.verify()
        return partition

    def execute(self, levels: tuple[tuple[Step, ...], ...], do: Callable[[Step], list[Drift] | None], cancellation: Cancellation) -> tuple[list[Drift], list[Drift]]:
        # attempt() ALWAYS catches, even under FailFast, so an apply() blowing up in a worker thread surfaces
        # back on THIS thread as a clean value rather than a stray cross-thread exception, and carries back the
        # step's own return. The level is a barrier: every step in it runs to completion before we inspect
        # outcomes, so under FailFast we re-raise the first failure only after the level finishes.
        def attempt(_step):
            try:
                return _step, do(_step), None
            except Exception as _exception:
                return _step, None, _exception

        returns: list[Drift] = []
        failures: list[Drift] = []
        pool = self._ensure_pool()
        for level in levels:
            if cancellation.cancelled():
                raise Cancelled(f"Run cancelled by {type(cancellation).__name__}")
            # pool.map preserves input order, so `returns` builds in resolved step order on THIS thread.
            for step, produced, exception in pool.map(attempt, level):
                if exception is not None:
                    if self._on_error is OnError.FailFast:
                        raise exception
                    failures.append(DriftItem(type(step).__name__, f"step failed: {type(exception).__name__}: {exception}"))
                elif produced:  # prune's residue (what survived), or converge's applied items - apply's no-op returns None
                    returns.extend(produced)
        return returns, failures


class Pipeline(_PooledExecutor):
    """The dual of Parallel: runs each independent CHAIN concurrently on the pool, the steps WITHIN a chain
    in series. Parallel fans the steps inside a level and bars between levels, Pipeline fans the chains and
    serialises inside each. Correct only when the chains share no Step.after edge, which the Reconciler proves
    upfront via the partition's verify(), so a chain never waits on another and needs no barrier."""

    def arrange(self, ordering: Ordering, steps: tuple[Step, ...]) -> Chains:
        # Demands independent chains: an Ordering that does not override chains() only yields the one-chain
        # fallback (its whole graph in a single chain), which pipelined would run nothing concurrently. Reject
        # it as a class (the unbound override check against the ABC), then verify no Step.after edge crosses
        # between the chains the Ordering produced before running.
        if type(ordering).chains is Ordering.chains:
            raise ValueError(
                f"{type(self).__name__} needs a chain-aware Ordering that splits steps into independent "
                f"chains. {type(ordering).__name__} only yields one chain of everything (its chains() is "
                f"the fallback), so pipelining it would run nothing concurrently. Use Components."
            )
        partition = Chains(ordering.chains(steps))
        partition.verify()
        return partition

    def execute(self, chains: tuple[tuple[Step, ...], ...], do: Callable[[Step], list[Drift] | None], cancellation: Cancellation) -> tuple[list[Drift], list[Drift]]:
        # run_chain walks ONE chain in series on a worker thread, checking cancellation between its steps (a
        # chain can be long, unlike a level's single fan). It ALWAYS catches, like Parallel's attempt(): a step
        # blowing up, or a cancellation firing, comes back as a clean value on THIS thread. pool.map is a
        # barrier over the chains and preserves their order, so returns build in resolved order and the FIRST
        # chain (in order) that failed or cancelled is the one re-raised.
        def run_chain(chain):
            produced_all: list[Drift] = []
            failures_all: list[Drift] = []
            for step in chain:
                if cancellation.cancelled():
                    return produced_all, failures_all, Cancelled(f"Run cancelled by {type(cancellation).__name__}")
                try:
                    produced = do(step)
                except Exception as exception:
                    if self._on_error is OnError.FailFast:
                        return produced_all, failures_all, exception
                    failures_all.append(DriftItem(type(step).__name__, f"step failed: {type(exception).__name__}: {exception}"))
                else:
                    if produced:  # prune's residue, or converge's applied items - apply's no-op returns None
                        produced_all.extend(produced)
            return produced_all, failures_all, None

        returns: list[Drift] = []
        failures: list[Drift] = []
        pool = self._ensure_pool()
        for chain_returns, chain_failures, error in pool.map(run_chain, chains):
            if error is not None:
                raise error   # a Cancelled (under any on_error), or under FailFast the chain's first failure
            returns.extend(chain_returns)
            failures.extend(chain_failures)
        return returns, failures
