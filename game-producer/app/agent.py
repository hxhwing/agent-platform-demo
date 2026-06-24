# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Game Producer — the main multi-agent studio (A2A client + A2UI renderer).

Architecture (one orchestrator + five specialists):

    root_agent / game_producer (orchestrator)
        ├── researcher         Agent + google_search (is this a known game IP?)
        ├── art_creator       Agent + generate_character_portrait (Nano Banana 2)
        ├── story_writer        Agent (name / tagline / backstory / dialogue)
        ├── skill_designer   Agent (HP/ATK/DEF/SPD + skills as JSON)
        └── localization_agent RemoteA2aAgent  ★ A2A call to the external studio
    + render_character_card    (A2UI v0.8 card = the final deliverable)

The four specialists are exposed to the root as AgentTools, so the producer
*orchestrates and aggregates* (clean trace) rather than handing off control.
Deploys to Agent Runtime; observability via BigQuery Agent Analytics + Cloud Trace.
"""

import logging
import os

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import google_search, url_context
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.agent_tool import AgentTool
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, AGENT_CARD_WELL_KNOWN_PATH
from google.genai import types
from google.adk.plugins.bigquery_agent_analytics_plugin import (
    BigQueryAgentAnalyticsPlugin,
    BigQueryLoggerConfig,
)
from google.cloud import bigquery

_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"  # Gemini 3 models only resolve in global
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

from .tools import generate_character_portrait, take_portrait  # noqa: E402  (after env setup)
from .render import (  # noqa: E402
    A2UI_STATE_KEY,
    a2ui_card_parts,
    render_character_card,
)

MODEL = "gemini-3.5-flash"


def _model() -> Gemini:
    return Gemini(model=MODEL, retry_options=types.HttpRetryOptions(attempts=3))


# Where the external localization studio lives. Local A2A server by default;
# point this at the Cloud Run URL after deploying localization-studio.
# adk_a2a serves the card at  {base}/a2a/app/.well-known/agent-card.json
LOCALIZATION_BASE_URL = os.environ.get("LOCALIZATION_AGENT_URL", "http://localhost:8000")
LOCALIZATION_CARD_URL = f"{LOCALIZATION_BASE_URL}/a2a/app{AGENT_CARD_WELL_KNOWN_PATH}"

# ---------------------------------------------------------------- specialists

# Built-in tools must be the ONLY kind of tool on their agent (no function tools
# mixed in), so the researcher is its own agent and the root reaches it as an
# AgentTool. Two BUILT-IN tools, however, can coexist: Gemini 3 supports
# google_search + url_context together, and may use both in a single turn.
researcher = Agent(
    name="researcher",
    model=_model(),
    description="Researches the web (and any user-provided URLs): is this a known game IP, and what art style / story themes / trends define the relevant game or genre.",
    instruction="""
You are the RESEARCHER for a game studio. Given a character brief (and any name
or distinctive details the producer passes you), gather TWO things the other
specialists will build on. Use `google_search` for live web search, and
`url_context` to read any URL the producer hands you.

A. IP CHECK — is this a KNOWN, existing game (or other established IP) character?
   - If YES, return canonical facts the team must stay faithful to: franchise /
     game of origin, canonical character name, role/class and signature abilities
     or stats, signature visual look (so the art matches), 1-2 notable lore points.
   - If NO established match, say exactly: "ORIGINAL — no established IP found;
     create freely." so the team invents from scratch.

B. GENRE & STYLE RESEARCH — for the relevant game/franchise (or, if original, the
   closest genre the brief implies), research what defines it so the art, story and
   skills can match:
   - art style / visual direction (palette, rendering, silhouette conventions)
   - story / lore themes and tone
   - notable characters, mechanics, and current trends / hotspots in that space

C. USER-PROVIDED BACKGROUND (when present) — the producer may pass you URLs and/or
   background text extracted from documents the user supplied as the game's world /
   lore reference. Treat this material as AUTHORITATIVE game background:
   - For each URL, use `url_context` to read it and fold its world/art/lore details
     into your answer.
   - Let this provided material drive the GENRE & STYLE research (B); use
     `google_search` only to FILL GAPS the material doesn't cover (e.g. verifying
     whether the IP really exists, or current trends not in the source). Do not
     contradict the user's background with generic search results.
   - For the IP CHECK (A), facts still win: never assert canon the sources don't
     support.

