import time
"""Generate independently framed storyboard panels from character sheets."""

import asyncio
import hashlib
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

from first_read.config import Settings
from first_read.gcs import upload_bytes
from first_read.memory import lookup_characters
from first_read.models import (
    CHARACTER_SHEET_STYLE,
    OUTPUT_ROOT,
    PANEL_CONCURRENCY,
    PANEL_FILENAME,
    PANEL_STYLE,
    STORYBOARD_MODEL,
)
from first_read.schema import Asset, Beat, Breakdown, PanelOutput, Scene
from first_read.store import insert_asset, upsert_character_sheet

FRAME_INSTRUCTION = (
    "This is a full-bleed photographic composition. The image content extends to all "
    "four edges of the canvas with nothing surrounding it. There is no drawn frame, "
    "no ruled box, no white margin, no matte, no border of any kind — the artwork "
    "itself IS the entire image.\n"
    "Create exactly ONE storyboard image showing one camera shot and one moment in "
    "time. Not a page, not a grid, not a strip, not a sequence.\n"
    "Every surface in this image is blank. Signs, menu boards, posters, labels and "
    "screens carry no writing — they are plain shapes with no letters, numbers, "
    "glyphs or marks resembling text."
)
INTERIOR_WEATHER_CLAUSE = (
    "The camera is INSIDE the building. Rain, snow and weather are visible ONLY "
    "through the windows, beyond the glass. The interior air is completely clear — "
    "no rain streaks, no droplets, no precipitation of any kind crosses the room, "
    "the ceiling, the furniture or the characters."
)
CLOTHING_INSTRUCTION = (
    "Clothing must match the attached reference exactly. Garments stay fastened or "
    "open as described; no character is ever bare-chested or partially undressed "
    "unless the beat description explicitly says so."
)
INTERIOR_WEATHER_SUBSTITUTIONS = (
    (
        r"\brain\s+hammers?(?:\s+against)?\s+(?:the\s+|a\s+)?windows?\b",
        "a window streaked with rain on the outside",
    ),
    (
        (
            r"\brain\s+(?:beats?|lashes?|pelts?|drums?)\s+(?:against|on)\s+"
            r"(?:the\s+|a\s+)?windows?\b"
        ),
        "rain streaking the outside of the window",
    ),
    (
        (
            r"\brain\s+(?:runs?|streams?|streaks?)\s+down\s+"
            r"(?:the\s+|a\s+)?windows?\b"
        ),
        "rain streaking the outside of the window",
    ),
    (
        (
            r"\bsnow\s+(?:beats?|blows?|drifts?|swirls?)\s+(?:against|past)\s+"
            r"(?:the\s+|a\s+)?windows?\b"
        ),
        "snow visible outside the window",
    ),
    (
        (
            r"\b(?:rain|snow|hail|sleet)\s+(?:falls?|pours?|drives?|swirls?)\s+"
            r"(?:through|across|inside)\s+(?:the\s+)?(?:room|interior)\b"
        ),
        "weather visible beyond the windows",
    ),
)

_PERSIST_LOCK = threading.Lock()


def _trim_border(data: bytes) -> bytes:
    """Models draw a frame despite instruction; crop it deterministically."""
    from io import BytesIO

    from PIL import Image, ImageChops

    image = Image.open(BytesIO(data)).convert("RGB")
    background = Image.new("RGB", image.size, image.getpixel((0, 0)))
    mask = ImageChops.difference(image, background).convert("L")
    box = mask.point(lambda value: 255 if value > 12 else 0).getbbox()
    if not box:
        return data
    width, height = image.size
    if (box[2] - box[0]) < width * 0.5 or (box[3] - box[1]) < height * 0.5:
        return data
    buffer = BytesIO()
    image.crop(box).save(buffer, format="PNG")
    return buffer.getvalue()


def _client() -> genai.Client:
    settings = Settings.load()
    return genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )


def _generate_image(prompt: str, reference_uris: list[str] | None = None) -> bytes:
    parts: list = [prompt]
    parts.extend(
        types.Part.from_uri(file_uri=uri, mime_type="image/png")
        for uri in reference_uris or []
    )
    client = _client()
    client = _client()
    delay = 5.0
    for attempt in range(6):
        try:
            response = client.models.generate_content(
                model=STORYBOARD_MODEL,
                contents=parts,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"]
                ),
            )
            break
        except Exception as error:
            transient = "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error)
            if not transient or attempt == 5:
                raise
            time.sleep(delay)
            delay *= 2
    for part in response.candidates[0].content.parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is not None and inline_data.data:
            return _trim_border(inline_data.data)
    raise RuntimeError("Storyboard model returned no image part")


def _sheet_object_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "character"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"sheets/{slug}-{digest}.png"


def _character_sheet_prompt(visual_description: str, wardrobe: str = "") -> str:
    parts = [visual_description.strip().rstrip(".")]
    if wardrobe.strip():
        parts.append(f"Wardrobe: {wardrobe.strip().rstrip('.')}")
    parts.append(CHARACTER_SHEET_STYLE)
    return ". ".join(parts)


