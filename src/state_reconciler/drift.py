"""The drift contract: one thing found out of desired state, read through a two-field protocol."""
from typing import Protocol


class Drift(Protocol):
    # One thing found out of desired state. These two fields are the entire contract a drift exposes,
    # so any domain item satisfies the kernel just by structurally exposing them. Richer payload stays
    # private to the domain item. Read by duck typing, never isinstance-tested: the Protocol names the
    # boundary, it does not gate at runtime.
    name: str     # the subject that drifted (a config file, an env var, a service)
    message: str  # human-readable one-liner: what is wrong


class DriftItem:
    # The kernel's concrete default Drift, for domains with no carrier of their own. A domain with a
    # richer item just exposes name + message on it and uses that instead.
    __slots__ = ("name", "message")

    def __init__(self, name: str, message: str):
        self.name = name
        self.message = message

    def __repr__(self) -> str:
        return f"DriftItem({self.name!r}, {self.message!r})"

    def __str__(self) -> str:
        return f"{self.name}: {self.message}"
