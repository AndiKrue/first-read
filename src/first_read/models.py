"""All model and media constants used by FIRST READ."""

import os
from pathlib import Path

REASONING_MODEL = "gemini-3-pro-preview"
REASONING_FALLBACK_MODEL = "gemini-2.5-pro"
STORYBOARD_MODEL = "gemini-2.5-flash-image"
TABLE_READ_MODEL = "gemini-2.5-pro-tts"

RANDOMIZE_POOLS = {
    "genre": [
        "noir",
        "kitchen-sink drama",
        "quiet comedy",
        "workplace drama",
        "romantic comedy",
        "family drama",
        "gentle mystery",
        "social realism",
        "chamber drama",
        "low-key thriller",
        "science fiction",
        "bittersweet comedy",
    ],
    "setting": [
        "a nearly empty night bus",
        "a hospital corridor before visiting hours",
        "a locksmith's shop at closing time",
        "the back room of a corner bakery",
        "a laundromat during a power flicker",
        "a council office waiting area",
        "a small-town train platform in the rain",
        "a school kitchen after lunch",
        "a cramped apartment hallway",
        "a community swimming pool reception desk",
        "a repair garage just after sunrise",
        "a grocery store stockroom",
    ],
    "situation": [
        "someone has brought back a key that opens the wrong door",
        "two siblings disagree about who should call their father",
        "a regular customer quietly admits they cannot pay",
        "a night-shift worker recognizes a passenger who vanished years ago",
        "one person must confess they lost something entrusted to them",
        "a colleague is leaving today and has told nobody why",
        "two strangers discover they are waiting for the same apology",
        "a tenant hears that the building has been sold",
        "someone arrives to collect a cake ordered under a false name",
        "a missed train forces an overdue conversation",
        "an employee finds a resignation letter meant for someone else",
        "a small favor reveals a much larger secret",
    ],
}

VOICE_ROSTER = (
    "Kore",
    "Puck",
    "Charon",
    "Aoede",
    "Fenrir",
    "Leda",
    "Orus",
    "Zephyr",
)

CHARACTER_SHEET_STYLE = (
    "monochrome greyscale graphite pencil drawing, strictly black white and grey "
    "only, absolutely no colour of any kind, clean line art, single figure, "
    "neutral standing pose, plain empty background, no text, no lettering, "
    "no signature, no border"
)
PANEL_STYLE = (
    "monochrome greyscale graphite pencil drawing, strictly black white and grey "
    "only, absolutely no colour of any kind, clean cinematic line art, consistent "
    "faces and wardrobe, widescreen 16:9 composition, no text, no lettering, "
    "no signage text, no signature, no watermark, no border"
)
PANEL_CONCURRENCY = 1
MIN_PANEL_SECONDS = 2.5

OUTPUT_ROOT = Path(os.environ.get("FIRST_READ_OUTPUT_DIR", "/tmp/first-read"))
PANEL_FILENAME = "panel_{index:02d}.png"
AUDIO_FILENAME = "tableread.wav"
ANIMATIC_FILENAME = "animatic.mp4"
