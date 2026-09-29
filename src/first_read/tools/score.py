"""Generate instrumental scene music with Vertex Lyria."""

import base64
import json
import wave
from pathlib import Path
from urllib.request import Request, urlopen

from first_read.config import Settings
from first_read.gcs import upload_bytes
from first_read.models import OUTPUT_ROOT, SCORE_FILENAME, SCORE_MODEL
from first_read.schema import Asset, Breakdown, Scene, ScoreOutput
from first_read.store import insert_asset


class VertexLyriaClient:
    """Small Vertex predict adapter; replaceable by a fake in pipeline tests."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def predict(self, *, model: str, prompt: str) -> dict:
        import google.auth
        from google.auth.transport.requests import Request as AuthRequest

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(AuthRequest())
        location = self.settings.google_cloud_location
        url = (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/"
            f"{self.settings.google_cloud_project}/locations/{location}/"
            f"publishers/google/models/{model}:predict"
        )
        body = json.dumps(
            {"instances": [{"prompt": prompt}], "parameters": {"sample_count": 1}}
        ).encode()
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=120) as response:
            return json.load(response)


def _client() -> VertexLyriaClient:
    return VertexLyriaClient(Settings.load())


def _prompt(scene: Scene, breakdown: Breakdown) -> str:
    return (
        "Instrumental cinematic underscore only. No vocals, singing, spoken words, "
        "chanting, or sampled speech. Leave clear space in the midrange for dialogue. "
        "Begin gently, maintain a restrained pulse, and resolve softly. "
        f"Musical direction: {breakdown.tone}. "
        f"Setting: {scene.location}, {scene.time_of_day}. "
        f"Dramatic movement: {breakdown.staging}."
    )


def _audio_bytes(response: dict | bytes) -> bytes:
    if isinstance(response, bytes):
        return response
    predictions = response.get("predictions") or []
    if not predictions:
        raise RuntimeError("Lyria returned no predictions")
    encoded = predictions[0].get("audioContent") or predictions[0].get("audio_content")
    if not encoded:
        raise RuntimeError("Lyria returned no audio")
    return base64.b64decode(encoded)


def _generate_score(
    scene: Scene, breakdown: Breakdown, run_id: str, script_title: str
) -> ScoreOutput:
    prompt = _prompt(scene, breakdown)
    data = _audio_bytes(_client().predict(model=SCORE_MODEL, prompt=prompt))
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    local_path = Path(run_dir / SCORE_FILENAME)
    local_path.write_bytes(data)
    with wave.open(str(local_path), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    if duration <= 0:
        raise RuntimeError("Lyria returned empty audio")
    uploaded = upload_bytes(data, f"runs/{run_id}/{SCORE_FILENAME}", "audio/wav")
    insert_asset(
        Asset(
            run_id=run_id,
            asset_type="score",
            script_title=script_title,
            scene_slug=scene.slugline,
            int_ext=scene.int_ext,
            time_of_day=scene.time_of_day,
            location=scene.location,
            characters=scene.characters,
            tone=breakdown.tone,
            prompt=prompt,
            model_id=SCORE_MODEL,
            gcs_uri=uploaded.gcs_uri,
            duration_seconds=duration,
        )
    )
    return ScoreOutput(
        url=uploaded.signed_url,
        gcs_uri=uploaded.gcs_uri,
        local_path=str(local_path),
        duration_seconds=duration,
    )


generate_score = _generate_score
