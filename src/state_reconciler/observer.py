"""Observer: optional trace hooks fired around each converge pass, composed via Chorus."""
from .drift import Drift


class Observer:
    """Trace hooks the Reconciler fires around each converge pass. Subclass and override only the hooks
    you need, the rest stay no-ops, so a reconciler with the default observer behaves byte-for-byte as
    one with none. It is deliberately not abstract for that reason. Every hook runs on the calling thread
    after the executor has collected its results, so an implementation never faces the worker threads.
    Trace what was acted on and what the world still shows, without instrumenting a single Step by hand.
    """

    def began(self) -> None:
        # A converge pass is about to apply its steps.
        ...

    def acted(self, applied: list[Drift]) -> None:
        # What this pass's apply() changed, in resolved order.
        ...

    def remained(self, residual: list[Drift]) -> None:
        # The drift the re-probe still finds after the pass, what remained between the world and desired.
        ...


class Chorus(Observer):
    """An Observer that relays each hook to several observers in order. This is the composition seam,
    the way Quorum composes cancellations, so one run can trace to a log and a metrics sink at once. A
    Chorus is itself an Observer, so it nests."""

    def __init__(self, *observers: Observer):
        self.__observers = observers

    def began(self) -> None:
        for observer in self.__observers:
            observer.began()

    def acted(self, applied: list[Drift]) -> None:
        for observer in self.__observers:
            observer.acted(applied)

    def remained(self, residual: list[Drift]) -> None:
        for observer in self.__observers:
            observer.remained(residual)
