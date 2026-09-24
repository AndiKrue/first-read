"""ClickHouse write path for durable production metadata."""

import threading
import uuid
from datetime import UTC, datetime

import clickhouse_connect

from first_read.config import Settings
from first_read.schema import Asset, Character, RunRecord

ASSETS_DDL = """
CREATE TABLE IF NOT EXISTS assets (
  run_id           String,
  created_at       DateTime DEFAULT now(),
  asset_type       LowCardinality(String),
  script_title     String,
  scene_slug       String,
  beat_id          String,
  shot_type        LowCardinality(String),
  int_ext          LowCardinality(String),
  time_of_day      LowCardinality(String),
  location         String,
  characters       Array(String),
  tone             String,
  prompt           String,
  model_id         LowCardinality(String),
  gcs_uri          String,
  duration_seconds Float32
) ENGINE = MergeTree ORDER BY (scene_slug, asset_type, created_at)
"""

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id           String,
  created_at       DateTime DEFAULT now(),
  updated_at       DateTime DEFAULT now(),
  script_title     String,
  scene_slug       String,
  int_ext          LowCardinality(String),
  time_of_day      LowCardinality(String),
  tone             String,
  characters       Array(String),
  stage            LowCardinality(String),
  error            String DEFAULT '',
  breakdown_json   String DEFAULT '',
  panel_uris       Array(String),
  audio_uri        String DEFAULT '',
  animatic_uri     String DEFAULT '',
  duration_seconds Float32 DEFAULT 0
) ENGINE = ReplacingMergeTree(updated_at) ORDER BY run_id
"""

ASSET_COLUMNS = (
    "run_id",
    "asset_type",
    "script_title",
    "scene_slug",
    "beat_id",
    "shot_type",
    "int_ext",
    "time_of_day",
    "location",
    "characters",
    "tone",
    "prompt",
    "model_id",
    "gcs_uri",
    "duration_seconds",
)

RUN_COLUMNS = (
    "run_id",
    "created_at",
    "updated_at",
    "script_title",
    "scene_slug",
    "int_ext",
    "time_of_day",
    "tone",
    "characters",
    "stage",
    "error",
    "breakdown_json",
    "panel_uris",
    "audio_uri",
    "animatic_uri",
    "duration_seconds",
)


_LOCAL = threading.local()


def get_client():
    """One client per thread: clickhouse-connect sessions are not concurrent-safe."""
    existing = getattr(_LOCAL, "client", None)
    if existing is not None:
        return existing
    _LOCAL.client = _build_client()
    return _LOCAL.client


def _build_client():
    settings = Settings.load()
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        secure=settings.clickhouse_secure,
    )


_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def initialize_schema() -> None:
    """DDL once per process: each ALTER bumps ClickHouse metadata version,
    and a replica that has not caught up rejects the next one with code 517."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        client = get_client()
        client.command(ASSETS_DDL)
        client.command(RUNS_DDL)
        _SCHEMA_READY = True


def insert_asset(asset: Asset) -> None:
    """Insert immediately so successfully produced assets survive partial runs."""
    client = get_client()
    client.insert(
        "assets",
        [[getattr(asset, column) for column in ASSET_COLUMNS]],
        column_names=list(ASSET_COLUMNS),
    )


def upsert_run(record: RunRecord) -> None:
    """Append a new run version for ReplacingMergeTree to collapse on reads."""
    get_client().insert(
        "runs",
        [[getattr(record, column) for column in RUN_COLUMNS]],
        column_names=list(RUN_COLUMNS),
    )


def upsert_character(character: Character, script_title: str) -> None:
    del script_title  # Retained for the public pipeline's existing call contract.
    client = get_client()
    existing = _current_character(client, character.name)
    if existing:
        (
            character_id,
            version,
            sheet_gcs_uri,
            locked_fields,
            origin_id,
            visitor_id,
            owner_id,
            _,
        ) = existing
    else:
        character_id = str(uuid.uuid4())
        version = 0
        sheet_gcs_uri = ""
        locked_fields = []
        origin_id = ""
        visitor_id = ""
        owner_id = ""
    client.insert(
        "characters",
        [
            [
                character_id,
                version + 1,
                character.name,
                character.visual_description,
                character.wardrobe,
                character.voice_name,
                sheet_gcs_uri,
                locked_fields,
                origin_id,
                visitor_id,
                owner_id,
                datetime.now(UTC),
            ]
        ],
        column_names=[
            "character_id",
            "version",
            "name",
            "visual_description",
            "wardrobe",
            "voice_name",
            "sheet_gcs_uri",
            "locked_fields",
            "origin_id",
            "visitor_id",
            "owner_id",
            "created_at",
        ],
    )


def upsert_character_sheet(name: str, uri: str) -> None:
    """Persist a sheet URI without replacing the character's fixed attributes."""
    client = get_client()
    existing = _current_character(client, name)
    if not existing:
        raise LookupError(f"Character not found: {name}")
    (
        character_id,
        version,
        _,
        locked_fields,
        origin_id,
        visitor_id,
        owner_id,
        _,
    ) = existing
    current = client.query(
        "SELECT name, visual_description, wardrobe, voice_name "
        "FROM characters "
        "WHERE character_id = {character_id:String} AND version = {version:UInt32} "
        "LIMIT 1",
        parameters={"character_id": character_id, "version": version},
    ).result_rows[0]
    client.insert(
        "characters",
        [
            [
                character_id,
                version + 1,
                *current,
                uri,
                locked_fields,
                origin_id,
                visitor_id,
                owner_id,
                datetime.now(UTC),
            ]
        ],
        column_names=[
            "character_id",
            "version",
            "name",
            "visual_description",
            "wardrobe",
            "voice_name",
            "sheet_gcs_uri",
            "locked_fields",
            "origin_id",
            "visitor_id",
            "owner_id",
            "created_at",
        ],
    )


def _current_character(client, name: str):
    """Return the current row for the newest identity carrying ``name``."""
    rows = client.query(
        "SELECT character_id, latest_version AS version, sheet_gcs_uri, locked_fields, origin_id, "
        "visitor_id, owner_id, created_at FROM ("
        "SELECT character_id, max(version) AS latest_version, "
        "argMax(name, version) AS name, argMax(sheet_gcs_uri, version) AS sheet_gcs_uri, "
        "argMax(locked_fields, version) AS locked_fields, "
        "argMax(origin_id, version) AS origin_id, "
        "argMax(visitor_id, version) AS visitor_id, "
        "argMax(owner_id, version) AS owner_id, "
        "argMax(created_at, version) AS created_at "
        "FROM characters GROUP BY character_id"
        ") WHERE name = {name:String} ORDER BY created_at DESC, character_id DESC LIMIT 1",
        parameters={"name": name},
    ).result_rows
    return rows[0] if rows else None
