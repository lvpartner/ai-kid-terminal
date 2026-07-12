import pytest

from kid_terminal.services.turn_state import TurnState, TurnStateMachine


def test_turn_state_machine_records_ordered_stage_durations() -> None:
    turn = TurnStateMachine(entered_at=10.0)
    turn.transition(TurnState.TRANSCRIBING, now=10.2)
    turn.transition(TurnState.RESEARCHING, now=10.8)
    turn.transition(TurnState.VALIDATING, now=11.0)
    turn.transition(TurnState.SYNTHESIZING, now=11.1)
    turn.transition(TurnState.PLAYING, now=11.4)
    turn.transition(TurnState.COMPLETED, now=12.0)
    assert turn.state == TurnState.COMPLETED
    assert turn.durations_ms["transcribing"] == 600
    assert turn.durations_ms["playing"] >= 599


def test_turn_state_machine_rejects_invalid_or_terminal_transitions() -> None:
    turn = TurnStateMachine(entered_at=1.0)
    with pytest.raises(ValueError, match="invalid turn transition"):
        turn.transition(TurnState.PLAYING, now=1.1)
    turn.interrupt(now=1.2)
    with pytest.raises(ValueError, match="cannot leave terminal"):
        turn.transition(TurnState.FAILED, now=1.3)
