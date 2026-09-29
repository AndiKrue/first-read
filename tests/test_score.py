"""Score generation and ffmpeg mix construction without Vertex or media IO."""

import base64
import io
import wave
from pathlib import Path

from first_read.gcs import UploadedObject
from first_read.parse import parse_fountain
from first_read.schema import Breakdown
from first_read.tools import score
from first_read.tools.assemble import _ffmpeg_command, _score_mix_filter


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000)
    return output.getvalue()


def test_lyria_score_uses_tone_and_uploads_audio(monkeypatch, tmp_path):
    class FakeLyria:
        def predict(self, *, model, prompt):
            assert model == score.SCORE_MODEL
            assert "quiet dread" in prompt
            assert "No vocals" in prompt
            return {
                "predictions": [{"audioContent": base64.b64encode(_wav()).decode()}]
            }

    uploads = []
    monkeypatch.setattr(score, "_client", lambda: FakeLyria())
    monkeypatch.setattr(score, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(score, "insert_asset", lambda asset: None)

    def upload(data, name, content_type):
        uploads.append((name, content_type, data))
        return UploadedObject(gcs_uri=f"gs://bucket/{name}", signed_url="https://score")

    monkeypatch.setattr(score, "upload_bytes", upload)
    scene = parse_fountain("INT. CAFE - NIGHT\n\nJUNE\nHello.")
    breakdown = Breakdown(
        beats=[
            {
                "beat_id": str(i),
                "description": "A beat",
                "shot_type": "WIDE",
                "characters_present": [],
            }
            for i in range(4)
        ],
        characters=[],
        location_description="Cafe",
        staging="A quiet conversation",
        tone="quiet dread",
    )
    output = score.generate_score(scene, breakdown, "run-1", "Scene")
    assert output.gcs_uri == "gs://bucket/runs/run-1/score.wav"
    assert output.duration_seconds == 1.0
    assert uploads[0][:2] == ("runs/run-1/score.wav", "audio/wav")


def test_mix_command_includes_score_input_and_ducking():
    command = _ffmpeg_command(
        Path("panels.txt"),
        "read.wav",
        Path("animatic.mp4"),
        15.0,
        pad_audio=False,
        score_path="score.wav",
    )
    assert command[command.index("-stream_loop") + 3] == "score.wav"
    assert "[mix]" in command
    assert "[2:a]" in command[command.index("-filter_complex") + 1]
    assert "sidechaincompress" in _score_mix_filter(15.0)
