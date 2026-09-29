"""Mux generated panels and measured table-read audio with ffmpeg."""

import subprocess
from pathlib import Path

from first_read.gcs import upload_bytes
from first_read.models import (
    ANIMATIC_FILENAME,
    MIN_PANEL_SECONDS,
    OUTPUT_ROOT,
    SCORED_READ_FILENAME,
)
from first_read.schema import (
    AnimaticOutput,
    Asset,
    AudioOutput,
    Breakdown,
    PanelOutput,
    Scene,
    ScoreOutput,
)
from first_read.store import insert_asset


def _concat_path(path: str) -> str:
    return str(Path(path).resolve()).replace("'", "'\\''")


def _panel_timing(audio_seconds: float, panel_count: int) -> tuple[float, float]:
    """Return the uniform panel hold and total video duration."""
    if panel_count <= 0:
        raise ValueError("Cannot calculate timing without panels")
    seconds_per_panel = round(max(MIN_PANEL_SECONDS, audio_seconds / panel_count), 6)
    video_duration = max(audio_seconds, seconds_per_panel * panel_count)
    return seconds_per_panel, video_duration


def _ffmpeg_command(
    concat_path: Path,
    audio_path: str,
    output_path: Path,
    video_duration: float,
    *,
    pad_audio: bool,
    score_path: str | None = None,
) -> list[str]:
    """Build the mux command, padding short audio to the explicit video length."""
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        audio_path,
    ]
    if score_path:
        command.extend(["-stream_loop", "-1", "-i", score_path])
        command.extend(["-filter_complex", _score_mix_filter(video_duration)])
    command.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "[mix]" if score_path else "1:a:0",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            "-c:a",
            "aac",
        ]
    )
    if pad_audio and not score_path:
        command.extend(["-af", "apad"])
    command.extend(["-t", f"{video_duration:.6f}", str(output_path)])
    return command


def _score_mix_filter(
    duration: float, *, voice_index: int = 1, score_index: int = 2
) -> str:
    """Duck instrumental music under dialogue, then fade exposed edges."""
    fade_out = max(0.0, duration - 1.5)
    return (
        f"[{voice_index}:a]asplit=2[sidechain][voicein];"
        f"[{score_index}:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
        "volume=0.28,afade=t=in:st=0:d=1,"
        f"afade=t=out:st={fade_out:.6f}:d=1.5[music];"
        "[music][sidechain]sidechaincompress=threshold=0.018:ratio=8:"
        "attack=40:release=450[ducked];"
        f"[voicein]apad=whole_dur={duration:.6f}[voice];"
        "[voice][ducked]amix=inputs=2:duration=longest:normalize=0,"
        f"atrim=duration={duration:.6f}[mix]"
    )


def mix_scored_read(audio: AudioOutput, score: ScoreOutput, run_id: str) -> str:
    """Upload the mixed read for playback while the animatic is being made."""
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / SCORED_READ_FILENAME
    command = [
        "ffmpeg",
        "-y",
        "-i",
        audio.local_path,
        "-stream_loop",
        "-1",
        "-i",
        score.local_path,
        "-filter_complex",
        _score_mix_filter(audio.duration_seconds, voice_index=0, score_index=1),
        "-map",
        "[mix]",
        "-t",
        f"{audio.duration_seconds:.6f}",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"ffmpeg scored read failed ({result.returncode}): {result.stderr.strip()}"
        )
    return upload_bytes(
        output_path.read_bytes(), f"runs/{run_id}/{SCORED_READ_FILENAME}", "audio/wav"
    ).gcs_uri


def assemble_animatic(
    scene: Scene,
    breakdown: Breakdown,
    panels: list[PanelOutput],
    audio: AudioOutput,
    run_id: str,
    script_title: str,
    score: ScoreOutput | None = None,
    output_name: str = ANIMATIC_FILENAME,
) -> AnimaticOutput:
    if not panels:
        raise ValueError("Cannot assemble an animatic without panels")
    seconds_per_panel, video_duration = _panel_timing(
        audio.duration_seconds, len(panels)
    )
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    concat_path = run_dir / "panels.txt"
    lines = []
    for panel in panels:
        lines.extend(
            [
                f"file '{_concat_path(panel.local_path)}'",
                f"duration {seconds_per_panel:.6f}",
            ]
        )
    lines.append(f"file '{_concat_path(panels[-1].local_path)}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output_path = run_dir / output_name
    command = _ffmpeg_command(
        concat_path,
        audio.local_path,
        output_path,
        video_duration,
        pad_audio=video_duration > audio.duration_seconds,
        score_path=score.local_path if score else None,
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"ffmpeg failed ({result.returncode}): {result.stderr.strip()}"
        )
    video_bytes = output_path.read_bytes()
    uploaded = upload_bytes(video_bytes, f"runs/{run_id}/{output_name}", "video/mp4")
    insert_asset(
        Asset(
            run_id=run_id,
            asset_type="animatic",
            script_title=script_title,
            scene_slug=scene.slugline,
            int_ext=scene.int_ext,
            time_of_day=scene.time_of_day,
            location=scene.location,
            characters=scene.characters,
            tone=breakdown.tone,
            prompt=(
                "Uniform-duration panel assembly with a 2.5-second minimum hold, "
                "synced to the measured table read"
            ),
            model_id="ffmpeg",
            gcs_uri=uploaded.gcs_uri,
            duration_seconds=video_duration,
        )
    )
    return AnimaticOutput(
        url=uploaded.signed_url,
        gcs_uri=uploaded.gcs_uri,
        local_path=str(output_path),
    )


def assemble_tool(
    scene_json: str,
    breakdown_json: str,
    panels_json: str,
    audio_json: str,
    run_id: str,
    script_title: str,
) -> dict:
    """ADK-facing assembly tool."""
    import json

    return assemble_animatic(
        Scene.model_validate_json(scene_json),
        Breakdown.model_validate_json(breakdown_json),
        [PanelOutput.model_validate(item) for item in json.loads(panels_json)],
        AudioOutput.model_validate_json(audio_json),
        run_id,
        script_title,
    ).model_dump()
