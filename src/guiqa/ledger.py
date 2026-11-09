class ProgressLedger:
    """Records executed GUI actions without reading page state or oracle evidence."""

    def __init__(self):
        self.selection_active = False
        self.inputs = []
        self.last_reload_step = None
        self.previous_click = None
        self.repeated_clicks = 0
        self.last_action = None

    def record(self, action, step):
        self.last_action = action
        kind = action["action"]
        if kind == "click":
            point = action["coordinate"]
            self.repeated_clicks = self.repeated_clicks + 1 if point == self.previous_click else 1
            self.previous_click = point
            self.selection_active = False
        else:
            self.previous_click = None
            self.repeated_clicks = 0
        if kind == "press" and action["key"] == "Control+a":
            self.selection_active = True
        elif kind == "press" and action["key"] == "Control+r":
            self.last_reload_step = step
            self.selection_active = False
        elif kind == "type":
            self.inputs.append({"step": step, "text": action["text"], "preceded_by_select_all": self.selection_active})
            self.selection_active = False

    def snapshot(self):
        if self.selection_active:
            next_step = "Select All has already executed. Now use type with the exact required replacement text. Clicking again would cancel the selection."
        elif self.last_action and self.last_action["action"] == "click":
            if not self.inputs or not self.inputs[-1]["preceded_by_select_all"]:
                next_step = "The click has already executed. If you clicked the target text field, now press Control+a to select its existing value; do not click it again."
            else:
                next_step = "The click has executed. If it was Save, now reload/reopen and inspect. If it was another required input, press Control+a before replacing its value."
        elif self.last_action and self.last_action["action"] == "type":
            next_step = "Typing has executed. Inspect the screenshot for the exact requested value. Complete any remaining fields, then Save; correct unexpected text with Select All and replacement."
        elif self.last_action and self.last_action.get("key") == "Control+r":
            next_step = "The page has reloaded. Inspect the same record's visible saved value and report pass/fail if the business check is complete."
        else:
            next_step = "Inspect the screenshot, locate the requested field, and begin the next pending business operation."
        notes = [next_step]
        if self.inputs and not self.inputs[-1]["preceded_by_select_all"]:
            notes.append("Your last type action did not follow Select All. Inspect the visible field: existing text may have been appended or truncated. Correct it before saving.")
        if self.repeated_clicks >= 2:
            notes.append("You repeated the same click. Inspect its visible outcome. Correct unexpected input; after saving, reopen instead of repeatedly saving.")
        notes.append("After all requested values are entered correctly, save once, reload/reopen the same record, and inspect the new screenshot before finishing.")
        return {"recent_inputs": self.inputs[-6:], "selection_active": self.selection_active,
                "last_executed_action": self.last_action,
                "last_reload_step": self.last_reload_step, "consecutive_identical_clicks": self.repeated_clicks,
                "pending_checks": notes}
