"""Generate an original, single-scene Fountain script."""

import re

from google import genai
from google.genai import types

from first_read.config import Settings
from first_read.models import REASONING_FALLBACK_MODEL, REASONING_MODEL
from first_read.parse import parse_fountain
from first_read.schema import StorySpec


def _client() -> genai.Client:
    settings = Settings.load()
    return genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )


def build_story_prompt(spec: StorySpec, parse_failure: str = "") -> str:
    """Build the writer prompt separately so its constraints are testable offline."""
    characters = ", ".join(spec.characters) if spec.characters else "choose two"
    setting = spec.setting or "choose an ordinary, human-scale setting"
    situation = spec.situation or "choose a small but meaningful conflict"
    tone = spec.tone or "choose a tone appropriate to the genre"
    correction = ""
    if parse_failure:
        correction = (
            f"\nThe previous draft failed validation: {parse_failure}\nCorrect it."
        )
    return f"""Write an original scene in plain Fountain text only.
Do not reproduce, adapt, quote, imitate, or use character names from any existing
film, television programme, book, or game. Invent every detail and name.

Genre: {spec.genre}
Setting: {setting}
Characters: {characters}
Situation: {situation}
Tone: {tone}

Requirements:
- Write exactly one scene.
- Begin with a valid Fountain slugline: INT. or EXT., then a location, a hyphen,
  and a time of day. INT. and EXT. are the only allowed slugline prefixes.
- Use two to four named characters, with each character cue in ALL CAPS.
- Write six to twelve dialogue exchanges.
- Include at least two action lines that clearly establish physical staging.
- Output plain Fountain only: no scene numbers, transitions, camera directions,
  markdown, commentary, title, or code fences.
- Keep the scene playable in approximately thirty seconds.
{correction}"""


def _request_scene(prompt: str, model: str) -> str:
    client = _client()
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.8),
    )
    return response.text or ""


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:fountain|text|plaintext)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def generate_scene(spec: StorySpec) -> str:
    """Generate Fountain and retry one parse failure with corrective context."""
    prompt = build_story_prompt(spec)
    last_error: ValueError | None = None
    for model in (REASONING_MODEL, REASONING_FALLBACK_MODEL):
        fountain_text = _strip_markdown_fence(_request_scene(prompt, model))
        try:
            parse_fountain(fountain_text)
        except ValueError as error:
            last_error = error
            prompt = build_story_prompt(spec, str(error))
            continue
        return fountain_text
    assert last_error is not None
    raise last_error
