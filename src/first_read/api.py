"""FastAPI surface and background pipeline runner."""

import asyncio
import contextvars
import hmac
import os
import random
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from pydantic import BaseModel, Field, ValidationError

from first_read.gcs import download_bytes, signed_url_for_uri
from first_read.memory import get_run as get_durable_run
from first_read.memory import get_run_for_resume as get_resumable_run
from first_read.memory import list_runs as list_durable_runs
from first_read.memory import lookup_characters, search_assets
from first_read.models import (
    AUDIO_FILENAME,
    OUTPUT_ROOT,
    PANEL_FILENAME,
    RANDOMIZE_POOLS,
)
from first_read.parse import parse_fountain
from first_read.schema import (
    AudioOutput,
    Breakdown,
    PanelOutput,
    RunRecord,
    Scene,
    StoryResponse,
    StorySpec,
)
from first_read.store import initialize_schema, upsert_character, upsert_run
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

    def browser_url(uri: str) -> str | None:
        if not uri:
            return None
        try:
            return signed_url_for_uri(uri)
        except ValueError:
            return None

    panel_urls = [url for uri in record.panel_uris if (url := browser_url(uri))]
    audio_url = browser_url(record.audio_uri)
    animatic_url = browser_url(record.animatic_uri)
    return RunResponse(
        run_id=record.run_id,
        script_title=record.script_title,
        scene_slug=record.scene_slug,
        stage=record.stage,
        breakdown=breakdown,
        panel_urls=panel_urls,
        audio_url=audio_url,
        audio_duration_seconds=(record.duration_seconds if audio_url else None),
        animatic_url=animatic_url,
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
        # A new run has no error here; a resumed early run drops its old one.
        record.error = ""
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


# --- Resume: finish a failed or cancelled run in place (WO-PUBLIC-01) ---------
#
# A *partial* run stored its breakdown: it resumes from that breakdown, keeps
# every stored panel, renders only the missing ones, then the read, then the
# animatic. An *early* run stopped before its breakdown and reruns the whole
# pipeline from a resupplied scene. Either way the run keeps its run_id, title
# and created_at, so the gallery card itself completes.
#
# Assumption beyond the work order: the table read performs the scene's
# dialogue, and no table stores scene text; the breakdown holds only what the
# camera sees. A partial run whose read is still to be made therefore also
# needs fountain_text. Only a run that already has its read (it failed while
# assembling) resumes without it.


class ResumeRequest(BaseModel):
    fountain_text: str | None = None


_RESUMABLE_STAGES = frozenset({"failed", "cancelled"})
_GCS_URI = re.compile(r"^gs://[^/]+/.+")
# Per-process guard against a second resume of a run this process is resuming.
_resuming: set[str] = set()
_resuming_lock = threading.Lock()


def _require_admin(authorization: str | None) -> None:
    """Answer like an unknown route unless the admin token is presented.

    The service had no administrative token before this route, so the token is
    FIRST_READ_ADMIN_TOKEN, sent as ``Authorization: Bearer <token>``. While the
    variable is unset or empty the route is closed to everyone.
    """
    expected = os.environ.get("FIRST_READ_ADMIN_TOKEN", "").strip()
    scheme, _, presented = (authorization or "").strip().partition(" ")
    if not (
        expected
        and scheme.lower() == "bearer"
        and hmac.compare_digest(presented.strip().encode(), expected.encode())
    ):
        raise HTTPException(status_code=404, detail="Not Found")


@dataclass
class _ResumePlan:
    kind: str  # "partial" or "early"
    scene: Scene
    fountain_text: str = ""
    breakdown: Breakdown | None = None
    kept: dict[int, str] = field(default_factory=dict)  # beat position -> URI
    missing: list[int] = field(default_factory=list)  # beat positions to render
    reuse_audio: bool = False

    @property
    def first_stage(self) -> str:
        if self.breakdown is None:
            return "queued"
        return f"panels {len(self.kept)}/{len(self.breakdown.beats)}"


def _kept_panels(record: RunRecord, breakdown: Breakdown) -> dict[int, str]:
    """Place each stored panel URI at the 1-based beat position it depicts.

    1. A URI named by generate_storyboard, ``runs/<run_id>/panel_NN.png``, is at
       position NN: the name is written from the beat's position.
    2. Otherwise, when ``panel_beat_ids`` is aligned with ``panel_uris``, the
       beat id gives the position.
    3. Any URI still unplaced takes the earliest free position, in stored order:
       panels render one at a time, so stored order is beat order.

    Empty, malformed and repeated URIs are not panels. Rule 1 comes first, so a
    missing position never names an object that a kept panel already uses.
    """
    total = len(breakdown.beats)
    entries: list[tuple[int, str]] = []
    seen: set[str] = set()
    for offset, uri in enumerate(record.panel_uris):
        if isinstance(uri, str) and _GCS_URI.match(uri) and uri not in seen:
            seen.add(uri)
            entries.append((offset, uri))

    kept: dict[int, str] = {}
    placed: set[int] = set()

    def place(offset: int, uri: str, position: int | None) -> None:
        if position is not None and 1 <= position <= total and position not in kept:
            kept[position] = uri
            placed.add(offset)

    named = re.compile(rf"/runs/{re.escape(record.run_id)}/panel_(\d+)\.png$")
    for offset, uri in entries:
        if match := named.search(uri):
            place(offset, uri, int(match.group(1)))

    if len(record.panel_beat_ids) == len(record.panel_uris):
        position_of_beat: dict[str, int] = {}
        for position, beat in enumerate(breakdown.beats, start=1):
            position_of_beat.setdefault(beat.beat_id, position)
        for offset, uri in entries:
            if offset not in placed:
                place(offset, uri, position_of_beat.get(record.panel_beat_ids[offset]))

    unplaced = [(offset, uri) for offset, uri in entries if offset not in placed]
    free = [position for position in range(1, total + 1) if position not in kept]
    for (offset, uri), position in zip(unplaced, free):
        place(offset, uri, position)
    return dict(sorted(kept.items()))


def _parse_resume_scene(fountain_text: str) -> Scene:
    try:
        return parse_fountain(fountain_text)
    except (ValueError, NotImplementedError) as error:
        raise HTTPException(
            status_code=422, detail=f"The scene could not be read: {error}"
        ) from error


def _require_matching_scene(
    scene: Scene, record: RunRecord, breakdown: Breakdown
) -> None:
    """A resupplied scene must be the one the stored breakdown was made from."""

    def heading(slugline: str) -> str:
        return " ".join(slugline.upper().split())

    if record.scene_slug and heading(scene.slugline) != heading(record.scene_slug):
        raise HTTPException(
            status_code=422,
            detail=(
                f"The supplied scene is not this run's scene: it begins "
                f"{scene.slugline!r}, and the run's begins {record.scene_slug!r}."
            ),
        )
    cast = sorted(character.name for character in breakdown.characters)
    if sorted(scene.characters) != cast:
        raise HTTPException(
            status_code=422,
            detail=(
                "The supplied scene's characters "
                f"({', '.join(sorted(scene.characters))}) are not the characters "
                f"of this run's breakdown ({', '.join(cast)})."
            ),
        )


def _plan_resume(record: RunRecord, fountain_text: str | None) -> _ResumePlan:
    text = fountain_text if fountain_text and fountain_text.strip() else None
    if not record.breakdown_json:
        if text is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This run stopped before its breakdown and its scene is not "
                    "stored, so the scene is needed: send it again as fountain_text."
                ),
            )
        return _ResumePlan(
            kind="early", scene=_parse_resume_scene(text), fountain_text=text
        )

    try:
        breakdown = Breakdown.model_validate_json(record.breakdown_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=409,
            detail="This run's stored breakdown cannot be read, so it cannot resume.",
        ) from error
    kept = _kept_panels(record, breakdown)
    missing = [p for p in range(1, len(breakdown.beats) + 1) if p not in kept]
    reuse_audio = bool(_GCS_URI.match(record.audio_uri)) and (
        record.duration_seconds > 0
    )
    if text is not None:
        scene = _parse_resume_scene(text)
        _require_matching_scene(scene, record, breakdown)
    elif reuse_audio:
        # Panels and assembly need only the heading, which the run stores.
        try:
            scene = parse_fountain(record.scene_slug)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This run's stored slugline cannot be read, so the scene is "
                    "needed: send it again as fountain_text."
                ),
            ) from error
        scene.characters = list(record.characters) or [
            character.name for character in breakdown.characters
        ]
    else:
        raise HTTPException(
            status_code=422,
            detail=(
                "This run's table read is still to be made and its scene is not "
                "stored, so the scene is needed for its dialogue: send it again "
                "as fountain_text."
            ),
        )
    return _ResumePlan(
        kind="partial",
        scene=scene,
        breakdown=breakdown,
        kept=kept,
        missing=missing,
        reuse_audio=reuse_audio,
    )


