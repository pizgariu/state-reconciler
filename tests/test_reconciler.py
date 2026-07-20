"""Reconciler core lifecycle and Controller tick/run/settle composition."""
import itertools
import unittest

from state_reconciler import Controller, DriftItem, Reconciler, Residual, Step
from support import Boom, Fixable, ReportOnly


class ConvergeTests(unittest.TestCase):
    def test_empty_reconciler_is_clean(self):
        rec = Reconciler(())
        self.assertEqual(rec.drift(), [])
        self.assertEqual(rec.converge(), [])

    def test_converge_applies_then_reports_clean_residual(self):
        f = Fixable()
        rec = Reconciler((f,))
        self.assertEqual(len(rec.drift()), 1)   # opening status: drift present
        self.assertEqual(rec.converge(), [])    # apply -> re-probe -> verified clean
        self.assertTrue(f.applied)

    def test_report_only_step_surfaces_in_residual_not_crash(self):
        residual = Reconciler((ReportOnly(),)).converge()
        self.assertEqual(len(residual), 1)
        self.assertEqual(residual[0].name, "svc")
        self.assertEqual(residual[0].message, "still wrong")

    def test_apply_exception_propagates_not_swallowed(self):
        with self.assertRaises(RuntimeError):
            Reconciler((Boom(),)).converge()

    def test_drift_flattens_in_resolved_order(self):
        class D1(Step):
            def drift(self) -> list:
                return [DriftItem("1", "x")]

        class D2(Step):
            after = (D1,)

            def drift(self) -> list:
                return [DriftItem("2", "y")]

        rec = Reconciler((D2(), D1()))   # shuffled, D1 must come first
        self.assertEqual([d.name for d in rec.drift()], ["1", "2"])

    def test_converge_reports_what_apply_changed_on_the_applied_channel(self):
        # A write-oriented step reports its change from apply(). It lands on applied, NOT the residual.
        class Writer(Step):
            def __init__(self):
                self.__written = False

            def drift(self) -> list:
                return [] if self.__written else [DriftItem("thing.conf", "out of date")]

            def apply(self):
                if self.__written:
                    return None
                self.__written = True
                return [DriftItem("thing.conf", "rewrote /etc/thing.conf")]

        result = Reconciler((Writer(),)).converge()
        self.assertEqual(result, [])                                       # residual clean, still a plain list
        self.assertEqual([c.message for c in result.applied], ["rewrote /etc/thing.conf"])

    def test_applied_is_empty_when_apply_returns_none(self):
        # A step whose apply() returns None (the default, git-hooks style) contributes nothing to applied.
        result = Reconciler((Fixable(),)).converge()
        self.assertEqual(result, [])
        self.assertEqual(result.applied, [])

    def test_a_raising_drift_during_the_reprobe_propagates_and_loses_applied(self):
        # Contract pin: the reads have no OnError policy, so a drift() that raises during converge's
        # re-probe propagates - and the applied record is lost with the exception. A step guarding a
        # real invariant keeps drift() total (return Drift, never raise).
        class Fragile(Step):
            def __init__(self):
                self.touched = False

            def drift(self) -> list:
                if self.touched:
                    raise RuntimeError("probe broke after apply")
                return [DriftItem("f", "needs fix")]

            def apply(self):
                self.touched = True
                return [DriftItem("f", "fixed it")]

        with self.assertRaises(RuntimeError):
            Reconciler((Fragile(),)).converge()


class ResidualTests(unittest.TestCase):
    def test_repr_shows_both_channels(self):
        # The inherited list repr would hide applied entirely - debugging output must show both channels.
        residual = Residual([DriftItem("svc", "still wrong")], [DriftItem("thing.conf", "rewrote it")])
        self.assertIn("applied=", repr(residual))
        self.assertIn("DriftItem('thing.conf', 'rewrote it')", repr(residual))
        self.assertIn("DriftItem('svc', 'still wrong')", repr(residual))


class ControllerTests(unittest.TestCase):
    """Controller drives a Reconciler in a continuous loop, by composition (not inheritance)."""

    class SlowWorld(Step):
        """Reaches desired state only after `passes` converge() calls - external drift settling in time."""

        def __init__(self, passes: int = 3):
            self.n = 0
            self.passes = passes

        def apply(self) -> None:
            self.n += 1

        def drift(self) -> list:
            return [] if self.n >= self.passes else [DriftItem("w", f"{self.n}/{self.passes}")]

    def test_controller_is_composition_not_inheritance(self):
        self.assertFalse(issubclass(Controller, Reconciler))
        self.assertNotIsInstance(Controller(Reconciler(())), Reconciler)

    def test_run_converges_once_per_tick_and_collects_residuals(self):
        seen = []
        controller = Controller(Reconciler((self.SlowWorld(),)), on_residual=seen.append)
        history = controller.run(range(3))
        self.assertEqual([len(residual) for residual in history], [1, 1, 0])  # clean on the 3rd pass
        self.assertEqual(len(seen), 3)               # on_residual fired every pass

    def test_settle_stops_as_soon_as_a_pass_is_clean(self):
        pulled = []

        def ticks():
            for i in range(10):
                pulled.append(i)
                yield i

        residual = Controller(Reconciler((self.SlowWorld(),))).settle(ticks())
        self.assertEqual(residual, [])
        self.assertEqual(len(pulled), 3)             # stopped at 3, did NOT drain all 10 ticks

    def test_settle_drains_ticks_when_never_clean(self):
        class Stuck(Step):
            def drift(self) -> list:
                return [DriftItem("svc", "stuck")]

        residual = Controller(Reconciler((Stuck(),))).settle(range(3))
        self.assertEqual(len(residual), 1)

    def test_settle_with_no_ticks_reports_current_drift(self):
        residual = Controller(Reconciler((self.SlowWorld(),))).settle(range(0))
        self.assertEqual(len(residual), 1)           # opening drift, nothing converged

    def test_run_with_no_ticks_does_nothing(self):
        self.assertEqual(list(Controller(Reconciler((self.SlowWorld(),))).run(range(0))), [])

    def test_run_is_a_lazy_stream_not_an_eager_list(self):
        # run() yields, so an infinite tick source is a forever-loop the caller drives lazily, NOT an eager
        # list that would never return. Pulling two off itertools.count() must not hang or exhaust memory.
        stream = Controller(Reconciler((self.SlowWorld(passes=99),))).run(itertools.count())
        first_two = [len(residual) for residual in itertools.islice(stream, 2)]
        self.assertEqual(len(first_two), 2)
