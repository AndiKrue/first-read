"""Units behind the resume route: run-state read/write, panel placement, storyboard."""

import asyncio
import json

import pytest

from first_read import api, memory, store
from first_read.gcs import UploadedObject
from first_read.schema import Breakdown, RunRecord
from first_read.tools import storyboard as storyboard_module

RUN_ID = "6152afee-460f-4ad4-814a-d22a29394262"
BUCKET = "gs://first-read-media"


def breakdown(count: int = 6) -> Breakdown:
    return Breakdown(
        beats=[
            {
                "beat_id": f"b{index}",
                "description": f"Beat {index}",
                "shot_type": "WIDE",
                "characters_present": ["JUNE"],
            }
            for index in range(1, count + 1)
        ],
        characters=[{"name": "JUNE", "visual_description": "40s"}],
        location_description="A cafe.",
        staging="June sits by the window.",
        tone="quiet dread",
    )


def named(position: int, run_id: str = RUN_ID) -> str:
    return f"{BUCKET}/runs/{run_id}/panel_{position:02d}.png"


# --- store.upsert_run ----------------------------------------------------------


class FakeClickHouse:
    def __init__(self, columns: list[str]) -> None:
        self.columns = columns
        self.queries: list[str] = []
        self.inserts: list[tuple[str, list, list[str]]] = []

    def query(self, sql: str, parameters=None):
        self.queries.append(sql)
        return type("Result", (), {"result_rows": [(name,) for name in self.columns]})()

    def insert(self, table: str, rows: list, column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))


@pytest.fixture
def clickhouse(monkeypatch):
    def install(columns: list[str]) -> FakeClickHouse:
        fake = FakeClickHouse(columns)
        monkeypatch.setattr(store, "get_client", lambda: fake)
        store.run_table_columns.cache_clear()
        return fake

    yield install
    store.run_table_columns.cache_clear()


def test_a_new_run_writes_exactly_the_columns_it_always_did(clickhouse):
    fake = clickhouse([*store.RUN_COLUMNS, "panel_beat_ids"])
    record = RunRecord(run_id="new", script_title="A title")

    store.upsert_run(record)

    table, rows, columns = fake.inserts[0]
    assert table == "runs"
    assert columns == list(store.RUN_COLUMNS)
    assert rows == [[getattr(record, column) for column in store.RUN_COLUMNS]]
    assert fake.queries == []  # the table is not even inspected


def test_restored_beat_ids_are_written_where_the_shared_table_has_the_column(
    clickhouse,
):
    fake = clickhouse([*store.RUN_COLUMNS, "panel_beat_ids"])
    record = RunRecord(run_id=RUN_ID, panel_uris=[named(1)], panel_beat_ids=["b1"])

    store.upsert_run(record)
    store.upsert_run(record)

    _, rows, columns = fake.inserts[0]
    assert columns == [*store.RUN_COLUMNS, "panel_beat_ids"]
    assert rows[0][-1] == ["b1"]
    assert len(fake.queries) == 1  # read once per process


def test_restored_beat_ids_are_not_written_where_the_column_does_not_exist(
    clickhouse,
):
    fake = clickhouse(list(store.RUN_COLUMNS))
    record = RunRecord(run_id=RUN_ID, panel_uris=[named(1)], panel_beat_ids=["b1"])

    store.upsert_run(record)

    assert fake.inserts[0][2] == list(store.RUN_COLUMNS)


def test_the_run_ddl_is_unchanged():
    assert "panel_beat_ids" not in store.RUNS_DDL
    assert "panel_beat_ids" not in store.RUN_COLUMNS


# --- memory.get_run_for_resume -------------------------------------------------