async def _recall_cast(breakdown: Breakdown, script_title: str) -> None:
    """Bring a stored breakdown's cast back in line with character memory.

    The breakdown stage ends by recording its characters, but a run stored
    before the ``characters`` table fix may have recorded them in a table that
    no longer exists. A character memory lacks is recorded now, as
    generate_breakdown would have, so its sheet can be stored against it. A
    remembered voice wins, as in generate_breakdown. The breakdown's
    descriptions are kept: the run's panels were drawn from them.
    """
    remembered = await lookup_characters(
        [character.name for character in breakdown.characters]
    )
    for character in breakdown.characters:
        values = remembered.get(character.name)
        if values is None:
            await asyncio.to_thread(upsert_character, character, script_title)
        elif values.get("voice_name"):
            character.voice_name = values["voice_name"]


def _download_to(uri: str, local_path: Path) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(download_bytes(uri))
    return local_path


def _kept_audio(record: RunRecord) -> AudioOutput:
    local_path = _download_to(
        record.audio_uri, OUTPUT_ROOT / record.run_id / AUDIO_FILENAME
    )
    return AudioOutput(
        url=signed_url_for_uri(record.audio_uri),
        gcs_uri=record.audio_uri,
        local_path=str(local_path),
        duration_seconds=record.duration_seconds,
    )


