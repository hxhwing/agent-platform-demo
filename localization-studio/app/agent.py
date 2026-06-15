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

"""Localization Studio — an EXTERNAL agent exposed over the A2A protocol.

In the demo narrative this is a *separate organization* (a third-party game
localization studio). The Game Producer never sees this code — it only talks to
it over A2A via the published Agent Card. This is the demo's "one agent
implemented with A2A" requirement.

The A2A server wiring lives in app/fast_api_app.py (scaffolded by
`agents-cli create --agent adk_a2a`); here we only define the agent.
"""

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

import os
import google.auth

_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"  # Gemini 3 models only resolve in global
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

MODEL = "gemini-3.5-flash"

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=(
        "External game-localization studio. Translates and culturally adapts "
        "game character content (name, tagline, lore, sample dialogue) into "
        "multiple target languages while preserving the character's personality."
    ),
    instruction="""
You are an award-winning GAME LOCALIZATION studio. You receive a game character
package (name, tagline, lore, one or two lines of sample dialogue) and produce
high-quality localized versions.

Rules:
- If the caller does not specify target languages, default to:
  Japanese (ja), Spanish (es), and Simplified Chinese (zh-CN).
- Do NOT translate literally. Adapt idioms, honorifics, and tone so the
  character feels native in each language (this is "transcreation").
- Preserve the character's personality, fantasy register, and any proper nouns
  that should stay untranslated (mark them).
- Return a compact Markdown table with columns:
  Language | Localized Name | Tagline | Sample Dialogue
- End with one short note on the biggest cultural adaptation you made.

Keep it tight and production-ready. You are a backend service for another agent.
""",
)

app = App(
    root_agent=root_agent,
    name="app",
)
