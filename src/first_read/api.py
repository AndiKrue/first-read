"""FastAPI surface and background pipeline runner."""

import asyncio
import contextvars
import os
import random
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from pydantic import BaseModel, Field

from first_read.gcs import signed_url_for_uri
from first_read.memory import get_run as get_durable_run
from first_read.memory import list_runs as list_durable_runs
from first_read.memory import search_assets
from first_read.models import RANDOMIZE_POOLS
from first_read.parse import parse_fountain
from first_read.schema import (
    Breakdown,
    PanelOutput,
    RunRecord,
    StoryResponse,
    StorySpec,
)
from first_read.store import initialize_schema, upsert_run
from first_read.tools import breakdown as breakdown_module
from first_read.tools import storyboard as storyboard_module
from first_read.tools import tableread as tableread_module
from first_read.tools.assemble import assemble_animatic
from first_read.tools.breakdown import generate_breakdown
from first_read.tools.story import generate_scene
from first_read.tools.storyboard import generate_storyboard
from first_read.tools.tableread import generate_table_read


class PrevizRequest(BaseModel):
    script_title: str = Field(min_length=1, max_length=200)
    fountain_text: str = Field(min_length=1)


class RunResponse(BaseModel):
    run_id: str
    script_title: str
    scene_slug: str
    stage: str
    breakdown: Breakdown | None = None
    panel_urls: list[str] = Field(default_factory=list)
    audio_url: str | None = None
    audio_duration_seconds: float | None = None
    animatic_url: str | None = None
    error: str | None = None


class SampleResponse(BaseModel):
    name: str
    title: str
    text: str


def _suggest_title(fountain_text: str, situation: str) -> str:
    scene = parse_fountain(fountain_text)
    location = scene.location.title().replace("'S", "'s")
    situation_words = re.sub(r"[^\w\s'-]", "", situation).split()[:6]
    if not situation_words:
        return location
    return f"{location}: {' '.join(situation_words).capitalize()}"


app = FastAPI(title="FIRST READ", version="0.1.0")
_runs: dict[str, RunRecord] = {}

# This rolling window is deliberately process-local and resets on restart. It is
# a demo cost guard, not access control.
_run_starts: deque[float] = deque()
_rate_lock = threading.Lock()
_request_api_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_api_key", default=None
)


class _ContextThreadPoolExecutor(ThreadPoolExecutor):
    """Carry the request-only API key into storyboard's panel worker threads."""

    def submit(self, fn, /, *args, **kwargs):
        context = contextvars.copy_context()
        return super().submit(context.run, fn, *args, **kwargs)


def _request_client(default_factory):
    api_key = _request_api_key.get()
    if api_key:
        return genai.Client(api_key=api_key)
    return default_factory()


# The generation modules retain the normal Vertex service-account factories.
# These context-aware adapters only select Developer API auth for one BYOK run.
_default_breakdown_client = breakdown_module._client
_default_storyboard_client = storyboard_module._client
_default_tableread_client = tableread_module._client
breakdown_module._client = lambda: _request_client(_default_breakdown_client)
storyboard_module._client = lambda: _request_client(_default_storyboard_client)
tableread_module._client = lambda: _request_client(_default_tableread_client)
storyboard_module.ThreadPoolExecutor = _ContextThreadPoolExecutor


def _max_runs_per_hour() -> int:
    try:
        return max(0, int(os.environ.get("MAX_RUNS_PER_HOUR", "3")))
    except ValueError:
        return 3


def _claim_shared_run() -> bool:
    cutoff = time.monotonic() - 3600
    with _rate_lock:
        while _run_starts and _run_starts[0] <= cutoff:
            _run_starts.popleft()
        if len(_run_starts) >= _max_runs_per_hour():
            return False
        _run_starts.append(time.monotonic())
        return True


def _touch_and_upsert(record: RunRecord) -> None:
    record.updated_at = datetime.now(UTC)
    upsert_run(record)


async def _transition(record: RunRecord, stage: str) -> None:
    record.stage = stage
    await asyncio.to_thread(_touch_and_upsert, record)


def _run_response(record: RunRecord) -> RunResponse:
    breakdown = (
        Breakdown.model_validate_json(record.breakdown_json)
        if record.breakdown_json
        else None
    )
    return RunResponse(
        run_id=record.run_id,
        script_title=record.script_title,
        scene_slug=record.scene_slug,
        stage=record.stage,
        breakdown=breakdown,
        panel_urls=[signed_url_for_uri(uri) for uri in record.panel_uris],
        audio_url=signed_url_for_uri(record.audio_uri) if record.audio_uri else None,
        audio_duration_seconds=(record.duration_seconds if record.audio_uri else None),
        animatic_url=(
            signed_url_for_uri(record.animatic_uri) if record.animatic_uri else None
        ),
        error=record.error or None,
    )


