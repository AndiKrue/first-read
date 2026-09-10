"""SMOKE 03 - storyboard panel, then a second panel conditioned on the first.

Reference-image conditioning is the mechanism that keeps a character looking
like the same person across panels. If attaching the previous panel does not
work, the whole storyboard stage needs redesigning - know now.
"""
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(
    vertexai=True,
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    location=os.environ["GOOGLE_CLOUD_LOCATION"],
)

STYLE = ("graphite storyboard panel, black and white, clean line art, "
         "no text, no lettering, 16:9")

CHAR = ("JUNE: woman in her forties, sharp features, dark hair pulled back, "
        "heavy wet wool coat")

def gen(prompt, ref_bytes=None):
    parts = [prompt]
    if ref_bytes:
        parts.append(types.Part.from_bytes(data=ref_bytes, mime_type="image/png"))
    resp = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=parts,
        config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
    )
    for p in resp.candidates[0].content.parts:
        if getattr(p, "inline_data", None) and p.inline_data.data:
            return p.inline_data.data
    raise RuntimeError("no image part in response")

p1 = gen(f"WIDE. Interior transport cafe at night, rain on the window. {CHAR} "
         f"sits alone with a folded map. {STYLE}")
open("smoke_panel_1.png", "wb").write(p1)
print("panel 1 OK")

p2 = gen(f"CLOSE. Same character, same location, she folds the map and puts it "
         f"in her coat. Match the attached reference exactly for character and "
         f"location. {CHAR} {STYLE}", ref_bytes=p1)
open("smoke_panel_2.png", "wb").write(p2)
print("panel 2 OK - open both and check she is the same person")
