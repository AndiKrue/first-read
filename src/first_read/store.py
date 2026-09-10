"""ClickHouse write path for durable production metadata."""

import threading

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

CHARACTERS_DDL = """
CREATE TABLE IF NOT EXISTS characters (
  name               String,
  first_seen         DateTime DEFAULT now(),
  visual_description String,
  wardrobe           String DEFAULT '',
  voice_name         LowCardinality(String),
  script_title       String,
  sheet_gcs_uri      String DEFAULT ''
) ENGINE = ReplacingMergeTree ORDER BY name
"""

CHARACTERS_SHEET_ALTER = """
ALTER TABLE characters
ADD COLUMN IF NOT EXISTS sheet_gcs_uri String DEFAULT ''
"""

CHARACTERS_WARDROBE_ALTER = """
ALTER TABLE characters
ADD COLUMN IF NOT EXISTS wardrobe String DEFAULT ''
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
        client.command(CHARACTERS_DDL)
        client.command(CHARACTERS_SHEET_ALTER)
        client.command(CHARACTERS_WARDROBE_ALTER)
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
    client = get_client()
    existing = client.query(
        "SELECT sheet_gcs_uri FROM characters FINAL WHERE name = {name:String} LIMIT 1",
        parameters={"name": character.name},
    ).result_rows
    sheet_gcs_uri = existing[0][0] if existing else ""
    client.insert(
        "characters",
        [
            [
                character.name,
                character.visual_description,
                character.wardrobe,
                character.voice_name,
                script_title,
                sheet_gcs_uri,
            ]
        ],
        column_names=[
            "name",
            "visual_description",
            "wardrobe",
            "voice_name",
            "script_title",
            "sheet_gcs_uri",
        ],
    )


def upsert_character_sheet(name: str, uri: str) -> None:
    """Persist a sheet URI without replacing the character's fixed attributes."""
    get_client().command(
        """
        INSERT INTO characters (
          name, first_seen, visual_description, wardrobe, voice_name, script_title,
          sheet_gcs_uri
        )
        SELECT
          name, first_seen, visual_description, wardrobe, voice_name, script_title,
          {uri:String}
        FROM characters FINAL
        WHERE name = {name:String}
        """,
        parameters={"name": name, "uri": uri},
    )