Add a short "source" note (cite provided URLs/docs and any searches). Be concise
and factual; never invent canon — if unsure about IP, treat it as ORIGINAL but
still give the genre/style research.
""",
    tools=[google_search, url_context],
)

art_creator = Agent(
    name="art_creator",
    model=_model(),
    description="Looks at the concept sketch and paints a polished character portrait.",
    instruction="""
You are the ART CREATOR of a game studio. You receive a concept sketch (image)
and/or a creative brief. Your job:
1. Decide the character's refined visual design: species, class, silhouette,
   outfit, colour palette, lighting, mood, and a consistent ART STYLE.
2. If a world/IP art style is provided in the brief, MATCH it for continuity.
3. Decide the IMAGE FORMAT from the user's request, choosing ONLY from the
   allowed values (never invent others):
   - aspect_ratio — one of: 1:1, 3:2, 2:3, 3:4, 1:4, 4:1, 4:3, 4:5, 5:4, 1:8, 8:1,
     9:16, 16:9, 21:9, 9:21. Use the user's ratio if given; map landscape/横屏 →
     "16:9", portrait/竖屏 → "9:16"; otherwise "1:1".
   - image_size — one of: 512, 1K, 2K, 4K. Use the user's resolution if given;
     otherwise "1K".
4. Call `generate_character_portrait` exactly once. ALWAYS pass THREE arguments
   explicitly: art_brief (a single rich paragraph — front-facing hero shot, clean
   background), aspect_ratio, and image_size (the values from step 3; default
   "1:1" and "1K" when the user didn't specify). Never omit aspect_ratio/image_size.
Return: the portrait public_url, the aspect_ratio & image_size used, plus a
1-sentence note on the art style you locked in (so other characters match later).
""",
    tools=[generate_character_portrait],
)

story_writer = Agent(
    name="story_writer",
    model=_model(),
    description="Writes the character name, tagline, backstory and sample dialogue.",
    instruction="""
You are the STORY WRITER. Given a character concept (and any world/IP context),
produce, tightly:
- name: an evocative character name
- tagline: one punchy line (<= 8 words)
- lore: a 2-3 sentence backstory consistent with the personality and world
- dialogue: ONE signature line the character would say
Keep tone consistent with the requested personality. Output clearly labelled.
""",
)

skill_designer = Agent(
    name="skill_designer",
    model=_model(),
    description="Designs the character's skills (and balanced combat stats).",
    instruction="""
You are the SKILL DESIGNER. Given the character's class/role, design the
character's skills (and balanced supporting stats) as STRICT JSON (no prose),
matching this shape:
{
  "stats": {"HP": 120, "ATK": 85, "DEF": 60, "SPD": 95},
  "skills": [
    {"name": "Skill Name", "desc": "what it does", "cost": 30}
  ]
}
Rules: total of the four stats ~= 360 (glass cannons skew ATK/SPD, tanks skew
HP/DEF). Provide 2-3 skills. Output ONLY the JSON object.
""",
)

# ★ The A2A piece: an EXTERNAL organization reached over the A2A protocol.
localization_agent = RemoteA2aAgent(
    name="localization_agent",
    description="External studio that localizes character content into multiple languages.",
    agent_card=LOCALIZATION_CARD_URL,
)

# ---------------------------------------------------------------------- root

# Output mode. A2UI (rich card via A2A DataParts) is emitted ONLY when
# A2UI_ENABLED=1 — i.e. the A2A / Cloud Run deployment. On Agent Runtime
# (ADK-native) this is unset, so the agent replies with formatted Markdown
# (incl. the portrait image) and intermediate steps stream to GE natively.
A2UI_ENABLED = os.environ.get("A2UI_ENABLED", "").strip().lower() in ("1", "true", "yes")


async def _finalize_turn(callback_context: CallbackContext):
    """End-of-turn callback: persist memory, then (A2A path only) emit the A2UI card.

    Always persists the session into Memory Bank (cross-session world bible / art
    style). On the A2A/Cloud Run path (A2UI_ENABLED) it also emits the stashed
    A2UI card as DataParts; on the ADK-native path it returns None so the model's
    own Markdown reply stands.
    """
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        logging.warning(f"Memory Bank write skipped: {e}")

    if not A2UI_ENABLED:
        # ADK-native (Agent Runtime / Markdown): BUILD the final response as
        # interleaved parts so the portrait renders BETWEEN "World" and "Lore":
        #   [text: title/tagline/World] → [image BYTES] → [text: Lore/Stats/…].
        # (GE renders image bytes, not Markdown `![](url)`.) Reset state per turn.
        md_top = callback_context.state.get("md_top")
        md_bottom = callback_context.state.get("md_bottom")
        key = callback_context.state.get("portrait_key")
        for k in ("md_top", "md_bottom", "portrait_key"):
            callback_context.state[k] = None
        if not md_top and not md_bottom:
            return None  # no card this turn → keep the model's own reply

        parts: list[types.Part] = []
        if md_top:
            parts.append(types.Part(text=md_top))
        portrait = take_portrait(key)  # (bytes, mime) from the portrait tool, this process
        if portrait:
            data, mime = portrait
            parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        if md_bottom:
            parts.append(types.Part(text=md_bottom))
        return types.Content(role="model", parts=parts)

    # Read and CLEAR the stashed card so a turn without render_character_card shows
    # the model's own text instead of re-emitting a stale card.
    messages = callback_context.state.get(A2UI_STATE_KEY)
    key = callback_context.state.get("portrait_key")
    callback_context.state[A2UI_STATE_KEY] = None
    callback_context.state["portrait_key"] = None
    if not messages:
        return None

    parts = [
        types.Part(text="Here's the finished character card."),
        *a2ui_card_parts(messages),
    ]
    # A2UI can't embed raw bytes, so attach the portrait as a separate inline image
    # part alongside the card (shown in chat, not inside the card frame).
    portrait = take_portrait(key)
    if portrait:
        data, mime = portrait
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    return types.Content(role="model", parts=parts)


# Managed Sessions + Memory Bank live in a Vertex AI Agent Engine instance (see
# .env AGENT_ENGINE_ID). PreloadMemoryTool auto-recalls relevant memories into the
# system instruction each turn; _finalize_turn writes them back.
_root_tools = [
    PreloadMemoryTool(),
    AgentTool(agent=researcher),
    AgentTool(agent=art_creator),
    AgentTool(agent=story_writer),
    AgentTool(agent=skill_designer),
    AgentTool(agent=localization_agent),
    render_character_card,
]

_FINAL_STEP_A2UI = """7. render_character_card — assemble EVERYTHING into the final A2UI card. Pass the
   skills/stats JSON from step 5 as two strings: stats_json (the "stats" object) and
   skills_json (the "skills" array); and pass the localization table from step 6 as
   localization_markdown. (The portrait is attached automatically — do not pass it.)

The rendered card is emitted to the user automatically after this turn — do NOT
paste the card JSON or re-dump the full character details as Markdown. Finish with
a SHORT confirmation (2-3 sentences): the character's name, whether it is an
established IP or original, and the art style you locked in for this world."""

_FINAL_STEP_MARKDOWN = """7. render_character_card — assemble EVERYTHING. Pass the skills/stats JSON from step 5
   as stats_json + skills_json strings; pass the localization table from step 6 as
   localization_markdown. (The portrait is attached automatically — do not pass it.)

The finished character sheet — with the portrait image rendered in the MIDDLE
(between World and Lore) — is shown to the user AUTOMATICALLY after this turn. So
do NOT write the sheet, any image, photo, or link yourself (no `![](...)`, no URLs,
no Lore/Stats/Skills text). After calling render_character_card, reply with just
ONE short confirmation line: the character's name, whether it is an established IP
or original, and the art style you locked in for this world."""

_FINAL_STEP = _FINAL_STEP_A2UI if A2UI_ENABLED else _FINAL_STEP_MARKDOWN

root_agent = Agent(
    name="root_agent",
    model=_model(),
    description="AI game studio producer that turns a concept sketch into a full game character.",
    instruction="""
You are the GAME PRODUCER orchestrating a studio. A user gives you a character
concept — usually an uploaded concept SKETCH (image) plus a short brief like
"a mischievous but wise forest elf mage". Turn it into a complete, shippable
game character by coordinating your team.

LANGUAGE: ALWAYS reply to the user in the SAME language as their latest message —
this applies to BOTH your progress narration AND your final confirmation. If the
user writes Chinese, narrate and confirm in Chinese; if English, in English, etc.
The example lines below are written in English only to show the FORMAT — translate
the wording (including the **bold** specialist name) into the user's language.

NARRATE YOUR PROCESS: immediately BEFORE you call each specialist tool, output
ONE progress line so the user can watch the studio work live (these lines stream
to the UI). The UI concatenates streamed text, so format each line EXACTLY like
this — the working specialist's name in **bold**, a space, an emoji, a short
status — and END every line with a BLANK LINE (two newlines) so each step renders
on its own line. Emit each line only ONCE. Use this exact format (translated to
the user's language):

**Researcher** 🔎 Checking whether this is a known game IP…

**Art Director** 🎨 Painting the character portrait…

**Lore Writer** ✍️ Naming the character and writing its lore…

**Balance Designer** ⚖️ Tuning stats & skills…

**Localization Studio (A2A)** 🌐 Localizing the character package…

**Producer** 🧩 Assembling the character card…

Output the line (including its trailing blank line), then call that tool in the
same step. Match the bold name to the specialist you are about to call.

WORKFLOW (call tools in this order; pass each result forward):
1. Memory is auto-recalled for you (PreloadMemoryTool). If a world bible /
   established art style from earlier characters appears in your context, reuse it
   so new characters stay consistent. If nothing is recalled, proceed fresh.
2. researcher — pass the brief (and any character name/details). It uses web
   search to tell you whether this is a KNOWN game character/IP. If it returns
   canonical facts (franchise, name, look, abilities), treat them as ground
   truth and pass them to the next specialists so the character stays faithful.
   If it returns "ORIGINAL — no established IP found", create freely.
   USER-PROVIDED BACKGROUND: if the user gave any reference URLs as the game's
   world/lore, pass those URLs through verbatim — the researcher reads them with
   url_context. If the user uploaded DOCUMENTS as background, you can see them
   directly; extract the relevant world / art-style / lore points into a short
   text summary and pass THAT to the researcher (it only receives text). Tell the
   researcher to treat this provided material as authoritative game background.

   RESEARCH FINDINGS = what the researcher returns: (a) IP canon (or "ORIGINAL"),
   (b) genre & style research (art direction, lore themes, trends), and (c) any
   user-provided background (URL/document) it folded in. The specialists DO NOT
   share context with each other or with the researcher — they only see what you
   pass them. So you MUST forward the RELEVANT research findings as input to EACH
   of steps 3, 4 and 5 below; never assume a specialist already knows them.
3. art_creator — pass the sketch + brief + the research findings RELEVANT to art:
   canonical look (if any), the genre/style art direction, and any user-provided
   world/art background (plus any recalled art style), AND any image aspect ratio /
   resolution the user asked for (e.g. "4:3", "横屏", "2K"). Get the portrait
   public_url and art-style note.
4. story_writer — pass the research findings RELEVANT to story: IP canon (stay
   faithful to it if found; otherwise invent), the genre/lore themes & tone, and
   any user-provided world background. Get name, tagline, lore, signature dialogue.
5. skill_designer — pass the research findings RELEVANT to mechanics: canonical
   abilities/stats (anchor to them if research provided them) and the genre's
   typical mechanics. Get the stats + skills JSON.
6. localization_agent — send the character package (name, tagline, lore, the
   signature dialogue) in ONE call. This is an EXTERNAL studio reached over A2A;
   one call returns ALL languages as a table. Call it EXACTLY ONCE — never call it
   a second time, even to adjust; use whatever the single call returns.
""" + _FINAL_STEP + """

Be efficient: one call per specialist. Never invent a portrait URL — only use
the one returned by art_creator.
""",
    tools=_root_tools,
    after_agent_callback=_finalize_turn,
)

# Initialize BigQuery Analytics
_plugins = []
_project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
_dataset_id = os.environ.get("BQ_ANALYTICS_DATASET_ID", "adk_agent_analytics")
_location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

if _project_id:
    try:
        bq = bigquery.Client(project=_project_id)
        bq.create_dataset(f"{_project_id}.{_dataset_id}", exists_ok=True)

        _plugins.append(
            BigQueryAgentAnalyticsPlugin(
                project_id=_project_id,
                dataset_id=_dataset_id,
                location=_location,
                config=BigQueryLoggerConfig(
                    gcs_bucket_name=os.environ.get("BQ_ANALYTICS_GCS_BUCKET"),
                    connection_id=os.environ.get("BQ_ANALYTICS_CONNECTION_ID"),
                ),
            )
        )
    except Exception as e:
        logging.warning(f"Failed to initialize BigQuery Analytics: {e}")

app = App(
    root_agent=root_agent,
    name="app",
    plugins=_plugins,
)
