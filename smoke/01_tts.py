"""SMOKE 01 - multi-speaker TTS on Vertex.

The decisive question: does gemini-2.5-pro-tts do multi_speaker on Vertex in
this region? If it fails, the fallback is per-character single-speaker calls
concatenated with ffmpeg - a different tableread.py. Know before firing the WO.
"""
import os, wave
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(
    vertexai=True,
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    location=os.environ["GOOGLE_CLOUD_LOCATION"],
)

SCRIPT = """TTS the following scene read, performed with tension and fatigue:
JUNE: I said I would try for six.
MARCO: The yard is empty. Everything is gone.
JUNE: Then we were right."""

resp = client.models.generate_content(
    model="gemini-2.5-pro-tts",
    contents=SCRIPT,
    config=types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=[
                    types.SpeakerVoiceConfig(
                        speaker="JUNE",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
                        ),
                    ),
                    types.SpeakerVoiceConfig(
                        speaker="MARCO",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
                        ),
                    ),
                ]
            )
        ),
    ),
)

data = resp.candidates[0].content.parts[0].inline_data.data
with wave.open("smoke_tts.wav", "wb") as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(24000)
    f.writeframes(data)

print(f"OK - wrote smoke_tts.wav, {len(data)} bytes. Listen to it.")
