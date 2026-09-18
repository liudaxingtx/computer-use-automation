"""Human-in-the-loop handoff — a pause / cede / resume state machine on the same
live session (design §8).

This is a *real* control-transfer mechanism, not a TODO: automation detects a
blocked/risky state, pauses itself, an operator takes over the live session,
and automation resumes from where it left off. Control ownership is always
answerable.
"""
from enum import Enum
from typing import Optional


class ControlState(str, Enum):
    AUTOMATION = "AUTOMATION"
    PAUSED = "PAUSED"
    HUMAN = "HUMAN"


class Handoff:
    """Tracks who is in control of a live session, and why control transferred."""

    def __init__(self):
        self.state = ControlState.AUTOMATION
        self.reason: Optional[str] = None
        self.history: list[dict] = []

    def pause(self, reason: str) -> None:
        """Automation detects a blocked/risky state and pauses itself."""
        if self.state != ControlState.AUTOMATION:
            raise RuntimeError(f"cannot pause from state {self.state.value}")
        self.state = ControlState.PAUSED
        self.reason = reason
        self.history.append({"event": "pause", "reason": reason})

    def cede(self, operator: str = "operator") -> None:
        """The operator takes over the *live* session."""
        if self.state != ControlState.PAUSED:
            raise RuntimeError(f"cannot cede from state {self.state.value}")
        self.state = ControlState.HUMAN
        self.history.append({"event": "cede", "operator": operator})

    def resume(self) -> None:
        """The operator signals done; automation resumes."""
        if self.state != ControlState.HUMAN:
            raise RuntimeError(f"cannot resume from state {self.state.value}")
        self.state = ControlState.AUTOMATION
        self.reason = None
        self.history.append({"event": "resume"})

    def who(self) -> str:
        """Answer: who is in control right now?"""
        return self.state.value

    def is_automation(self) -> bool:
        return self.state == ControlState.AUTOMATION

    def is_human(self) -> bool:
        return self.state == ControlState.HUMAN
