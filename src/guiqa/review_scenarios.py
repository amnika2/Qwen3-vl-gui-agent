"""Identity of a review task, independent of its execution and dataset partition."""

import hashlib
import json


def content_key(case_id, workflow, task, initial, requested_changes):
    content = {"case_id": case_id, "workflow": workflow, "task": task,
               "initial": initial, "requested_changes": requested_changes}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def scenario_metadata(case_id, workflow, task, initial, requested_changes, parent_scenario_id):
    key = content_key(case_id, workflow, task, initial, requested_changes)
    return {"scenario_fingerprint": key, "content_key": key, "parent_scenario_id": parent_scenario_id}
