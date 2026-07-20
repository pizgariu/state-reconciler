from state_reconciler import DriftItem, Step


class _RecStep(Step):
    """A step that records its apply() call into a shared log, for ordering assertions. Distinct
    subclasses (A, B, …) are needed because `after` refers to step CLASSES, and the topo-sort keys on
    type(step) - two instances of one class would collapse to a single node."""

    def __init__(self, log: list):
        self._log = log

    def apply(self) -> None:
        self._log.append(type(self).__name__)


class A(_RecStep):
    pass


class B(_RecStep):
    after = (A,)


class C(_RecStep):
    after = (B,)


class X(_RecStep):
    pass


class Y(_RecStep):
    pass


class Z(_RecStep):
    pass


class ReportOnly(Step):
    """A step the reconciler cannot satisfy on its own: drift never clears, apply stays the no-op."""

    def drift(self) -> list:
        return [DriftItem("svc", "still wrong")]


class Fixable(Step):
    """A step that drifts until applied once."""

    def __init__(self):
        self.applied = False

    def drift(self) -> list:
        return [] if self.applied else [DriftItem("f", "needs fix")]

    def apply(self) -> None:
        self.applied = True


class Boom(Step):
    def apply(self) -> None:
        raise RuntimeError("hard I/O failure")