def _row(**overrides) -> dict:
    row = {
        "run_id": RUN_ID,
        "created_at": "2026-09-20 18:30:00",
        "updated_at": "2026-09-20 18:31:00",
        "script_title": "The Last Departure",
        "scene_slug": "INT. TRANSPORT CAFE - NIGHT",
        "int_ext": "INT",
        "time_of_day": "NIGHT",
        "tone": "quiet dread",
        "characters": ["JUNE"],
        "stage": "failed",
        "error": "boom",
        "breakdown_json": "",
        "panel_uris": [named(1)],
        "audio_uri": "",
        "animatic_uri": "",
        "duration_seconds": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("has_column", [True, False])
def test_resume_read_selects_beat_ids_only_where_they_exist(monkeypatch, has_column):
    queries: list[str] = []

    async def run_query(sql: str) -> list[dict]:
        queries.append(sql)
        if "system.columns" in sql:
            return [{"name": "panel_beat_ids"}] if has_column else []
        extra = {"panel_beat_ids": [1]} if has_column else {}
        return [_row(**extra)]

    monkeypatch.setattr(memory, "_run_query", run_query)

    record = asyncio.run(memory.get_run_for_resume(RUN_ID))

    assert record.run_id == RUN_ID and record.stage == "failed"
    select = queries[1]
    assert (", panel_beat_ids FROM runs" in select) is has_column
    assert f"WHERE run_id = '{RUN_ID}'" in select
    assert "ORDER BY updated_at DESC" in select and "FINAL" not in select
    assert record.panel_beat_ids == (["1"] if has_column else [])


def test_resume_read_quotes_the_run_id(monkeypatch):
    queries: list[str] = []

    async def run_query(sql: str) -> list[dict]:
        queries.append(sql)
        return []

    monkeypatch.setattr(memory, "_run_query", run_query)

    assert asyncio.run(memory.get_run_for_resume("x' OR '1'='1")) is None
    assert "run_id = 'x\\' OR \\'1\\'=\\'1'" in queries[1]


# --- Panel placement -----------------------------------------------------------


def test_named_panels_sit_at_their_own_beats_whatever_the_stored_order():
    record = RunRecord(run_id=RUN_ID, panel_uris=[named(3), named(1), named(2)])

    assert api._kept_panels(record, breakdown()) == {
        1: named(1),
        2: named(2),
        3: named(3),
    }


def test_aligned_beat_ids_place_panels_the_storyboard_did_not_name():
    uris = [f"{BUCKET}/private/a.png", "", f"{BUCKET}/private/c.png"]
    record = RunRecord(
        run_id=RUN_ID, panel_uris=uris, panel_beat_ids=["b2", "b4", "b5"]
    )

    assert api._kept_panels(record, breakdown()) == {2: uris[0], 5: uris[2]}


def test_unplaced_panels_fill_the_earliest_free_beats_in_stored_order():
    uris = [f"{BUCKET}/private/first.png", f"{BUCKET}/private/second.png"]
    record = RunRecord(run_id=RUN_ID, panel_uris=[named(1), *uris])

    assert api._kept_panels(record, breakdown()) == {
        1: named(1),
        2: uris[0],
        3: uris[1],
    }


def test_empty_malformed_and_repeated_uris_are_not_panels():
    record = RunRecord(
        run_id=RUN_ID,
        panel_uris=["", "https://example.com/x.png", "gs://", named(2), named(2)],
    )

    assert api._kept_panels(record, breakdown()) == {2: named(2)}


def test_a_missing_beat_never_names_an_object_a_kept_panel_uses():
    # A private panel aligned to beat 1 and a storyboard panel named for beat 1.
    record = RunRecord(
        run_id=RUN_ID,
        panel_uris=[f"{BUCKET}/private/a.png", named(1)],
        panel_beat_ids=["b1", "b1"],
    )

    kept = api._kept_panels(record, breakdown())
    missing = [position for position in range(1, 7) if position not in kept]

    assert kept[1] == named(1)
    kept_objects = set(kept.values())
    assert all(named(position) not in kept_objects for position in missing)


def test_another_runs_panel_names_do_not_place_panels():
    record = RunRecord(run_id=RUN_ID, panel_uris=[named(4, run_id="other-run")])

    # Not this run's name, so it is placed by stored order instead.
    assert api._kept_panels(record, breakdown()) == {1: named(4, run_id="other-run")}


def test_an_unreadable_stored_breakdown_is_a_conflict():
    record = RunRecord(
        run_id=RUN_ID, stage="failed", breakdown_json=json.dumps({"beats": []})
    )

    with pytest.raises(api.HTTPException) as refused:
        api._plan_resume(record, "INT. CAFE - NIGHT")

    assert refused.value.status_code == 409


# --- generate_storyboard(positions=...) ---------------------------------------


@pytest.fixture
def storyboard(monkeypatch, tmp_path):
    uploads: list[str] = []
    monkeypatch.setattr(storyboard_module, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(
        storyboard_module,
        "ensure_character_sheet",
        lambda *_: f"{BUCKET}/sheets/june.png",
    )
    monkeypatch.setattr(storyboard_module, "_generate_image", lambda *_: b"png")
    monkeypatch.setattr(storyboard_module, "insert_asset", lambda asset: None)

    def upload(data, object_name, content_type):
        uploads.append(object_name)
        return UploadedObject(gcs_uri=f"{BUCKET}/{object_name}", signed_url="https://x")

    monkeypatch.setattr(storyboard_module, "upload_bytes", upload)
    return uploads


def _scene():
    from first_read.parse import parse_fountain

    return parse_fountain("INT. CAFE - NIGHT\n\nJUNE\nHello.\n")


def test_storyboard_renders_only_the_selected_beats_under_their_own_index(storyboard):
    progress = []

    outputs = storyboard_module.generate_storyboard(
        _scene(),
        breakdown(),
        RUN_ID,
        "Title",
        lambda done, total, panel: progress.append((done, total, panel.beat_id)),
        positions=[5, 2],
    )

    assert storyboard == [f"runs/{RUN_ID}/panel_02.png", f"runs/{RUN_ID}/panel_05.png"]
    assert [panel.beat_id for panel in outputs] == ["b2", "b5"]
    assert progress == [(1, 2, "b2"), (2, 2, "b5")]


def test_storyboard_without_a_selection_renders_every_beat_as_before(storyboard):
    progress = []

    outputs = storyboard_module.generate_storyboard(
        _scene(),
        breakdown(4),
        RUN_ID,
        "Title",
        lambda done, total, panel: progress.append((done, total)),
    )

    assert storyboard == [
        f"runs/{RUN_ID}/panel_{index:02d}.png" for index in range(1, 5)
    ]
    assert [panel.beat_id for panel in outputs] == ["b1", "b2", "b3", "b4"]
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)]
