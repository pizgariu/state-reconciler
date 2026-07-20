"""Serial and Parallel execution semantics: FailFast vs BestEffort, per-wave fan-out."""
import unittest

from state_reconciler import (
    DFS,
    DriftItem,
    Executor,
    Kahn,
    Levels,
    OnError,
    Ordering,
    Parallel,
    Reconciler,
    Serial,
    Step,
)
from support import A, B, Boom, C, X, Y, Z


class ExecutorTests(unittest.TestCase):
    """The injected Executor: Serial (default) and Parallel, plus the OnError failure policy."""

    def test_serial_is_the_default_and_fails_fast(self):
        # No executor -> Serial(FailFast): an apply() exception aborts the whole converge.
        with self.assertRaises(RuntimeError):
            Reconciler((Boom(),)).converge()

    def test_best_effort_collects_failures_as_residual_drift(self):
        # BestEffort catches the exception, records it as Drift, and keeps applying the rest.
        log = []

        class Ok(Step):
            def apply(self) -> None:
                log.append("ok")

        residual = Reconciler((Boom(), Ok()), executor=Serial(OnError.BestEffort)).converge()
        self.assertEqual(log, ["ok"])                 # sibling still ran after Boom blew up
        self.assertEqual(len(residual), 1)
        self.assertIn("step failed", residual[0].message)

    def test_parallel_preserves_dependency_order_across_levels(self):
        # The level barrier keeps every Step.after edge even while fanning each level out.
        log = []
        Reconciler((C(log), A(log), B(log)), Kahn(), executor=Parallel()).converge()
        self.assertEqual(log, ["A", "B", "C"])

    def test_parallel_runs_every_independent_step(self):
        # One level of independents: order within is unspecified, but all must run.
        log = []
        Reconciler((X(log), Y(log), Z(log)), Kahn(), executor=Parallel()).converge()
        self.assertEqual(sorted(log), ["X", "Y", "Z"])

    def test_parallel_failfast_reraises_on_the_calling_thread(self):
        with self.assertRaises(RuntimeError):
            Reconciler((Boom(),), Kahn(), executor=Parallel()).converge()

    def test_parallel_best_effort_collects_failures(self):
        residual = Reconciler((Boom(),), Kahn(), executor=Parallel(OnError.BestEffort)).converge()
        self.assertEqual(len(residual), 1)
        self.assertIn("step failed", residual[0].message)

    def test_reconciler_rejects_parallel_with_a_flat_only_ordering(self):
        # DFS yields no real levels (one-wave fallback), so Parallel would ignore after= -> refuse at build.
        with self.assertRaises(ValueError) as ctx:
            Reconciler((A([]),), DFS(), executor=Parallel())
        self.assertIn("Parallel", str(ctx.exception))

    def test_the_wave_shape_and_guard_live_in_arrange_on_any_executor(self):
        # No capability flag: each executor owns its shape via arrange(). A custom executor that builds a
        # wave partition and verifies it rejects a flat-only Ordering (the one-wave fallback fails
        # verify on dependent steps) and accepts real Kahn waves - tied to arrange(), not to
        # the Parallel class.
        class WaveExecutor(Executor):
            def arrange(self, ordering, steps):
                waves = Levels(ordering.levels(steps))
                waves.verify()
                return waves

            def execute(self, groups, do, cancellation):
                return [], []

        with self.assertRaises(ValueError):
            Reconciler((A([]), B([])), DFS(), executor=WaveExecutor())   # DFS one-wave fallback: B beside dep A
        Reconciler((A([]), B([])), Kahn(), executor=WaveExecutor())      # Kahn splits A then B - independent

    def test_guard_rejects_a_custom_ordering_whose_waves_violate_after(self):
        # A level-aware Ordering that overrides levels() but returns one wave of DEPENDENT steps passes the
        # capability check, yet a fanning executor would run them together and ignore after=. The structural
        # invariant catches the mis-split at construction.
        class OneBigWave(Ordering):
            def __call__(self, steps):
                return steps

            def levels(self, steps):
                return (tuple(steps),)   # everything in one wave, dependencies be damned

        with self.assertRaises(ValueError):
            Reconciler((A([]), B([])), OneBigWave(), executor=Parallel())   # B is after A, same wave

    def test_dfs_with_serial_is_allowed(self):
        # DFS is serial-only, and serial is fine: it still resolves the chain.
        log = []
        Reconciler((C(log), A(log), B(log)), DFS(), executor=Serial()).converge()
        self.assertEqual(log, ["A", "B", "C"])

    def test_parallel_reuses_one_pool_across_runs(self):
        # The pool is created once and reused, not rebuilt per run() (which a Fixpoint converge would do
        # every pass). Two converges on the same executor must hit the same pool object.
        executor = Parallel()
        rec = Reconciler((X([]), Y([]), Z([])), Kahn(), executor=executor)
        rec.converge()
        pool_after_first = executor._PooledExecutor__pool   # pool plumbing lives on the shared base now
        rec.converge()
        self.assertIsNotNone(pool_after_first)
        self.assertIs(pool_after_first, executor._PooledExecutor__pool)

    def test_parallel_as_context_manager_closes_the_pool_on_exit(self):
        # __enter__ hands back the executor, __exit__ calls close() which releases the worker threads
        # deterministically. A long-lived owner uses this instead of leaning on garbage collection.
        with Parallel() as executor:
            Reconciler((X([]), Y([]), Z([])), Kahn(), executor=executor).converge()
            self.assertIsNotNone(executor._PooledExecutor__pool)   # the run spun the pool up
        self.assertIsNone(executor._PooledExecutor__pool)          # __exit__ -> close() released it

    def test_close_is_a_no_op_when_no_pool_was_ever_created(self):
        # A short-lived executor that never ran has no pool, so close() returns without touching one.
        executor = Parallel()
        executor.close()
        self.assertIsNone(executor._PooledExecutor__pool)

    def test_parallel_routes_what_apply_returns_to_the_applied_channel(self):
        # A step whose apply() returns what it changed: the executor hands those back as its returns list,
        # and converge routes them to the applied channel, keeping them out of the residual.
        class Applied(Step):
            def apply(self):
                return [DriftItem("svc", "created")]

        residual = Reconciler((Applied(),), Kahn(), executor=Parallel()).converge()
        self.assertEqual(residual, [])                                       # nothing still wrong
        self.assertEqual([item.message for item in residual.applied], ["created"])
