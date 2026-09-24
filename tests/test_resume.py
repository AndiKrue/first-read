"""WO-PUBLIC-01: a failed run can be resumed in place.

The real endpoint and the real ``generate_storyboard`` run here; only the edges
(model calls, GCS, ClickHouse) are replaced, so "not regenerated" is asserted at
the upload boundary as well as on the stored URIs.
"""

from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from first_read import api
from first_read.gcs import UploadedObject
from first_read.models import PANEL_FILENAME
from first_read.schema import (
    AnimaticOutput,
    AudioOutput,
    Breakdown,
    PanelOutput,
    RunRecord,
)
from first_read.tools import storyboard as storyboard_module

TOKEN = "resume-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SCENE = (SAMPLES / "sample_scene.fountain").read_text(encoding="utf-8")
OTHER_SCENE = (SAMPLES / "second_scene.fountain").read_text(encoding="utf-8")
SLUG = "INT. TRANSPORT CAFE - NIGHT"
BUCKET = "gs://first-read-media"
TITLE = "The Last Departure"
CREATED = datetime(2026, 9, 20, 18, 30, tzinfo=UTC)
PARTIAL_ID = "6152afee-460f-4ad4-814a-d22a29394262"
NO_BEAT_IDS_ID = "38e808ef-7390-4aaa-8ad8-a4083bf38122"
EARLY_ID = "0c1d2e3f-0000-4000-8000-000000000001"


def make_breakdown(beat_ids: list[str]) -> Breakdown:
    return Breakdown(
        beats=[
            {
                "beat_id": beat_id,
                "description": f"Beat {beat_id}",
                "shot_type": "MEDIUM",
                "characters_present": ["JUNE", "MARCO"],
            }
            for beat_id in beat_ids
        ],
        characters=[
            {
                "name": "JUNE",
                "visual_description": "40s, dark hair",
                "wardrobe": "coat",
            },
            {
                "name": "MARCO",
                "visual_description": "30s, soaked",
                "wardrobe": "jacket",
            },
        ],
        location_description="A transport cafe at night.",
        staging="June and Marco sit opposite each other in a booth.",
        tone="quiet dread",
    )


def panel_uri(run_id: str, position: int) -> str:
    return f"{BUCKET}/runs/{run_id}/{PANEL_FILENAME.format(index=position)}"


