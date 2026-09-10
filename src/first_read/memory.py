"""Read production memory through mcp-clickhouse as an ADK MCP toolset.

Writes intentionally do not pass through this module. The MCP server stays
read-only; :mod:`first_read.store` owns ClickHouse DDL and inserts.
"""

import json
import os
from functools import lru_cache
from typing import Any

from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

from first_read.schema import Asset, RunRecord


@lru_cache(maxsize=1)
def get_mcp_toolset():
    """Build the shared official ADK toolset over the proven stdio launch."""
    server_environment = dict(os.environ)
    server_environment["CLICKHOUSE_ALLOW_WRITE_ACCESS"] = "false"
    server_environment["CLICKHOUSE_ALLOW_DROP"] = "false"
    server_environment["FASTMCP_DISABLE_BANNER"] = "1"
    server_environment["FASTMCP_NO_VERSION_CHECK"] = "1"
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="python",
                args=["-m", "mcp_clickhouse.main"],
                env=server_environment,
            ),
            timeout=60,
        ),
        tool_filter=["run_query", "list_databases", "list_tables"],
        tool_name_prefix="clickhouse_",
    )


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _text_from_result(result: Any) -> str:
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise RuntimeError(f"ClickHouse MCP query failed: {result}")
    chunks = [
        block.text
        for block in getattr(result, "content", [])
        if getattr(block, "text", None)
    ]
    if not chunks:
        raise RuntimeError("ClickHouse MCP run_query returned no text")
    return "\n".join(chunks)


def _parse_query_payload(payload: str | dict[str, Any]) -> list[dict[str, Any]]:
    """Decode every row in the MCP text envelope, not merely its first row."""
    decoded = json.loads(payload) if isinstance(payload, str) else payload
    columns = decoded.get("columns", [])
    rows = decoded.get("rows", [])
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise TypeError("ClickHouse MCP run_query returned an invalid row set")
    parsed = []
    for row in rows:
        if isinstance(row, dict):
            parsed.append(row)
        else:
            parsed.append(dict(zip(columns, row)))
    return parsed


def _parse_query_result(result: Any) -> list[dict[str, Any]]:
    """Accept one row-set block or aggregate row-set blocks from MCP."""
    text = _text_from_result(result)
    try:
        return _parse_query_payload(text)
    except json.JSONDecodeError:
        rows = []
        for block in getattr(result, "content", []):
            if getattr(block, "text", None):
                rows.extend(_parse_query_payload(block.text))
        return rows


async def _run_query(query: str) -> list[dict[str, Any]]:
    toolset = get_mcp_toolset()
    # The toolset owns connection pooling/lifecycle. Calling through its session
    # executor keeps all reads on the same MCP transport exposed to the agent.
    result = await toolset._execute_with_session(
        lambda session: session.call_tool("run_query", {"query": query}),
        "ClickHouse MCP run_query failed",
    )
    return _parse_query_result(result)


async def lookup_characters(names: list[str]) -> dict[str, dict[str, str]]:
    if not names:
        return {}
    name_list = ", ".join(_quote(name) for name in names)
    rows = await _run_query(
        "SELECT name, visual_description, wardrobe, voice_name, sheet_gcs_uri "
        "FROM characters FINAL "
        f"WHERE name IN ({name_list})"
    )
    return {
        row["name"]: {
            "visual_description": row["visual_description"],
            "wardrobe": row["wardrobe"],
            "voice_name": row["voice_name"],
            "sheet_gcs_uri": row["sheet_gcs_uri"],
        }
        for row in rows
    }


def _build_asset_search_query(
    character: str | None = None,
    int_ext: str | None = None,
    time_of_day: str | None = None,
    tone: str | None = None,
) -> str:
    clauses = []
    if character:
        clauses.append(f"has(characters, {_quote(character.upper())})")
    if int_ext:
        clauses.append(f"upper(int_ext) = {_quote(int_ext.upper())}")
    if time_of_day:
        clauses.append(f"upper(time_of_day) = {_quote(time_of_day.upper())}")
    if tone:
        clauses.append(f"positionCaseInsensitive(tone, {_quote(tone)}) > 0")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return (
        "SELECT run_id, created_at, asset_type, script_title, scene_slug, beat_id, "
        "shot_type, int_ext, time_of_day, location, characters, tone, prompt, "
        "model_id, gcs_uri, duration_seconds FROM assets"
        f"{where} ORDER BY created_at DESC LIMIT 200"
    )


async def search_assets(
    character: str | None = None,
    int_ext: str | None = None,
    time_of_day: str | None = None,
    tone: str | None = None,
) -> list[Asset]:
    rows = await _run_query(
        _build_asset_search_query(character, int_ext, time_of_day, tone)
    )
    return [Asset.model_validate(row) for row in rows]


_RUN_SELECT = (
    "run_id, created_at, updated_at, script_title, scene_slug, int_ext, "
    "time_of_day, tone, characters, stage, error, breakdown_json, panel_uris, "
    "audio_uri, animatic_uri, duration_seconds"
)


async def list_runs(limit: int = 24) -> list[RunRecord]:
    """Return the newest durable version of each recent run."""
    safe_limit = max(1, min(int(limit), 200))
    rows = await _run_query(
        f"SELECT {_RUN_SELECT} FROM runs FINAL "
        f"ORDER BY created_at DESC LIMIT {safe_limit}"
    )
    return [RunRecord.model_validate(row) for row in rows]


async def get_run(run_id: str) -> RunRecord | None:
    rows = await _run_query(
        f"SELECT {_RUN_SELECT} FROM runs FINAL WHERE run_id = {_quote(run_id)} LIMIT 1"
    )
    return RunRecord.model_validate(rows[0]) if rows else None