def _panels_for_assembly(
    run_id: str,
    breakdown: Breakdown,
    uris: dict[int, str],
    rendered: dict[int, PanelOutput],
) -> list[PanelOutput]:
    """Beat-ordered panels with local files; kept panels are downloaded, never redrawn."""
    panels = []
    for position in sorted(uris):
        if position in rendered:
            panels.append(rendered[position])
            continue
        uri = uris[position]
        local_path = _download_to(
            uri, OUTPUT_ROOT / run_id / PANEL_FILENAME.format(index=position)
        )
        panels.append(
            PanelOutput(
                beat_id=breakdown.beats[position - 1].beat_id,
                url=signed_url_for_uri(uri),
                gcs_uri=uri,
                local_path=str(local_path),
            )
        )
    return panels


async def _resume_partial(record: RunRecord, plan: _ResumePlan) -> None:
    run_id, scene, breakdown = record.run_id, plan.scene, plan.breakdown
    beats = breakdown.beats
    uris = dict(plan.kept)
    rendered: dict[int, PanelOutput] = {}

    def publish_panels() -> None:
        # panel_uris in beat order; panel_beat_ids restored from the breakdown.
        positions = sorted(uris)
        record.panel_uris = [uris[position] for position in positions]
        record.panel_beat_ids = [beats[position - 1].beat_id for position in positions]

    try:
        await asyncio.to_thread(initialize_schema)
        publish_panels()
        await _transition(record, f"panels {len(uris)}/{len(beats)}")
        if plan.missing or not plan.reuse_audio:
            await _recall_cast(breakdown, record.script_title)

        if plan.missing:
            position_of_file = {
                PANEL_FILENAME.format(index=position): position
                for position in plan.missing
            }

            def panel_ready(_done: int, _total: int, panel: PanelOutput) -> None:
                position = position_of_file[Path(panel.local_path).name]
                rendered[position] = panel
                uris[position] = panel.gcs_uri
                publish_panels()
                record.stage = f"panels {len(uris)}/{len(beats)}"
                _touch_and_upsert(record)

            outputs = await asyncio.to_thread(
                generate_storyboard,
                scene,
                breakdown,
                run_id,
                record.script_title,
                panel_ready,
                positions=plan.missing,
            )
            for position, panel in zip(plan.missing, outputs):
                rendered[position] = panel
                uris[position] = panel.gcs_uri
            publish_panels()

        if plan.reuse_audio:
            audio = await asyncio.to_thread(_kept_audio, record)
        else:
            await _transition(record, "reading")
            audio = await asyncio.to_thread(
                generate_table_read, scene, breakdown, run_id, record.script_title
            )
            record.audio_uri = audio.gcs_uri
            record.duration_seconds = audio.duration_seconds

        await _transition(record, "assembling")
        panels = await asyncio.to_thread(
            _panels_for_assembly, run_id, breakdown, uris, rendered
        )
        animatic = await asyncio.to_thread(
            assemble_animatic,
            scene,
            breakdown,
            panels,
            audio,
            run_id,
            record.script_title,
        )
        record.animatic_uri = animatic.gcs_uri
        record.error = ""
        await _transition(record, "done")
    except Exception as error:  # noqa: BLE001 - background jobs must expose failure state
        record.stage = "failed"
        record.error = str(error)
        with suppress(Exception):
            await asyncio.to_thread(_touch_and_upsert, record)


