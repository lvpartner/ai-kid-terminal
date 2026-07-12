from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic


class TurnState(StrEnum):
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    RESEARCHING = "researching"
    VALIDATING = "validating"
    SYNTHESIZING = "synthesizing"
    PLAYING = "playing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


TERMINAL_STATES = {TurnState.COMPLETED, TurnState.INTERRUPTED, TurnState.FAILED}
ALLOWED_TRANSITIONS = {
    TurnState.RECORDING: {TurnState.TRANSCRIBING, TurnState.INTERRUPTED, TurnState.FAILED},
    TurnState.TRANSCRIBING: {TurnState.RESEARCHING, TurnState.INTERRUPTED, TurnState.FAILED},
    TurnState.RESEARCHING: {TurnState.VALIDATING, TurnState.INTERRUPTED, TurnState.FAILED},
    TurnState.VALIDATING: {TurnState.SYNTHESIZING, TurnState.INTERRUPTED, TurnState.FAILED},
    TurnState.SYNTHESIZING: {
        TurnState.PLAYING,
        TurnState.COMPLETED,
        TurnState.INTERRUPTED,
        TurnState.FAILED,
    },
    TurnState.PLAYING: {TurnState.COMPLETED, TurnState.INTERRUPTED, TurnState.FAILED},
}


@dataclass
class TurnStateMachine:
    state: TurnState = TurnState.RECORDING
    entered_at: float = field(default_factory=monotonic)
    durations_ms: dict[str, int] = field(default_factory=dict)

    def transition(self, target: TurnState, *, now: float | None = None) -> None:
        if self.state in TERMINAL_STATES:
            raise ValueError(f"cannot leave terminal turn state {self.state}")
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"invalid turn transition {self.state}->{target}")
        current = monotonic() if now is None else now
        self.durations_ms[self.state.value] = max(0, int((current - self.entered_at) * 1000))
        self.state = target
        self.entered_at = current

    def fail(self, *, now: float | None = None) -> None:
        if self.state not in TERMINAL_STATES:
            self.transition(TurnState.FAILED, now=now)

    def interrupt(self, *, now: float | None = None) -> None:
        if self.state not in TERMINAL_STATES:
            self.transition(TurnState.INTERRUPTED, now=now)