def ensure_character_sheet(
    name: str, visual_description: str, wardrobe: str = ""
) -> str:
    """Return a persistent sheet URI, generating and recording it only once."""
    remembered = asyncio.run(lookup_characters([name])).get(name, {})
    existing_uri = remembered.get("sheet_gcs_uri", "")
    if existing_uri:
        return existing_uri

    prompt = _character_sheet_prompt(visual_description, wardrobe)
    image_bytes = _generate_image(prompt)
    uploaded = upload_bytes(image_bytes, _sheet_object_name(name), "image/png")
    upsert_character_sheet(name, uploaded.gcs_uri)
    insert_asset(
        Asset(
            run_id=f"character-sheet:{name}",
            asset_type="sheet",
            script_title="",
            scene_slug="",
            int_ext="",
            time_of_day="",
            location="",
            characters=[name],
            tone="",
            prompt=prompt,
            model_id=STORYBOARD_MODEL,
            gcs_uri=uploaded.gcs_uri,
        )
    )
    return uploaded.gcs_uri


def _location_description_for_panel(description: str, int_ext: str) -> str:
    """Rewrite exterior weather action only when the camera is indoors."""
    if int_ext != "INT":
        return description
    sanitized = description
    for pattern, replacement in INTERIOR_WEATHER_SUBSTITUTIONS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    return sanitized


def _wardrobe_instruction(breakdown: Breakdown, beat: Beat) -> str | None:
    present = set(beat.characters_present)
    clauses = [
        f"{character.name} wears {character.wardrobe.strip().rstrip('.')}."
        for character in breakdown.characters
        if character.name in present and character.wardrobe.strip()
    ]
    if not clauses:
        return None
    return "Wardrobe continuity: " + " ".join(clauses)


def build_panel_prompt(scene: Scene, breakdown: Breakdown, beat: Beat) -> str:
    """Build one panel prompt from the beat and invariant scene facts."""
    instructions = [
        FRAME_INSTRUCTION,
        f"SHOT TYPE: {beat.shot_type}. The camera must use a {beat.shot_type} shot.",
        (
            "FIXED BLOCKING for every shot in this scene, never varied: "
            f"{breakdown.staging}. The characters occupy these positions in this "
            "shot as well, regardless of camera angle."
        ),
        f"Beat action: {beat.description}",
    ]
    if scene.int_ext == "INT":
        instructions.append(INTERIOR_WEATHER_CLAUSE)
    instructions.append(CLOTHING_INSTRUCTION)
    instructions.append(
        "Location: "
        + _location_description_for_panel(breakdown.location_description, scene.int_ext)
    )
    wardrobe_instruction = _wardrobe_instruction(breakdown, beat)
    if wardrobe_instruction:
        instructions.append(wardrobe_instruction)
    instructions.extend(
        [
            (
                "Attached character references define appearance and fixed clothing "
                "only. Framing, pose, and action must come from the beat description, "
                "and the shot type governs the camera."
            ),
            f"Style: {PANEL_STYLE}",
        ]
    )
    return "\n".join(instructions)


def generate_storyboard(
    scene: Scene,
    breakdown: Breakdown,
    run_id: str,
    script_title: str,
    on_panel: Callable[[int, int, PanelOutput], None] | None = None,
) -> list[PanelOutput]:
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sheet_uris = {
        character.name: ensure_character_sheet(
            character.name, character.visual_description, character.wardrobe
        )
        for character in breakdown.characters
    }
    total = len(breakdown.beats)
    callback_lock = threading.Lock()
    completed_panels = 0

    def render_panel(index: int, beat: Beat) -> PanelOutput:
        nonlocal completed_panels
        prompt = build_panel_prompt(scene, breakdown, beat)
        references = [
            sheet_uris[name] for name in beat.characters_present if name in sheet_uris
        ]
        image_bytes = _generate_image(prompt, references)
        local_path = run_dir / PANEL_FILENAME.format(index=index)
        local_path.write_bytes(image_bytes)
        uploaded = upload_bytes(
            image_bytes, f"runs/{run_id}/{local_path.name}", "image/png"
        )
        asset = Asset(
            run_id=run_id,
            asset_type="panel",
            script_title=script_title,
            scene_slug=scene.slugline,
            beat_id=beat.beat_id,
            shot_type=beat.shot_type,
            int_ext=scene.int_ext,
            time_of_day=scene.time_of_day,
            location=scene.location,
            characters=beat.characters_present,
            tone=breakdown.tone,
            prompt=prompt,
            model_id=STORYBOARD_MODEL,
            gcs_uri=uploaded.gcs_uri,
        )
        with _PERSIST_LOCK:
            insert_asset(asset)
        output = PanelOutput(
            beat_id=beat.beat_id,
            url=uploaded.signed_url,
            gcs_uri=uploaded.gcs_uri,
            local_path=str(local_path),
        )
        if on_panel:
            with callback_lock:
                completed_panels += 1
                on_panel(completed_panels, total, output)
        return output

    with ThreadPoolExecutor(max_workers=PANEL_CONCURRENCY) as executor:
        futures = [
            executor.submit(render_panel, index, beat)
            for index, beat in enumerate(breakdown.beats, start=1)
        ]
        return [future.result() for future in futures]


def storyboard_tool(
    scene_json: str, breakdown_json: str, run_id: str, script_title: str
) -> list[dict]:
    """ADK-facing storyboard tool."""
    outputs = generate_storyboard(
        Scene.model_validate_json(scene_json),
        Breakdown.model_validate_json(breakdown_json),
        run_id,
        script_title,
    )
    return [output.model_dump() for output in outputs]
