"""Read and teardown step methods: plan, audit, footprint, and prune."""
import unittest

from state_reconciler import DriftItem, OnError, Reconciler, Serial, Step
from support import A, Fixable, _RecStep


class PlanTests(unittest.TestCase):
    """plan() is the dry-run: a read-only preview of converge's work, carried on the Drift channel."""

    def test_plan_defaults_to_drift(self):
        class Fixable(Step):
            def drift(self) -> list:
                return [DriftItem("f", "needs fix")]

            def apply(self) -> None:
                pass

        rec = Reconciler((Fixable(),))
        self.assertEqual([(d.name, d.message) for d in rec.plan()],
                         [(d.name, d.message) for d in rec.drift()])

    def test_plan_does_not_mutate(self):
        class Fixable(Step):
            def __init__(self):
                self.applied = False

            def drift(self) -> list:
                return [] if self.applied else [DriftItem("f", "x")]

            def apply(self) -> None:
                self.applied = True

        f = Fixable()
        rec = Reconciler((f,))
        rec.plan()
        rec.plan()
        self.assertFalse(f.applied)                  # plan() is read-only, never applies

    def test_custom_plan_is_distinct_from_drift(self):
        class Rewrite(Step):
            def drift(self) -> list:
                return [DriftItem("cfg", "content differs")]

            def plan(self) -> list:
                return [DriftItem("cfg", "would rewrite 3 lines")]

        rec = Reconciler((Rewrite(),))
        self.assertEqual(rec.drift()[0].message, "content differs")
        self.assertEqual(rec.plan()[0].message, "would rewrite 3 lines")

    def test_plan_flattens_in_resolved_order(self):
        class PA(Step):
            def plan(self) -> list:
                return [DriftItem("A", "a")]

        class PB(Step):
            after = (PA,)

            def plan(self) -> list:
                return [DriftItem("B", "b")]

        rec = Reconciler((PB(), PA()))               # shuffled, PA must come first
        self.assertEqual([d.name for d in rec.plan()], ["A", "B"])

    def test_report_only_step_can_plan_nothing(self):
        class ReportOnly(Step):
            def drift(self) -> list:
                return [DriftItem("svc", "still wrong")]

            def plan(self) -> list:
                return []                            # apply is a no-op, so nothing is planned

        rec = Reconciler((ReportOnly(),))
        self.assertEqual(len(rec.drift()), 1)
        self.assertEqual(rec.plan(), [])


class AuditTests(unittest.TestCase):
    """audit() is the advisory read channel: findings about a system IN desired state that still
    deserve attention. Opt-in per step, flattened by the same read engine as drift and plan, and
    invisible to converge - advice never dirties the residual proof."""

    class Advising(Step):
        def audit(self) -> list:
            return [DriftItem("hint", "a better mode is available")]

    def test_audit_defaults_to_no_advice_and_never_echoes_drift(self):
        # A drifting step with no audit() override advises nothing - advice is opt-in, not a drift echo.
        rec = Reconciler((Fixable(),))
        self.assertEqual(len(rec.drift()), 1)
        self.assertEqual(rec.audit(), [])

    def test_audit_flattens_in_resolved_order(self):
        class AdviseFirst(Step):
            def audit(self) -> list:
                return [DriftItem("first", "x")]

        class AdviseSecond(Step):
            after = (AdviseFirst,)

            def audit(self) -> list:
                return [DriftItem("second", "y")]

        rec = Reconciler((AdviseSecond(), AdviseFirst()))   # shuffled, after= must order the advice
        self.assertEqual([item.name for item in rec.audit()], ["first", "second"])

    def test_converge_never_touches_audit_findings(self):
        # An advising step in desired state: converge proves clean (empty residual, empty applied)
        # while the advice stays fully readable on its own channel.
        rec = Reconciler((self.Advising(),))
        result = rec.converge()
        self.assertEqual(result, [])
        self.assertEqual(result.applied, [])
        self.assertEqual([item.message for item in rec.audit()], ["a better mode is available"])