async def _run_pipeline(
    run_id: str, request: PrevizRequest, api_key: str | None = None
) -> None:
    record = _runs[run_id]
    key_token = _request_api_key.set(api_key)
    try:
        await asyncio.to_thread(initialize_schema)
        await asyncio.to_thread(_touch_and_upsert, record)
        await _transition(record, "parsing")
        scene = parse_fountain(request.fountain_text)
        record.scene_slug = scene.slugline
        record.int_ext = scene.int_ext
        record.time_of_day = scene.time_of_day
        record.characters = scene.characters

        await _transition(record, "breakdown")
        breakdown = await generate_breakdown(scene, request.script_title)
        record.breakdown_json = breakdown.model_dump_json()
        record.tone = breakdown.tone

        def panel_ready(index: int, total: int, panel: PanelOutput) -> None:
            record.panel_uris.append(panel.gcs_uri)
            record.stage = f"panels {index}/{total}"
            _touch_and_upsert(record)

        await _transition(record, f"panels 0/{len(breakdown.beats)}")
        panels = await asyncio.to_thread(
            generate_storyboard,
            scene,
            breakdown,
            run_id,
            request.script_title,
            panel_ready,
        )
        record.panel_uris = [panel.gcs_uri for panel in panels]

        await _transition(record, "reading")
        audio = await asyncio.to_thread(
            generate_table_read, scene, breakdown, run_id, request.script_title
        )
        record.audio_uri = audio.gcs_uri
        record.duration_seconds = audio.duration_seconds

        await _transition(record, "assembling")
        animatic = await asyncio.to_thread(
            assemble_animatic,
            scene,
            breakdown,
            panels,
            audio,
            run_id,
            request.script_title,
        )
        record.animatic_uri = animatic.gcs_uri
        await _transition(record, "done")
    except Exception as error:  # noqa: BLE001 - background jobs must expose failure state
        record.stage = "failed"
        record.error = (
            "The supplied API key was rejected or the generation request failed."
            if api_key
            else str(error)
        )
        with suppress(Exception):
            await asyncio.to_thread(_touch_and_upsert, record)
    finally:
        _request_api_key.reset(key_token)


@app.post("/api/previz", status_code=202)
async def create_previz(
    request: PrevizRequest,
    background_tasks: BackgroundTasks,
    api_key: str | None = Header(default=None, alias="X-Goog-Api-Key"),
):
    if not api_key and not _claim_shared_run():
        raise HTTPException(
            status_code=429,
            detail=(
                "The shared demo key is exhausted for this hour. Previous runs "
                "remain viewable in the gallery, or supply your own Google API key."
            ),
        )
    run_id = str(uuid.uuid4())
    _runs[run_id] = RunRecord(run_id=run_id, script_title=request.script_title)
    background_tasks.add_task(_run_pipeline, run_id, request, api_key)
    return {"run_id": run_id}


@app.post("/api/story", response_model=StoryResponse)
async def create_story(spec: StorySpec) -> StoryResponse:
    fountain_text = await asyncio.to_thread(generate_scene, spec)
    return StoryResponse(
        fountain_text=fountain_text,
        title=_suggest_title(fountain_text, spec.situation),
    )


@app.get("/api/story/random", response_model=StorySpec)
async def random_story() -> StorySpec:
    return StorySpec(
        genre=random.choice(RANDOMIZE_POOLS["genre"]),
        setting=random.choice(RANDOMIZE_POOLS["setting"]),
        situation=random.choice(RANDOMIZE_POOLS["situation"]),
    )


@app.get("/api/runs", response_model=list[RunResponse])
async def get_runs(limit: int = Query(default=24, ge=1, le=200)) -> list[RunResponse]:
    return [_run_response(record) for record in await list_durable_runs(limit)]


@app.get("/api/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str) -> RunResponse:
    record = _runs.get(run_id)
    if record is None:
        record = await get_durable_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Run not found")
        _runs[run_id] = record
    return _run_response(record)


@app.get("/api/samples", response_model=list[SampleResponse])
async def get_samples() -> list[SampleResponse]:
    sample_dir = Path(__file__).resolve().parents[2] / "samples"
    samples = []
    for path in sorted(sample_dir.glob("*.fountain")):
        text = path.read_text(encoding="utf-8")
        scene = parse_fountain(text)
        samples.append(
            SampleResponse(
                name=(
                    f"{scene.location.lower().capitalize()} — "
                    f"{scene.int_ext}, {scene.time_of_day.lower()}"
                ),
                title=path.stem.replace("_", " ").title(),
                text=text,
            )
        )
    return samples


@app.get("/api/search")
async def search_production_memory(
    character: str | None = Query(default=None),
    int_ext: str | None = Query(default=None),
    time_of_day: str | None = Query(default=None),
    tone: str | None = Query(default=None),
):
    assets = await search_assets(character, int_ext, time_of_day, tone)
    results = []
    for asset in assets:
        item = asset.model_dump(mode="json")
        item["url"] = signed_url_for_uri(asset.gcs_uri)
        results.append(item)
    return {"assets": results}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


_web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
if _web_dist.is_dir():
    _assets = _web_dist / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_web(path: str):
        candidate = (_web_dist / path).resolve()
        if candidate.is_file() and _web_dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_web_dist / "index.html")
