"""Persistent procedural experience from public agent trajectories."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3


class ExperienceStore:
    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY, namespace TEXT, case_id TEXT, workflow TEXT,
                task TEXT, procedure TEXT, lessons TEXT, source_run_id TEXT, created_at TEXT,
                UNIQUE(namespace, source_run_id))""")

    def save(self, namespace, case_id, workflow, task, procedure, lessons, source_run_id):
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                "INSERT INTO experiences(namespace,case_id,workflow,task,procedure,lessons,source_run_id,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (namespace, case_id, workflow, task, json.dumps(procedure, ensure_ascii=False),
                 json.dumps(lessons, ensure_ascii=False), source_run_id,
                 datetime.now(timezone.utc).isoformat()))
            return {"experience_id": cursor.lastrowid, "source_run_id": source_run_id}

    def search(self, namespace, task, case_id, workflow, limit=3):
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM experiences WHERE namespace=? ORDER BY id DESC", (namespace,)).fetchall()
        query = set(re.findall(r"\w+", task.lower()))
        ranked = []
        for row in rows:
            experience = dict(row)
            words = set(re.findall(r"\w+", experience["task"].lower()))
            score = len(query & words) / max(1, len(query | words))
            score += 2 * (experience["case_id"] == case_id) + (experience["workflow"] == workflow)
            if score:
                del experience["task"]
                experience["procedure"] = json.loads(experience["procedure"])
                experience["lessons"] = json.loads(experience["lessons"])
                ranked.append((score, experience))
        ranked.sort(key=lambda item: (item[0], item[1]["id"]), reverse=True)
        return {"experiences": [experience for _, experience in ranked[:limit]]}