class FootprintTests(unittest.TestCase):
    """footprint() is the teardown preview: what the steps own that exists right now, flattened in the
    order prune() would tear it down. Opt-in per step (default []), read-only, and converge never
    consults it."""

    def test_footprint_defaults_to_nothing(self):
        self.assertEqual(Reconciler((A([]),)).footprint(), [])

    def test_footprint_lists_in_teardown_order(self):
        class Base(Step):
            def footprint(self):
                return [DriftItem("base", "removed")]

        class Dependent(Step):
            after = (Base,)

            def footprint(self):
                return [DriftItem("dependent", "removed")]

        rec = Reconciler((Base(), Dependent()))
        self.assertEqual([item.name for item in rec.footprint()], ["dependent", "base"])   # prune's order

    def test_footprint_is_read_only_and_invisible_to_converge(self):
        log = []

        class Owning(_RecStep):
            def footprint(self):
                return [DriftItem("thing", "removed")]

        rec = Reconciler((Owning(log),))
        self.assertEqual(len(rec.footprint()), 1)
        self.assertEqual(log, [])                # the preview ran nothing
        self.assertEqual(rec.converge(), [])


class PruneTests(unittest.TestCase):
    """prune() is the deletion half: Reconciler.prune() runs step.prune() in REVERSE resolved order."""

    def test_prune_runs_in_reverse_of_build_order(self):
        log = []

        class PA(Step):
            def prune(self) -> None:
                log.append("A")

        class PB(Step):
            after = (PA,)

            def prune(self) -> None:
                log.append("B")

        class PC(Step):
            after = (PB,)

            def prune(self) -> None:
                log.append("C")

        Reconciler((PC(), PA(), PB())).prune()
        self.assertEqual(log, ["C", "B", "A"])       # teardown is the inverse of build order

    def test_build_forward_then_tear_down_backward(self):
        applied, pruned = [], []

        class A2(Step):
            def apply(self) -> None:
                applied.append("A2")

            def prune(self) -> None:
                pruned.append("A2")

        class B2(Step):
            after = (A2,)

            def apply(self) -> None:
                applied.append("B2")

            def prune(self) -> None:
                pruned.append("B2")

        rec = Reconciler((B2(), A2()))
        rec.converge()
        rec.prune()
        self.assertEqual(applied, ["A2", "B2"])
        self.assertEqual(pruned, ["B2", "A2"])

    def test_prune_goes_through_the_executor_best_effort(self):
        log = []

        class BoomPrune(Step):
            def prune(self) -> None:
                raise RuntimeError("rm failed")

        class OkPrune(Step):
            def prune(self) -> None:
                log.append("ok")

        failures = Reconciler((BoomPrune(), OkPrune()), executor=Serial(OnError.BestEffort)).prune()
        self.assertEqual(log, ["ok"])
        self.assertEqual(len(failures), 1)

    def test_prune_default_is_a_no_op(self):
        class CreateOnly(Step):
            def apply(self) -> None:
                pass

        self.assertEqual(Reconciler((CreateOnly(),)).prune(), [])

    def test_prune_returns_the_residue_a_step_reports(self):
        # Self-verification: prune() returns whatever a step says SURVIVED its teardown.
        class Stubborn(Step):
            def prune(self) -> list:
                return [DriftItem("artifact", "survived teardown")]

        residual = Reconciler((Stubborn(),)).prune()
        self.assertEqual([(d.name, d.message) for d in residual], [("artifact", "survived teardown")])

    def test_prune_is_clean_when_every_step_reports_no_residue(self):
        # A step that removed everything returns [] - the empty residual is the proof of a clean teardown.
        class CleanRemove(Step):
            def prune(self) -> list:
                return []

        self.assertEqual(Reconciler((CleanRemove(),)).prune(), [])

    def test_prune_surfaces_both_residue_and_best_effort_failures(self):
        # Residue (a soft "survived" return) and a hard prune() exception both land in the residual.
        class Survivor(Step):
            def prune(self) -> list:
                return [DriftItem("a", "survived teardown")]

        class BoomPrune(Step):
            def prune(self) -> list:
                raise RuntimeError("rm failed")

        residual = Reconciler((Survivor(), BoomPrune()), executor=Serial(OnError.BestEffort)).prune()
        names = {d.name for d in residual}
        self.assertIn("a", names)            # the soft residue
        self.assertIn("BoomPrune", names)
