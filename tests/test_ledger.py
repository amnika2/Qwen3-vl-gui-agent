from guiqa.ledger import ProgressLedger


def test_unselected_typing_is_retained_after_repeated_save_clicks():
    ledger = ProgressLedger()
    ledger.record({"action": "click", "coordinate": [500, 445]}, 1)
    ledger.record({"action": "type", "text": "Saved Pen Name"}, 2)
    for step in range(3, 15):
        ledger.record({"action": "click", "coordinate": [301, 799]}, step)
    state = ledger.snapshot()
    assert not state["recent_inputs"][0]["preceded_by_select_all"]
    assert state["consecutive_identical_clicks"] == 12
    assert any("appended" in message for message in state["pending_checks"])
    assert "oracle_verdict" not in state


def test_replace_and_reload_record_actions_without_claiming_persistence():
    ledger = ProgressLedger()
    ledger.record({"action": "click", "coordinate": [500, 445]}, 1)
    ledger.record({"action": "press", "key": "Control+a"}, 2)
    ledger.record({"action": "type", "text": "Saved Pen Name"}, 3)
    ledger.record({"action": "press", "key": "Control+r"}, 4)
    state = ledger.snapshot()
    assert state["recent_inputs"][0]["preceded_by_select_all"]
    assert state["last_reload_step"] == 4
    assert "persisted" not in state
