"""Once single pass versus Fixpoint loop-to-settle."""
import unittest

from state_reconciler import DriftItem, Fixpoint, Reconciler, Step


class ConvergenceTests(unittest.TestCase):
    """The injected Convergence: Once (default, single pass) vs Fixpoint (loop to a fixed point)."""

    class Staged(Step):
        """Clears one unit of drift per apply(), needs `passes` applies to reach desired state."""

        def __init__(self, passes: int):
            self.remaining = passes

        def drift(self) -> list:
            return [] if self.remaining == 0 else [DriftItem("staged", f"{self.remaining} left")]

        def apply(self) -> None:
            if self.remaining:
                self.remaining -= 1

    def test_once_is_the_default_single_pass(self):
        step = self.Staged(3)
        residual = Reconciler((step,)).converge()    # one apply only
        self.assertEqual(step.remaining, 2)
        self.assertEqual(len(residual), 1)

    def test_fixpoint_loops_until_clean(self):
        step = self.Staged(3)
        residual = Reconciler((step,), convergence=Fixpoint()).converge()
        self.assertEqual(step.remaining, 0)
        self.assertEqual(residual, [])

    def test_applied_accumulates_across_fixpoint_passes(self):
        # The applied channel carries EVERY productive pass, not just the last (the accumulator is closed
        # over the cycle closure), and a settled apply() that returns None adds nothing - so no overcount.
        class StagedRecording(Step):
            def __init__(self, passes):
                self.__remaining = passes

            def drift(self) -> list:
                return [] if self.__remaining == 0 else [DriftItem("staged", "more to do")]

            def apply(self):
                if self.__remaining == 0:
                    return None
                self.__remaining -= 1
                return [DriftItem("staged", "did a pass")]

        result = Reconciler((StagedRecording(2),), convergence=Fixpoint()).converge()
        self.assertEqual(result, [])                     # settled clean
        self.assertEqual(len(result.applied), 2)         # both productive passes accumulated, no overcount

    def test_fixpoint_stops_early_when_a_pass_changes_nothing(self):
        # A stuck step settles after the SECOND pass shows no change, not after every max_pass.
        applies = []

        class Stuck(Step):
            def apply(self) -> None:
                applies.append(1)

            def drift(self) -> list:
                return [DriftItem("svc", "stuck")]

        residual = Reconciler((Stuck(),), convergence=Fixpoint(max_passes=10)).converge()
        self.assertEqual(len(applies), 2)            # stopped at 2, not 10
        self.assertEqual(len(residual), 1)

    def test_fixpoint_respects_the_max_passes_ceiling(self):
        # Residual changes every pass (never settles) -> Fixpoint stops at the ceiling, guaranteed.
        applies = []

        class NeverSettles(Step):
            def apply(self) -> None:
                applies.append(1)

            def drift(self) -> list:
                return [DriftItem("n", f"pass {len(applies)}")]   # always a different message

        Reconciler((NeverSettles(),), convergence=Fixpoint(max_passes=4)).converge()
        self.assertEqual(len(applies), 4)

    def test_fixpoint_max_passes_must_be_positive(self):
        for bad in (0, -1):
            with self.subTest(max_passes=bad):
                with self.assertRaises(ValueError):
                    Fixpoint(max_passes=bad)