class World:
    """The durable store, GCS and the models, as the pipeline sees them."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.db: dict[str, RunRecord] = {}
        self.writes: list[RunRecord] = []
        self.reads: list[str] = []
        self.uploads: list[str] = []
        self.images = 0
        self.downloads: list[str] = []
        self.breakdowns: list = []
        self.table_reads: list = []
        self.assembled: list[list[PanelOutput]] = []
        self.recorded_characters: list[str] = []
        self.remembered = {
            "JUNE": {"visual_description": "40s", "wardrobe": "", "voice_name": "Kore"},
            "MARCO": {
                "visual_description": "30s",
                "wardrobe": "",
                "voice_name": "Puck",
            },
        }
        self.table_read_error: Exception | None = None

        monkeypatch.setenv("FIRST_READ_ADMIN_TOKEN", TOKEN)
        monkeypatch.setenv("MAX_RUNS_PER_HOUR", "100")
        monkeypatch.setattr(api, "_runs", {})
        monkeypatch.setattr(api, "_run_starts", deque())
        monkeypatch.setattr(api, "_resuming", set())
        monkeypatch.setattr(api, "OUTPUT_ROOT", tmp_path)
        monkeypatch.setattr(api, "initialize_schema", lambda: None)
        monkeypatch.setattr(api, "upsert_run", self.upsert_run)
        monkeypatch.setattr(api, "get_resumable_run", self.get_run)
        monkeypatch.setattr(api, "lookup_characters", self.lookup_characters)
        monkeypatch.setattr(api, "upsert_character", self.upsert_character)
        monkeypatch.setattr(api, "download_bytes", self.download_bytes)
        monkeypatch.setattr(api, "signed_url_for_uri", self.browser_url)
        monkeypatch.setattr(api, "generate_breakdown", self.generate_breakdown)
        monkeypatch.setattr(api, "generate_table_read", self.generate_table_read)
        monkeypatch.setattr(api, "assemble_animatic", self.assemble_animatic)
        # The real generate_storyboard runs; only its model and storage edges
        # are replaced.
        monkeypatch.setattr(storyboard_module, "OUTPUT_ROOT", tmp_path)
        monkeypatch.setattr(
            storyboard_module,
            "ensure_character_sheet",
            lambda name, *_: f"{BUCKET}/sheets/{name.lower()}.png",
        )
        monkeypatch.setattr(storyboard_module, "_generate_image", self.generate_image)
        monkeypatch.setattr(storyboard_module, "upload_bytes", self.upload_bytes)
        monkeypatch.setattr(storyboard_module, "insert_asset", lambda asset: None)
        self.client = TestClient(api.app)

    # ClickHouse -----------------------------------------------------------
    def upsert_run(self, record: RunRecord) -> None:
        self.writes.append(record.model_copy(deep=True))
        self.db[record.run_id] = record.model_copy(deep=True)

    async def get_run(self, run_id: str) -> RunRecord | None:
        self.reads.append(run_id)
        record = self.db.get(run_id)
        return record.model_copy(deep=True) if record else None

    async def lookup_characters(self, names: list[str]) -> dict:
        return {
            name: self.remembered[name] for name in names if name in self.remembered
        }

    def upsert_character(self, character, script_title: str) -> None:
        self.recorded_characters.append(character.name)

    # GCS ------------------------------------------------------------------
    def upload_bytes(self, data: bytes, object_name: str, content_type: str):
        self.uploads.append(object_name)
        return UploadedObject(
            gcs_uri=f"{BUCKET}/{object_name}",
            signed_url=f"https://storage.googleapis.com/first-read-media/{object_name}",
        )

    def download_bytes(self, uri: str) -> bytes:
        self.downloads.append(uri)
        return b"stored:" + uri.encode()

    @staticmethod
    def browser_url(uri: str) -> str:
        return "https://storage.googleapis.com/" + uri.removeprefix("gs://")

    # Models ---------------------------------------------------------------
    def generate_image(self, prompt: str, reference_uris=None) -> bytes:
        self.images += 1
        return b"new panel"

    async def generate_breakdown(self, scene, script_title: str) -> Breakdown:
        self.breakdowns.append(scene)
        return make_breakdown(["1", "2", "3", "4", "5"])

    def generate_table_read(self, scene, breakdown, run_id, script_title):
        self.table_reads.append(scene)
        if self.table_read_error:
            raise self.table_read_error
        local = self.tmp / run_id / "tableread.wav"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"wav")
        return AudioOutput(
            url="https://audio",
            gcs_uri=f"{BUCKET}/runs/{run_id}/tableread.wav",
            local_path=str(local),
            duration_seconds=14.5,
        )

    def assemble_animatic(self, scene, breakdown, panels, audio, run_id, title):
        for panel in panels:
            assert Path(panel.local_path).is_file(), panel
        assert Path(audio.local_path).is_file()
        self.assembled.append(list(panels))
        return AnimaticOutput(
            url="https://animatic",
            gcs_uri=f"{BUCKET}/runs/{run_id}/animatic.mp4",
            local_path=str(self.tmp / run_id / "animatic.mp4"),
        )

    # Helpers --------------------------------------------------------------
    def seed(self, record: RunRecord) -> RunRecord:
        self.db[record.run_id] = record.model_copy(deep=True)
        return record

    def resume(self, run_id: str, body=None, headers=AUTH):
        return self.client.post(
            f"/api/runs/{run_id}/resume",
            json={"fountain_text": None} if body is None else body,
            headers=headers,
        )


@pytest.fixture
def world(monkeypatch, tmp_path) -> World:
    return World(monkeypatch, tmp_path)


def partial_record() -> RunRecord:
    """6152afee: six beat ids, five panels (the fourth entry was left empty)."""
    return RunRecord(
        run_id=PARTIAL_ID,
        created_at=CREATED,
        updated_at=CREATED,
        script_title=TITLE,
        scene_slug=SLUG,
        int_ext="INT",
        time_of_day="NIGHT",
        tone="quiet dread",
        characters=["JUNE", "MARCO"],
        stage="failed",
        error="Code: 60. Table first_read.characters_v3 does not exist.",
        breakdown_json=make_breakdown(["1", "2", "3", "4", "5", "6"]).model_dump_json(),
        panel_uris=[
            panel_uri(PARTIAL_ID, 1),
            panel_uri(PARTIAL_ID, 2),
            panel_uri(PARTIAL_ID, 3),
            "",
            panel_uri(PARTIAL_ID, 5),
            panel_uri(PARTIAL_ID, 6),
        ],
        panel_beat_ids=["1", "2", "3", "4", "5", "6"],
    )


def no_beat_ids_record() -> RunRecord:
    """38e808ef: five panels, no beat ids."""
    beat_ids = ["10", "20", "30", "40", "50"]
    return RunRecord(
        run_id=NO_BEAT_IDS_ID,
        created_at=CREATED,
        script_title=TITLE,
        scene_slug=SLUG,
        int_ext="INT",
        time_of_day="NIGHT",
        characters=["JUNE", "MARCO"],
        stage="failed",
        error="ValueError: characters_present",
        breakdown_json=make_breakdown(beat_ids).model_dump_json(),
        panel_uris=[panel_uri(NO_BEAT_IDS_ID, position) for position in range(1, 6)],
    )


def early_record(created_at: datetime = CREATED) -> RunRecord:
    return RunRecord(
        run_id=EARLY_ID,
        created_at=created_at,
        updated_at=created_at,
        script_title=TITLE,
        stage="failed",
        error="Code: 181. FINAL is not supported for SharedMergeTree.",
    )


# --- Partial runs ------------------------------------------------------------


def test_partial_run_keeps_stored_panels_and_renders_only_the_missing_one(world):
    stored = world.seed(partial_record())
    kept_before = [uri for uri in stored.panel_uris if uri]

    response = world.resume(PARTIAL_ID, {"fountain_text": SCENE})

    assert response.status_code == 202
    assert response.json() == {"run_id": PARTIAL_ID, "resume": "partial"}
    # Only beat 4 was drawn and uploaded; no stored panel was regenerated.
    assert world.images == 1
    assert world.uploads == [f"runs/{PARTIAL_ID}/panel_04.png"]
    assert world.breakdowns == []

    final = world.db[PARTIAL_ID]
    assert final.stage == "done"
    assert final.error == ""
    assert final.run_id == PARTIAL_ID
    assert final.script_title == TITLE
    assert final.created_at == CREATED
    # The stored URIs are unchanged and at their beats; the new one fills beat 4.
    assert final.panel_uris == [
        kept_before[0],
        kept_before[1],
        kept_before[2],
        panel_uri(PARTIAL_ID, 4),
        kept_before[3],
        kept_before[4],
    ]
    assert final.panel_beat_ids == ["1", "2", "3", "4", "5", "6"]
    assert final.audio_uri == f"{BUCKET}/runs/{PARTIAL_ID}/tableread.wav"
    assert final.animatic_uri == f"{BUCKET}/runs/{PARTIAL_ID}/animatic.mp4"
    # No version written during the resume ever lost a kept panel.
    for version in world.writes:
        assert set(kept_before) <= set(version.panel_uris), version.stage
        assert len(version.panel_beat_ids) == len(version.panel_uris)
    assert [version.stage for version in world.writes] == [
        "panels 5/6",
        "panels 6/6",
        "reading",
        "assembling",
        "done",
    ]
    # Kept panels were fetched for assembly, not redrawn, and assembled in beat order.
    assert sorted(world.downloads) == sorted(kept_before)
    assert [panel.beat_id for panel in world.assembled[0]] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    ]
    assert [panel.gcs_uri for panel in world.assembled[0]] == final.panel_uris
    # The read performed the resupplied scene's dialogue.
    assert len(world.table_reads) == 1
    assert any(
        getattr(element, "type", "") == "dialogue"
        for element in world.table_reads[0].elements
    )
    # The live record serves the same card, completed.
    live = world.client.get(f"/api/runs/{PARTIAL_ID}").json()
    assert live["stage"] == "done" and live["error"] is None
    assert len(live["panel_urls"]) == 6


def test_partial_run_without_beat_ids_gets_them_from_its_breakdown_in_order(world):
    stored = world.seed(no_beat_ids_record())
    assert stored.panel_beat_ids == []

    response = world.resume(NO_BEAT_IDS_ID, {"fountain_text": SCENE})

    assert response.status_code == 202
    # All five panels exist: none is drawn, and none is uploaded.
    assert world.images == 0
    assert world.uploads == []
    # The very first version written already carries the restored ids.
    assert world.writes[0].panel_beat_ids == ["10", "20", "30", "40", "50"]
    assert world.writes[0].panel_uris == stored.panel_uris
    final = world.db[NO_BEAT_IDS_ID]
    assert final.stage == "done"
    assert final.panel_uris == stored.panel_uris
    assert final.panel_beat_ids == ["10", "20", "30", "40", "50"]
    assert [version.stage for version in world.writes] == [
        "panels 5/5",
        "reading",
        "assembling",
        "done",
    ]


def test_partial_run_that_has_its_read_resumes_without_the_scene(world):
    record = no_beat_ids_record()
    record.audio_uri = f"{BUCKET}/runs/{NO_BEAT_IDS_ID}/tableread.wav"
    record.duration_seconds = 12.0
    record.error = "ffmpeg failed (1)"
    world.seed(record)

    response = world.resume(NO_BEAT_IDS_ID)

    assert response.status_code == 202
    assert world.table_reads == []
    assert record.audio_uri in world.downloads
    final = world.db[NO_BEAT_IDS_ID]
    assert final.stage == "done"
    assert final.audio_uri == record.audio_uri
    assert final.error == ""


def test_partial_run_whose_read_is_missing_needs_the_scene(world):
    world.seed(partial_record())

    response = world.resume(PARTIAL_ID, {"fountain_text": None})

    assert response.status_code == 422
    assert "scene is needed" in response.json()["detail"]
    assert world.writes == []
    assert world.db[PARTIAL_ID].stage == "failed"


def test_partial_run_refuses_a_different_scene(world):
    world.seed(partial_record())

    response = world.resume(PARTIAL_ID, {"fountain_text": OTHER_SCENE})

    assert response.status_code == 422
    assert "not this run's scene" in response.json()["detail"]
    assert world.writes == []


def test_a_resume_that_fails_again_replaces_the_error_and_keeps_the_panels(world):
    stored = world.seed(no_beat_ids_record())
    world.table_read_error = RuntimeError("429 RESOURCE_EXHAUSTED on the TTS model")

    response = world.resume(NO_BEAT_IDS_ID, {"fountain_text": SCENE})

    assert response.status_code == 202
    final = world.db[NO_BEAT_IDS_ID]
    assert final.stage == "failed"
    assert final.error == "429 RESOURCE_EXHAUSTED on the TTS model"
    assert final.panel_uris == stored.panel_uris
    assert final.panel_beat_ids == ["10", "20", "30", "40", "50"]
    # It can be resumed again once the fault is gone.
    world.table_read_error = None
    assert world.resume(NO_BEAT_IDS_ID, {"fountain_text": SCENE}).status_code == 202
    assert world.db[NO_BEAT_IDS_ID].stage == "done"
    assert world.db[NO_BEAT_IDS_ID].error == ""


def test_a_cancelled_run_can_be_resumed(world):
    record = no_beat_ids_record()
    record.stage = "cancelled"
    world.seed(record)

    assert world.resume(NO_BEAT_IDS_ID, {"fountain_text": SCENE}).status_code == 202
    assert world.db[NO_BEAT_IDS_ID].stage == "done"


# --- Early runs --------------------------------------------------------------


def test_early_run_resumes_with_the_scene_and_finishes_under_its_run_id(world):
    # Stored DateTime values read back naive; the resume must not move them.
    world.seed(early_record(created_at=CREATED.replace(tzinfo=None)))

    response = world.resume(EARLY_ID, {"fountain_text": SCENE})

    assert response.status_code == 202
    assert response.json() == {"run_id": EARLY_ID, "resume": "early"}
    assert len(world.breakdowns) == 1
    assert world.breakdowns[0].slugline == SLUG
    assert world.uploads == [
        f"runs/{EARLY_ID}/{PANEL_FILENAME.format(index=position)}"
        for position in range(1, 6)
    ]
    final = world.db[EARLY_ID]
    assert final.stage == "done"
    assert final.error == ""
    assert final.script_title == TITLE
    assert final.created_at == CREATED
    assert final.scene_slug == SLUG
    assert len(final.panel_uris) == 5
    assert final.animatic_uri == f"{BUCKET}/runs/{EARLY_ID}/animatic.mp4"
    assert {version.run_id for version in world.writes} == {EARLY_ID}
    assert all(version.created_at == CREATED for version in world.writes)
    # Exactly the new-run stages, and like a new run it writes no beat ids.
    assert [version.stage for version in world.writes] == [
        "queued",
        "parsing",
        "breakdown",
        "panels 0/5",
        "panels 1/5",
        "panels 2/5",
        "panels 3/5",
        "panels 4/5",
        "panels 5/5",
        "reading",
        "assembling",
        "done",
    ]
    assert all(version.panel_beat_ids == [] for version in world.writes)
    # The old error stays until the run reaches done.
    assert world.writes[-2].error == early_record().error


@pytest.mark.parametrize(
    "body",
    [{"fountain_text": None}, {}, {"fountain_text": "   \n"}],
    ids=["null", "absent", "blank"],
)
def test_early_run_without_the_scene_is_refused_with_422(world, body):
    world.seed(early_record())

    response = world.resume(EARLY_ID, body)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "scene is needed" in detail and "fountain_text" in detail
    assert world.writes == []
    assert world.db[EARLY_ID].stage == "failed"


def test_early_run_with_an_unreadable_scene_is_refused_with_422(world):
    world.seed(early_record())

    response = world.resume(EARLY_ID, {"fountain_text": "no slugline here"})

    assert response.status_code == 422
    assert response.json()["detail"].startswith("The scene could not be read")
    assert world.writes == []


# --- Refusals ----------------------------------------------------------------


@pytest.mark.parametrize(
    "stage", ["done", "queued", "breakdown", "panels 2/5", "reading", "assembling"]
)
def test_only_failed_or_cancelled_runs_resume(world, stage):
    record = no_beat_ids_record()
    record.stage = stage
    world.seed(record)

    response = world.resume(NO_BEAT_IDS_ID, {"fountain_text": SCENE})

    assert response.status_code == 409
    assert f"this run is {stage}" in response.json()["detail"]
    assert world.writes == []


def test_a_run_already_being_resumed_is_refused(world):
    world.seed(no_beat_ids_record())
    api._resuming.add(NO_BEAT_IDS_ID)

    response = world.resume(NO_BEAT_IDS_ID, {"fountain_text": SCENE})

    assert response.status_code == 409
    assert world.writes == []


def test_unknown_run_is_404_behind_the_token(world):
    response = world.resume("no-such-run", {"fountain_text": SCENE})

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


# --- The admin guard ---------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": TOKEN},
        {"Authorization": f"Basic {TOKEN}"},
        {"X-Goog-Api-Key": TOKEN},
    ],
    ids=["none", "wrong", "no-scheme", "basic", "api-key-header"],
)
def test_without_the_admin_token_the_route_is_404(world, headers):
    world.seed(partial_record())

    response = world.resume(PARTIAL_ID, {"fountain_text": SCENE}, headers=headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert world.reads == [] and world.writes == []


def test_without_the_token_even_a_malformed_body_is_404(world):
    response = world.client.post(
        f"/api/runs/{PARTIAL_ID}/resume",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_a_malformed_body_with_the_token_is_422(world):
    world.seed(partial_record())

    response = world.client.post(
        f"/api/runs/{PARTIAL_ID}/resume",
        content=b"{not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert world.writes == []


def test_the_route_is_closed_while_no_admin_token_is_configured(world, monkeypatch):
    monkeypatch.delenv("FIRST_READ_ADMIN_TOKEN")
    world.seed(partial_record())

    assert world.resume(PARTIAL_ID, {"fountain_text": SCENE}).status_code == 404
    monkeypatch.setenv("FIRST_READ_ADMIN_TOKEN", "   ")
    assert (
        world.resume(
            PARTIAL_ID, {"fountain_text": SCENE}, headers={"Authorization": "Bearer "}
        ).status_code
        == 404
    )
    assert world.reads == [] and world.writes == []


def test_the_route_is_not_advertised(world):
    paths = world.client.get("/openapi.json").json()["paths"]

    assert "/api/previz" in paths
    assert not any("resume" in path for path in paths)


# --- POST /api/previz is unchanged -------------------------------------------


def test_previz_still_creates_a_new_run_and_completes_it(world):
    response = world.client.post(
        "/api/previz", json={"script_title": TITLE, "fountain_text": SCENE}
    )

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert list(response.json()) == ["run_id"]
    assert run_id not in (PARTIAL_ID, NO_BEAT_IDS_ID, EARLY_ID)
    final = world.db[run_id]
    assert final.stage == "done"
    assert final.error == ""
    assert final.script_title == TITLE
    assert len(final.panel_uris) == 5
    # All beats rendered, in order; no beat ids are written for a new run.
    assert world.uploads == [
        f"runs/{run_id}/{PANEL_FILENAME.format(index=position)}"
        for position in range(1, 6)
    ]
    assert all(version.panel_beat_ids == [] for version in world.writes)
    assert world.reads == []


def test_previz_keeps_its_shared_hourly_cap(world, monkeypatch):
    monkeypatch.setenv("MAX_RUNS_PER_HOUR", "0")

    response = world.client.post(
        "/api/previz", json={"script_title": TITLE, "fountain_text": SCENE}
    )

    assert response.status_code == 429
    assert world.writes == []


def test_previz_validation_is_unchanged(world):
    response = world.client.post(
        "/api/previz", json={"script_title": "", "fountain_text": SCENE}
    )

    assert response.status_code == 422
