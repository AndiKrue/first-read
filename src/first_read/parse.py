"""A deliberately small, single-scene Fountain parser."""

import re
from pathlib import Path

from first_read.schema import ActionElement, DialogueElement, Scene

_SLUGLINE = re.compile(r"^(INT|EXT)\.\s*(.+?)\s+-\s+(.+)$", re.IGNORECASE)


def parse_fountain(source: str | bytes | Path) -> Scene:
    if isinstance(source, Path):
        if source.suffix.lower() == ".pdf":
            raise NotImplementedError(
                "PDF input is out of scope; provide Fountain text"
            )
        text = source.read_text(encoding="utf-8")
    elif isinstance(source, bytes):
        if source.startswith(b"%PDF"):
            raise NotImplementedError(
                "PDF input is out of scope; provide Fountain text"
            )
        text = source.decode("utf-8")
    else:
        text = source

    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    first = next((index for index, line in enumerate(lines) if line), None)
    if first is None:
        raise ValueError("Fountain scene is empty")
    match = _SLUGLINE.match(lines[first])
    if not match:
        raise ValueError("Scene must begin with an INT. or EXT. slugline")

    elements = []
    characters: list[str] = []
    index = first + 1
    while index < len(lines):
        line = lines[index]
        if not line:
            index += 1
            continue
        if _SLUGLINE.match(line):
            raise ValueError("Exactly one scene is supported per request")
        next_nonblank = next(
            (lines[pos] for pos in range(index + 1, len(lines)) if lines[pos]), ""
        )
        is_cue = line == line.upper() and bool(
            re.fullmatch(r"[A-Z][A-Z0-9 ._'-]*", line)
        )
        if is_cue and next_nonblank:
            character = re.sub(r"\s*\([^)]*\)\s*$", "", line).strip()
            if character not in characters:
                characters.append(character)
            index += 1
            while index < len(lines) and not lines[index]:
                index += 1
            parenthetical = None
            if (
                index < len(lines)
                and lines[index].startswith("(")
                and lines[index].endswith(")")
            ):
                parenthetical = lines[index][1:-1].strip()
                index += 1
            dialogue = []
            while index < len(lines) and lines[index]:
                dialogue.append(lines[index])
                index += 1
            if not dialogue:
                raise ValueError(f"Character cue {character!r} has no dialogue")
            elements.append(
                DialogueElement(
                    character=character,
                    text=" ".join(dialogue),
                    parenthetical=parenthetical,
                )
            )
        else:
            action = [line]
            index += 1
            while index < len(lines) and lines[index]:
                action.append(lines[index])
                index += 1
            elements.append(ActionElement(text=" ".join(action)))

    characters.sort(key=lambda name: text.find(name))
    return Scene(
        slugline=lines[first].upper(),
        int_ext=match.group(1).upper(),
        location=match.group(2).strip().upper(),
        time_of_day=match.group(3).strip().upper(),
        elements=elements,
        characters=characters,
    )
