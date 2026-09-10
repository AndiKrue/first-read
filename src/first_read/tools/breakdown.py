"""Turn a parsed scene into a strict visual breakdown."""

import asyncio
import json
import re

from google import genai
from google.genai import types

from first_read.config import Settings
from first_read.memory import lookup_characters
from first_read.models import REASONING_FALLBACK_MODEL, REASONING_MODEL
from first_read.schema import Breakdown, Scene
from first_read.store import upsert_character


def _client() -> genai.Client:
    settings = Settings.load()
    return genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )


def _json_text(response) -> str:
    text = response.text or ""
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _request_breakdown(prompt: str, model: str):
    client = _client()
    return client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )


async def generate_breakdown(scene: Scene, script_title: str) -> Breakdown:
    """Generate 4–6 validated beats after consulting persistent memory."""
    remembered = await lookup_characters(scene.characters)
    fixed = {
        name: {
            field: values.get(field, "")
            for field in ("visual_description", "wardrobe", "voice_name")
        }
        for name, values in remembered.items()
        if values.get("visual_description")
    }
    prompt = f"""Return one JSON object only. Do not use markdown or prose.
Break this single scene into 4 to 6 ordered visual beats.
Each beat has beat_id, description of only what the camera sees, shot_type chosen
from WIDE, MEDIUM, CLOSE, OTS, and characters_present.
Characters must contain name, visual_description, wardrobe, and voice_name. For
genuinely new characters invent a stable visual description containing age range,
build, hair, and distinguishing features. Use every non-empty field in these
remembered records verbatim and fill only empty fields: {json.dumps(fixed)}
`wardrobe`: exactly what this character wears in this scene, garment by garment,
top to bottom, stating whether each is fastened or open. Be specific and complete
— "dark wool overcoat worn open over a buttoned grey shirt, black trousers, ankle
boots". Never leave a layer unstated. This is fixed for the whole scene and will
be reused in every panel.
Include one stable location_description paragraph and a tone of two or three words.
`staging`: exactly one sentence stating each character's physical position relative
to the room and to every other character. It must contain no weather, lighting,
mood, or camera language. Example: "June and Marco sit opposite each other in a
worn vinyl booth; June is on the side against the window, while Marco has his back
to the rest of the empty cafe."
Every scene character must appear once in characters; invent nobody else.
Scene JSON: {scene.model_dump_json()}
Required shape: {{"beats": [], "characters": [], "location_description": "", "staging": "", "tone": ""}}
"""
    last_error: Exception | None = None
    for model in (REASONING_MODEL, REASONING_FALLBACK_MODEL):
        try:
            response = await asyncio.to_thread(_request_breakdown, prompt, model)
            breakdown = Breakdown.model_validate_json(_json_text(response))
            names = [character.name for character in breakdown.characters]
            if set(names) != set(scene.characters) or len(names) != len(
                scene.characters
            ):
                raise ValueError(
                    "breakdown characters must exactly match the parsed scene"
                )
            if len({beat.beat_id for beat in breakdown.beats}) != len(breakdown.beats):
                raise ValueError("breakdown beat_id values must be unique")
            for beat in breakdown.beats:
                unknown = set(beat.characters_present) - set(scene.characters)
                if unknown:
                    raise ValueError(
                        f"beat {beat.beat_id} has unknown characters: {unknown}"
                    )
            by_name = {character.name: character for character in breakdown.characters}
            for name, values in fixed.items():
                by_name[name].visual_description = values["visual_description"]
                if values["wardrobe"]:
                    by_name[name].wardrobe = values["wardrobe"]
                by_name[name].voice_name = values.get("voice_name", "")
            for character in breakdown.characters:
                if character.name not in fixed:
                    character.voice_name = ""
            break
        except Exception as error:  # noqa: BLE001 - retry fallback model on any failure
            last_error = error
    else:
        raise RuntimeError(
            "Both reasoning models failed to produce a complete valid breakdown: "
            + repr(last_error)
        ) from last_error
    for character in breakdown.characters:
        await asyncio.to_thread(upsert_character, character, script_title)
    return breakdown


async def breakdown_tool(scene_json: str, script_title: str) -> dict:
    """ADK-facing breakdown tool with JSON-serializable arguments/results."""
    scene = Scene.model_validate_json(scene_json)
    return (await generate_breakdown(scene, script_title)).model_dump()
