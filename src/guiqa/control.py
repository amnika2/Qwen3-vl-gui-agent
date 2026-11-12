import json

from PIL import Image, ImageChops, ImageStat

from .actions import browser_action


REVIEW_EVIDENCE_FIELDS = ("requested_value_entered", "submission_succeeded", "final_record_visible")

# Calibrated on recorded traces: a failed (unfocused) type changes nothing (0.0),
# a successful type into a small field measures ~0.10. Clicks are excluded:
# focusing an input field does not change pixels, so a no-change click is not
# diagnostic and such feedback misled the model into re-clicking.
VISIBLE_CHANGE = {"type": 0.03}


def review_guard(action, profile="persistence"):
    if action["verdict"] == "blocked":
        return None
    if profile == "business_outcome_v1":
        required = ("requested_value_entered", "submission_observed")
        missing = [field for field in required if action.get(field) is not True]
        if missing:
            return "review_evidence_not_established:" + ",".join(missing)
        if action.get("business_rejection_visible") is True:
            correct_verdict = "pass" if action.get("expected_rejection") is True else "fail"
            return None if action["verdict"] == correct_verdict else "review_rejection_verdict_contradicts_task"
    missing = [field for field in REVIEW_EVIDENCE_FIELDS if action.get(field) is not True]
    return "review_evidence_not_established:" + ",".join(missing) if missing else None


class LoopControl:
    def __init__(self, screenshot):
        self.previous = Image.open(screenshot).convert("L").resize((64, 45))
        self.actions = []
        self.static_clicks = 0
        self.last_type_step = 0
        self.post_type_step = 0
        self.difference = None
        self.differences = {}

    def record(self, action, step, screenshot):
        frame = Image.open(screenshot).convert("L").resize((64, 45))
        difference = ImageStat.Stat(ImageChops.difference(self.previous, frame)).mean[0]
        self.previous = frame
        self.difference = difference
        self.differences[step] = difference
        self.static_clicks = self.static_clicks + 1 if action["action"] == "click" and difference < 0.5 else 0
        if action["action"] == "type":
            self.last_type_step = step
            self.post_type_step = 0
        elif self.last_type_step and not self.post_type_step:
            self.post_type_step = step
        self.actions.append(json.dumps(browser_action(action), sort_keys=True))
        for period in (1, 2, 3):
            if len(self.actions) >= 3 * period:
                a, b, c = (self.actions[-3 * period:-2 * period], self.actions[-2 * period:-period], self.actions[-period:])
                if a == b == c:
                    return f"action_cycle_period_{period}"
        if self.static_clicks >= 3:
            return "three_clicks_without_visual_change"
        return None
