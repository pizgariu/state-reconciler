"""Observer hooks fired around each converge pass, and Chorus composition."""
import unittest

from state_reconciler import Chorus, DriftItem, Observer, Reconciler, Step


class _OneFix(Step):
    # Drifts once, then apply() clears it. Enough to make a converge pass do real work.
    def __init__(self) -> None:
        self.__done = False

    def drift(self) -> list[DriftItem]:
        return [] if self.__done else [DriftItem("x", "needs fix")]

    def apply(self) -> list[DriftItem]:
        changed = self.drift()
        self.__done = True
        return changed


class _Recorder(Observer):
    # Records each hook and its payload names, so a test can assert the order and the contents.
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def began(self) -> None:
        self.events.append(("began",))

    def acted(self, applied) -> None:
        self.events.append(("acted", [item.name for item in applied]))

    def remained(self, residual) -> None:
        self.events.append(("remained", [item.name for item in residual]))


class ObserverTests(unittest.TestCase):
    def test_hooks_fire_in_order_around_a_pass(self):
        recorder = _Recorder()
        Reconciler([_OneFix()], observer=recorder).converge()
        self.assertEqual([event[0] for event in recorder.events], ["began", "acted", "remained"])
        self.assertEqual(recorder.events[1], ("acted", ["x"]))       # acted saw what apply changed
        self.assertEqual(recorder.events[2], ("remained", []))       # nothing remained after the fix

    def test_default_observer_is_a_silent_no_op(self):
        # No observer means the base Observer, which does nothing, so converge behaves as always.
        self.assertEqual(Reconciler([_OneFix()]).converge(), [])

    def test_chorus_relays_to_every_observer(self):
        a, b = _Recorder(), _Recorder()
        Reconciler([_OneFix()], observer=Chorus(a, b)).converge()
        self.assertEqual([event[0] for event in a.events], ["began", "acted", "remained"])
        self.assertEqual([event[0] for event in b.events], ["began", "acted", "remained"])

    def test_chorus_is_itself_an_observer_and_nests(self):
        inner, outer = _Recorder(), _Recorder()
        Reconciler([_OneFix()], observer=Chorus(Chorus(inner), outer)).converge()
        self.assertEqual([event[0] for event in inner.events], ["began", "acted", "remained"])
        self.assertEqual([event[0] for event in outer.events], ["began", "acted", "remained"])

    def test_an_empty_chorus_is_a_no_op(self):
        self.assertEqual(Reconciler([_OneFix()], observer=Chorus()).converge(), [])
