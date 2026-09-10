"""Validated data exchanged by every pipeline stage."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StorySpec(BaseModel):
    genre: str
    setting: str = ""
    characters: list[str] = Field(default_factory=list)
    situation: str = ""
    tone: str = ""


class StoryResponse(BaseModel):
    fountain_text: str
    title: str


class ActionElement(BaseModel):
    type: Literal["action"] = "action"
    text: str


class DialogueElement(BaseModel):
    type: Literal["dialogue"] = "dialogue"
    character: str
    text: str
    parenthetical: str | None = None


SceneElement = Annotated[ActionElement | DialogueElement, Field(discriminator="type")]


class Scene(BaseModel):
    slugline: str
    int_ext: Literal["INT", "EXT"]
    location: str
    time_of_day: str
    elements: list[SceneElement]
    characters: list[str]


class Character(BaseModel):
    name: str
    visual_description: str
    wardrobe: str = ""
    voice_name: str = ""


class Beat(BaseModel):
    beat_id: str
    description: str
    shot_type: Literal["WIDE", "MEDIUM", "CLOSE", "OTS"]
    characters_present: list[str]

    @field_validator("beat_id", mode="before")
    @classmethod
    def _coerce_beat_id(cls, value: object) -> str:
        """Models return beat ids as ints; the pipeline keys on strings."""
        return str(value)

    @field_validator("shot_type", mode="before")
    @classmethod
    def _normalize_shot_type(cls, value: object) -> object:
        """Normalize common model spellings without weakening the strict enum."""
        if not isinstance(value, str):
            return value
        normalized = value.strip().upper().replace("-", " ").replace("_", " ")
        aliases = {
            "CLOSE UP": "CLOSE",
            "CLOSEUP": "CLOSE",
            "OVER THE SHOULDER": "OTS",
        }
        return aliases.get(normalized, normalized)


class Breakdown(BaseModel):
    beats: list[Beat]
    characters: list[Character]
    location_description: str
    staging: str
    tone: str

    @field_validator("beats")
    @classmethod
    def validate_beat_count(cls, value: list[Beat]) -> list[Beat]:
        if not 4 <= len(value) <= 6:
            raise ValueError("breakdown must contain between 4 and 6 beats")
        return value

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, value: str) -> str:
        if not 2 <= len(value.split()) <= 3:
            raise ValueError("tone must contain two or three words")
        return value


class Asset(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    created_at: datetime | None = None
    asset_type: Literal["sheet", "panel", "audio", "animatic"]
    script_title: str
    scene_slug: str
    beat_id: str = ""
    shot_type: str = ""
    int_ext: str
    time_of_day: str
    location: str
    characters: list[str]
    tone: str
    prompt: str
    model_id: str
    gcs_uri: str
    duration_seconds: float = 0.0


class PanelOutput(BaseModel):
    beat_id: str
    url: str
    gcs_uri: str
    local_path: str


class AudioOutput(BaseModel):
    url: str
    gcs_uri: str
    local_path: str
    duration_seconds: float


class AnimaticOutput(BaseModel):
    url: str
    gcs_uri: str
    local_path: str


class RunRecord(BaseModel):
    """Durable pipeline state; media locations remain stable ``gs://`` URIs."""

    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    script_title: str = ""
    scene_slug: str = ""
    int_ext: str = ""
    time_of_day: str = ""
    tone: str = ""
    characters: list[str] = Field(default_factory=list)
    stage: str = "queued"
    error: str = ""
    breakdown_json: str = ""
    panel_uris: list[str] = Field(default_factory=list)
    audio_uri: str = ""
    animatic_uri: str = ""
    duration_seconds: float = 0.0
