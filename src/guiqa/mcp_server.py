"""Serve the agent's procedural memory over MCP stdio."""

import argparse
from typing import Annotated

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from .agent_memory import ExperienceStore


class SearchResult(BaseModel):
    experiences: list[dict]


class SavedExperience(BaseModel):
    experience_id: int
    source_run_id: str


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--namespace", required=True)
    args = parser.parse_args()
    create_server(args.database, args.namespace).run()


def create_server(database, namespace):
    store = ExperienceStore(database)
    server = MCPServer("guiqa-memory", instructions="Recall procedures, then verify the current task afresh.")

    @server.tool()
    def search_experiences(task: str, case_id: str, workflow: str,
                           limit: Annotated[int, Field(ge=1, le=10)] = 3) -> SearchResult:
        """Find previous GUI procedures and lessons. Prior values and verdicts are not current evidence."""
        return SearchResult(**store.search(namespace, task, case_id, workflow, limit))

    @server.tool()
    def save_experience(case_id: str, workflow: str, task: str, procedure: list[str],
                        lessons: list[str], source_run_id: str) -> SavedExperience:
        """Save an episode's procedural lessons after final review; omit product verdicts and private evidence."""
        return SavedExperience(**store.save(namespace, case_id, workflow, task, procedure, lessons, source_run_id))

    return server


if __name__ == "__main__":
    main()
