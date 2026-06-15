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
"""Agent Runtime entrypoint — ADK-native (Option 1).

Deploys the agent to Agent Runtime as a plain `AdkApp` (NOT A2A). This is the
"complete Agent Platform" demo path:

- **Managed Sessions + Memory Bank**, auto-wired to this deployment's OWN Agent
  Engine: Agent Runtime injects `GOOGLE_CLOUD_AGENT_ENGINE_ID` and `AdkApp`
  builds VertexAiSessionService + VertexAiMemoryBankService against it (the
  regional engine; the Gemini model still uses location=global). So we pass NO
  memory_service_builder. The end-user is forwarded by GE on this ADK-native
  path, so memory is keyed per real user.
- **Intermediate steps stream to GE natively** (function_call/function_response,
  sub-agent calls) via the reasoning-engine streamQuery API — no A2A narration.
- **Markdown output** (no A2UI): `A2UI_ENABLED` is unset here, so the agent
  replies with a formatted Markdown character sheet (see app/agent.py).

The A2A + A2UI variant lives in app/fast_api_app.py (Cloud Run, Option 2).
Select via `agents-cli-manifest.yaml` `deployment_target` + `is_a2a`.
"""
import logging
import os
from typing import Any

import vertexai
from dotenv import load_dotenv
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as google_cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp

from app.agent import app as adk_app
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback

# Load environment variables from .env file at runtime
load_dotenv()


class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        """Initialize the agent engine app with logging and telemetry."""
        vertexai.init()
        setup_telemetry()
        super().set_up()
        logging.basicConfig(level=logging.INFO)
        logging_client = google_cloud_logging.Client()
        self.logger = logging_client.logger(__name__)
        if gemini_location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = gemini_location

    def register_feedback(self, feedback: dict[str, Any]) -> None:
        """Collect and log feedback."""
        feedback_obj = Feedback.model_validate(feedback)
        self.logger.log_struct(feedback_obj.model_dump(), severity="INFO")

    def register_operations(self) -> dict[str, list[str]]:
        """Registers the operations of the Agent."""
        operations = super().register_operations()
        operations[""] = [*operations.get("", []), "register_feedback"]
        return operations


gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

# Sessions + Memory Bank are auto-wired to this deployment's own engine by AdkApp
# (platform injects GOOGLE_CLOUD_AGENT_ENGINE_ID). Do NOT pass memory_service_builder.
agent_runtime = AgentEngineApp(
    app=adk_app,
    artifact_service_builder=lambda: (
        GcsArtifactService(bucket_name=logs_bucket_name)
        if logs_bucket_name
        else InMemoryArtifactService()
    ),
)
