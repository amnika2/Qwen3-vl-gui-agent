"""One tool registry for browser actions and discovered MCP tools."""

from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import sys

import anyio
from anyio.from_thread import start_blocking_portal
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from .browser import execute
from .actions import KEYS


BROWSER_ARGUMENTS = {
    "click": {"coordinate": {"type": "array", "minItems": 2, "maxItems": 2,
                              "items": {"type": "number", "minimum": 0, "maximum": 1000}}},
    "type": {"text": {"type": "string", "maxLength": 2000}},
    "press": {"key": {"enum": sorted(KEYS)}},
    "scroll": {"pixels": {"type": "integer", "minimum": -1800, "maximum": 1800}},
    "wait": {"seconds": {"type": "number", "minimum": 0, "maximum": 5}},
}


class MCPConnections:
    def __init__(self, servers):
        self.servers = servers
        self.clients = {}
        self.catalog = {}

    def __enter__(self):
        self.manager = start_blocking_portal()
        self.portal = self.manager.__enter__()
        try:
            self.future, _ = self.portal.start_task(self.serve)
        except BaseException:
            self.manager.__exit__(*sys.exc_info())
            raise
        return self

    async def serve(self, *, task_status):
        async with AsyncExitStack() as stack:
            for server in self.servers:
                target = server["url"] if "url" in server else StdioServerParameters(
                    command=server["command"], args=server.get("args", []),
                    env={**os.environ, **server.get("env", {})})
                client = await stack.enter_async_context(Client(target))
                listing = await client.list_tools()
                for tool in listing.tools:
                    name = server["name"] + "__" + tool.name
                    self.clients[name] = (client, tool.name)
                    self.catalog[name] = {"name": name, "description": tool.description,
                                          "input_schema": tool.input_schema, "transport": "mcp"}
            self.stop = anyio.Event()
            task_status.started()
            await self.stop.wait()

    def call(self, name, arguments):
        return self.portal.call(self.call_async, name, arguments)

    async def call_async(self, name, arguments):
        client, remote_name = self.clients[name]
        result = await client.call_tool(remote_name, arguments)
        return {"is_error": result.is_error, "data": result.structured_content,
                "text": "\n".join(block.text for block in result.content if block.type == "text")}

    def __exit__(self, *exc):
        self.portal.call(self.stop.set)
        try:
            self.future.result()
        finally:
            self.manager.__exit__(*exc)


class ToolRegistry:
    def __init__(self, page, directory, connections):
        self.page, self.directory, self.connections = page, Path(directory), connections
        self.catalog = {
            "browser__" + action: {"name": "browser__" + action, "description": "Browser " + action,
                "action": action, "transport": "browser", "input_schema": {
                    "type": "object", "properties": properties, "required": list(properties),
                    "additionalProperties": False}}
            for action, properties in BROWSER_ARGUMENTS.items()}
        self.catalog.update(connections.catalog)

    def execute(self, action, caller="agent"):
        result = execute(self.page, action)
        self.record("browser__" + action["action"], action, result, caller)
        return result

    def call(self, name, arguments, caller="agent"):
        if self.catalog[name]["transport"] == "browser":
            return self.execute({"action": self.catalog[name]["action"], **arguments}, caller)
        result = self.connections.call(name, arguments)
        self.record(name, arguments, result, caller)
        return result

    def record(self, name, arguments, result, caller):
        with (self.directory / "tool-calls.jsonl").open("a") as handle:
            handle.write(json.dumps({"name": name, "arguments": arguments, "result": result,
                                     "caller": caller}, ensure_ascii=False) + "\n")
