"""Cast stable voices and perform the whole scene in one TTS request."""

import base64
import wave

from google import genai
from google.genai import types

from first_read.config import Settings
from first_read.gcs import upload_bytes
from first_read.models import (
    AUDIO_FILENAME,
    OUTPUT_ROOT,
    TABLE_READ_MODEL,
    VOICE_ROSTER,
)
from first_read.schema import Asset, AudioOutput, Breakdown, DialogueElement, Scene
from first_read.store import insert_asset, upsert_character


def _client() -> genai.Client:
    settings = Settings.load()
    return genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )


def _cast(breakdown: Breakdown, script_title: str) -> dict[str, str]:
    used = {
        character.voice_name
        for character in breakdown.characters
        if character.voice_name
    }
    available = [voice for voice in VOICE_ROSTER if voice not in used]
    new_characters = sorted(
        (character for character in breakdown.characters if not character.voice_name),
        key=lambda character: character.name,
    )
    if len(new_characters) > len(available):
        raise RuntimeError("Scene has more new characters than the fixed voice roster")
    for character, voice in zip(new_characters, available):
        character.voice_name = voice
    for character in breakdown.characters:
        upsert_character(character, script_title)
    return {character.name: character.voice_name for character in breakdown.characters}


def generate_table_read(
    scene: Scene, breakdown: Breakdown, run_id: str, script_title: str
) -> AudioOutput:
    casting = _cast(breakdown, script_title)
    dialogue = [
        element for element in scene.elements if isinstance(element, DialogueElement)
    ]
    if not dialogue:
        raise ValueError("Table read requires at least one dialogue element")
    directions = [
        f"{element.character}: {element.parenthetical}"
        for element in dialogue
        if element.parenthetical
    ]
    style = f"Perform this scene with a {breakdown.tone} tone"
    if directions:
        style += "; observe these line directions: " + "; ".join(directions)
    transcript = (
        style
        + ".\n"
        + "\n".join(f"{element.character}: {element.text}" for element in dialogue)
    )
    speaker_configs = [
        types.SpeakerVoiceConfig(
            speaker=name,
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=casting[name]
                )
            ),
        )
        for name in scene.characters
    ]
    client = _client()
    response = client.models.generate_content(
        model=TABLE_READ_MODEL,
        contents=transcript,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                    speaker_voice_configs=speaker_configs
                )
            ),
        ),
    )
    data = response.candidates[0].content.parts[0].inline_data.data
    pcm = base64.b64decode(data) if isinstance(data, str) else data
    if not pcm:
        raise RuntimeError("Table-read model returned empty audio")
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    local_path = run_dir / AUDIO_FILENAME
    with wave.open(str(local_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(pcm)
    with wave.open(str(local_path), "rb") as wav_file:
        duration = wav_file.getnframes() / wav_file.getframerate()
    wav_bytes = local_path.read_bytes()
    uploaded = upload_bytes(wav_bytes, f"runs/{run_id}/{AUDIO_FILENAME}", "audio/wav")
    insert_asset(
        Asset(
            run_id=run_id,
            asset_type="audio",
            script_title=script_title,
            scene_slug=scene.slugline,
            int_ext=scene.int_ext,
            time_of_day=scene.time_of_day,
            location=scene.location,
            characters=scene.characters,
            tone=breakdown.tone,
            prompt=transcript,
            model_id=TABLE_READ_MODEL,
            gcs_uri=uploaded.gcs_uri,
            duration_seconds=duration,
        )
    )
    return AudioOutput(
        url=uploaded.signed_url,
        gcs_uri=uploaded.gcs_uri,
        local_path=str(local_path),
        duration_seconds=duration,
    )


def tableread_tool(
    scene_json: str, breakdown_json: str, run_id: str, script_title: str
) -> dict:
    """ADK-facing table-read tool."""
    return generate_table_read(
        Scene.model_validate_json(scene_json),
        Breakdown.model_validate_json(breakdown_json),
        run_id,
        script_title,
    ).model_dump()
