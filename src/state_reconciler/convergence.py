"""Convergence strategies: how many times to repeat apply -> re-probe. Once (default) and Fixpoint."""
from abc import ABC, abstractmethod
from collections.abc import Callable

from .drift import Drift


class Convergence(ABC):
    """Strategy for how many times to repeat the apply -> re-probe cycle.

    converge is a zero-arg callable that runs one cycle and returns its residual Drift. The
    strategy invokes it as its policy dictates and returns the final residual. Injected at
    construction so the public converge() stays argument-free.
    """
    @abstractmethod
    def __call__(self, converge: Callable[[], list[Drift]]) -> list[Drift]:
        ...


class Once(Convergence):
    """Run exactly one apply -> re-probe cycle."""
    def __call__(self, converge: Callable[[], list[Drift]]) -> list[Drift]:
        return converge()


class Fixpoint(Convergence):
    """Repeat the apply -> re-probe cycle until the residual stops changing or max_passes is reached.

    Some steps only reach desired state once an earlier step's apply() has made room: one pass
    clears what it can, the next clears what the first unblocked, and so on. Settling is by value:
    two residuals are equal when their (name, message) multisets match, so the loop stops as soon
    as a pass changes nothing (converged clean, or genuinely stuck). max_passes is the hard ceiling
    that guarantees termination even when a step never settles.
    """

    def __init__(self, max_passes: int = 10):
        if max_passes < 1:
            raise ValueError(f"Fixpoint max_passes must be >= 1, got {max_passes}")
        self.__max_passes = max_passes

    def __call__(self, converge: Callable[[], list[Drift]]) -> list[Drift]:
        residual = converge()
        for _ in range(self.__max_passes - 1):
            if not residual:
                break  # converged clean
            previous = sorted((item.name, item.message) for item in residual)
            residual = converge()
            if sorted((item.name, item.message) for item in residual) == previous:
                break  # fixed point reached (clean or stuck)
        return residual
