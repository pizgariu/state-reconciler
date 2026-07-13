"""Cancellation strategies for a cooperative run stop: Deadline, Flag, and a Quorum composite over Rules."""
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable


class Cancelled(Exception):
    """Raised when a Cancellation fires mid-run, aborting a converge or prune. Partial applies are
    idempotent, so re-running resumes from where it stopped. There is no rollback or snapshot machinery.
    For a domain whose apply() regenerates what prune() removes, an interrupted teardown has two clean
    exits: re-run prune to finish it, or converge to restore."""


class Cancellation:
    # A cooperative stop for a long run. The Executor checks it before each step (Serial) or each level
    # (Parallel) and raises Cancelled if it fires. The base never cancels.
    def cancelled(self) -> bool:
        return False


class Deadline(Cancellation):
    # Cancels once a wall-clock budget (seconds) elapses. The clock starts on the first check, so
    # construction-to-run latency is not counted against the budget.
    def __init__(self, seconds: float):
        if seconds < 0:
            raise ValueError(f"Deadline seconds must be >= 0, got {seconds}")
        self.__seconds = seconds
        self.__started: float | None = None

    def cancelled(self) -> bool:
        now = time.monotonic()
        if self.__started is None:
            self.__started = now
        return now - self.__started >= self.__seconds


class Flag(Cancellation):
    # Cancels when cancel() is called, e.g. from a SIGINT handler or another thread watching the run.
    def __init__(self):
        self.__cancelled = False

    def cancel(self) -> None:
        self.__cancelled = True

    def cancelled(self) -> bool:
        return self.__cancelled


class Rule(ABC):
    # How a Quorum combines its members' fired-or-not states into one cancel-or-continue answer. Given a
    # generator of member states, Some and Every short-circuit it, Most must tally all.
    @abstractmethod
    def __call__(self, fired: Iterable[bool]) -> bool:
        ...


class Some(Rule):
    # Cancel as soon as ANY member fired. Quorum's default.
    def __call__(self, fired: Iterable[bool]) -> bool:
        return any(fired)


class Every(Rule):
    # Cancel only when EVERY member fired. Quorum forbids an empty member set, so the all([]) is True
    # trap cannot fire here.
    def __call__(self, fired: Iterable[bool]) -> bool:
        return all(fired)


class Most(Rule):
    # Cancel when MORE members fired than not: a strict majority, a tie does not cancel.
    def __call__(self, fired: Iterable[bool]) -> bool:
        tally = list(fired)
        return sum(tally) * 2 > len(tally)


class Quorum(Cancellation):
    # A composite Cancellation that fires when an injected Rule is met across its members: Some (the
    # default), Every, Most. A Quorum is itself a Cancellation, so it nests. Members are polled lazily,
    # so Some stops at the first that fired. An empty Quorum is rejected because its answer would hinge
    # on the Rule's vacuous case (Every would fire on all([]) is True).
    def __init__(self, *cancellations: Cancellation, rule: Rule | None = None):
        if not cancellations:
            raise ValueError("Quorum needs at least one cancellation - an empty quorum is ill-defined "
                             "(Every would fire on the vacuous all([]) is True)")
        self.__cancellations = cancellations
        self.__rule = rule or Some()   # None sentinel, never a mutable default instance

    def cancelled(self) -> bool:
        return self.__rule(cancellation.cancelled() for cancellation in self.__cancellations)


class AnyOf(Quorum):
    # Cancel as soon as ANY member fires, the common case. A named Quorum with the Some rule.
    def __init__(self, *cancellations: Cancellation):
        super().__init__(*cancellations, rule=Some())


class AllOf(Quorum):
    # Cancel only once EVERY member has fired.
    def __init__(self, *cancellations: Cancellation):
        super().__init__(*cancellations, rule=Every())


class Majority(Quorum):
    # Cancel when a strict majority of members have fired, a tie does not cancel.
    def __init__(self, *cancellations: Cancellation):
        super().__init__(*cancellations, rule=Most())
