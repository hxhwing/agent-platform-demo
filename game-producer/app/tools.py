"""Function tools for the Game Character Designer studio.

generate_character_portrait — the multimodal "out-image" capability.
It calls Gemini 3.1 Flash Image (Nano Banana 2) to paint a polished character
portrait and hands the raw PNG **bytes** to the end-of-turn callback, which
attaches them inline to the reply. Gemini Enterprise renders inline image bytes,
so there is **no storage bucket** — the user sees the portrait in chat and can
download it; nothing is persisted server-side.

The bytes are passed via an in-process cache (PORTRAIT_CACHE) keyed by a small
token stashed in tool state — so the (managed) session state stays tiny and the
image never has to be JSON-serialized.

IMPORTANT: the image model only resolves in location=global on this project.
"""

import os
import uuid

from google import genai
from google.genai import types
from google.adk.tools import ToolContext

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
# Image + text Gemini-3 models are only served from the global endpoint.
LOCATION = "global"
IMAGE_MODEL = "gemini-3.1-flash-image"  # Nano Banana 2

_genai = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)

# In-process hand-off of the portrait bytes from this tool to the end-of-turn
# callback in agent.py. Keyed by a token we also stash in tool state.
PORTRAIT_CACHE: dict[str, tuple[bytes, str]] = {}


def take_portrait(key: str):
    """Pop (bytes, mime) for `key`, or None. Called by the end-of-turn callback."""
    return PORTRAIT_CACHE.pop(key, None) if key else None


# Nano Banana 2 (gemini-3.1-flash-image) supported values. We fall back to the
# defaults when the caller passes anything else, so a bad value never fails the
# generation (belt-and-suspenders alongside the tool declaration the LLM reads).
_ASPECT_RATIOS = {
    "1:1", "3:2", "2:3", "3:4", "1:4", "4:1", "4:3", "4:5", "5:4",
    "1:8", "8:1", "9:16", "16:9", "21:9", "9:21",
}
_IMAGE_SIZES = {"512", "1K", "2K", "4K"}
_DEFAULT_ASPECT_RATIO = "1:1"
_DEFAULT_IMAGE_SIZE = "1K"


async def generate_character_portrait(
    art_brief: str,
    tool_context: ToolContext,
    aspect_ratio: str = _DEFAULT_ASPECT_RATIO,
    image_size: str = _DEFAULT_IMAGE_SIZE,
) -> dict:
    """Paint a polished game-character portrait from an art brief.

    Use this once you have a concrete visual description of the character
    (species, class, palette, mood, art style, framing). One call = one portrait.
    The image is attached to the final reply automatically — you do not pass it
    anywhere.

    Args:
        art_brief: A rich, self-contained image prompt describing the character's
            appearance, outfit, colour palette, mood and art style. Write it as a
            single descriptive paragraph — do NOT just pass the user's raw words.
        aspect_ratio: Image aspect ratio. MUST be EXACTLY one of: "1:1", "3:2",
            "2:3", "3:4", "1:4", "4:1", "4:3", "4:5", "5:4", "1:8", "8:1", "9:16",
            "16:9", "21:9", "9:21". Pick the closest to what the user asked for
            (landscape/横屏 → "16:9", portrait/竖屏 → "9:16"); default "1:1". Do not
            invent other ratios.
        image_size: Image resolution. MUST be EXACTLY one of: "512", "1K", "2K",
            "4K". Default "1K". Do not pass pixel dimensions or other strings.

    Returns:
        dict with: status, aspect_ratio, image_size (the values actually used).
    """
    # The art creator passes aspect_ratio/image_size via this tool's declaration;
    # we only validate + fall back to defaults so a stray value can't break generation.
    ar = aspect_ratio if aspect_ratio in _ASPECT_RATIOS else _DEFAULT_ASPECT_RATIO
    size = image_size if image_size in _IMAGE_SIZES else _DEFAULT_IMAGE_SIZE

    resp = _genai.models.generate_content(
        model=IMAGE_MODEL,
        contents=art_brief,
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=ar, image_size=size),
        ),
    )

    img_bytes = None
    mime = "image/png"
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []) or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                img_bytes = inline.data
                mime = inline.mime_type or mime
                break
        if img_bytes:
            break

    if not img_bytes:
        return {"status": "error", "detail": "Image model returned no image bytes."}

    # Hand the bytes to the end-of-turn callback via the in-process cache; keep only
    # a tiny token in (managed-)session state. GE renders these bytes inline at the
    # end of the reply — no bucket, no Markdown `![](url)`.
    key = uuid.uuid4().hex
    PORTRAIT_CACHE[key] = (img_bytes, mime)
    state = getattr(tool_context, "state", None)
    if state is not None:
        state["portrait_key"] = key

    return {"status": "ok", "aspect_ratio": ar, "image_size": size}