async def _resume_pipeline(record: RunRecord, plan: _ResumePlan) -> None:
    try:
        if plan.kind == "early":
            # Exactly the new-run pipeline, under the run's own id and title.
            record.panel_uris = []
            record.panel_beat_ids = []
            request = PrevizRequest.model_construct(
                script_title=record.script_title, fountain_text=plan.fountain_text
            )
            await _run_pipeline(record.run_id, request)
        else:
            await _resume_partial(record, plan)
    finally:
        with _resuming_lock:
            _resuming.discard(record.run_id)


def _as_utc(record: RunRecord) -> None:
    """Stored DateTime values read back naive, in UTC. clickhouse-connect would
    write a naive value back as process-local time and move the card's
    creation time, so mark them as UTC before the run is written again."""
    for name in ("created_at", "updated_at"):
        value = getattr(record, name)
        if value.tzinfo is None:
            setattr(record, name, value.replace(tzinfo=UTC))


# Deliberately absent from the OpenAPI schema and from every UI: without the
# admin token it answers 404, like any route that does not exist.
@app.post("/api/runs/{run_id}/resume", status_code=202, include_in_schema=False)
async def resume_run(run_id: str, request: Request, background_tasks: BackgroundTasks):
    # The body is read by hand so nothing, not even a 422 for bad JSON, is
    # answered before the token check.
    _require_admin(request.headers.get("authorization"))
    try:
        body = ResumeRequest.model_validate_json(await request.body() or b"{}")
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail='The body must be JSON of the form {"fountain_text": string or null}.',
        ) from error

    record = await get_resumable_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if record.stage not in _RESUMABLE_STAGES:
        raise HTTPException(
            status_code=409,
            detail=(
                "Only a failed or cancelled run can be resumed; "
                f"this run is {record.stage}."
            ),
        )
    plan = _plan_resume(record, body.fountain_text)
    with _resuming_lock:
        if run_id in _resuming:
            raise HTTPException(
                status_code=409, detail="This run is already being resumed."
            )
        _resuming.add(run_id)

    _as_utc(record)
    record.stage = plan.first_stage
    _runs[run_id] = record
    background_tasks.add_task(_resume_pipeline, record, plan)
    return {"run_id": run_id, "resume": plan.kind}


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
