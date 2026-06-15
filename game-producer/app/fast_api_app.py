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
"""Cloud Run entrypoint (Option 2) — A2A (for GE) + the ADK dev UI.

Serves BOTH on one Cloud Run service:
- **ADK dev UI** at ``/dev-ui/`` (+ its API) via ``get_fast_api_app(web=True)`` —
  interactive playground for testing/demo.
- **A2A** at ``/a2a/app`` with **streaming** + the A2UI extension — what Gemini
  Enterprise registers and calls (so GE shows the streamed build activity + card).

Both share the same agent (app/agent.py) and the same managed Sessions + Memory
Bank engine (`AGENT_ENGINE_ID`). The service is private; reach the dev UI with an
authenticated tunnel: `gcloud run services proxy game-producer --region us-central1`.

Pick this file vs Agent Runtime (`agent_runtime_app.py`) via the manifest
`deployment_target` (`cloud_run` → this, through the Dockerfile).
"""
import asyncio
import os

import google.auth
import nest_asyncio
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentExtension
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, EXTENDED_AGENT_CARD_PATH
from dotenv import load_dotenv
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.memory import VertexAiMemoryBankService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.cloud import logging as google_cloud_logging

from app.agent import app as adk_app
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback

load_dotenv()
setup_telemetry()
_, project_id = google.auth.default()
logging_client = google_cloud_logging.Client()
logger = logging_client.logger(__name__)

A2UI_EXTENSION = AgentExtension(
    uri="https://a2ui.org/a2a-extension/a2ui/v0.8",
    description="Ability to render A2UI",
    required=False,
    params={
        "supportedCatalogIds": [
            "https://a2ui.org/specification/v0_8/standard_catalog_definition.json"
        ]
    },
)


def _engine_id() -> str | None:
    return os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID") or os.environ.get("AGENT_ENGINE_ID")


def _engine_location() -> str:
    return os.environ.get("AGENT_ENGINE_LOCATION", "us-central1")


def _engine_uri() -> str | None:
    """agentengine:// URI for the dev-UI runner (full path so ADK reads the region
    from the path, not from GOOGLE_CLOUD_LOCATION=global)."""
    eid = _engine_id()
    if not eid:
        return None
    return f"agentengine://projects/{project_id}/locations/{_engine_location()}/reasoningEngines/{eid}"


def _build_services() -> tuple[object, object]:
    """Managed Sessions + Memory Bank for the A2A Runner (same engine as dev UI)."""
    eid = _engine_id()
    if eid:
        loc = _engine_location()
        return (
            VertexAiSessionService(project=project_id, location=loc, agent_engine_id=eid),
            VertexAiMemoryBankService(project=project_id, location=loc, agent_engine_id=eid),
        )
    return InMemorySessionService(), None


# --- Base app: ADK dev UI (served at /dev-ui/) + managed sessions/memory ------
_AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # parent of app/
_engine = _engine_uri()
app = get_fast_api_app(
    agents_dir=_AGENTS_DIR,
    web=True,
    a2a=False,  # we add our OWN A2A routes below (with the A2UI/streaming card)
    session_service_uri=_engine,
    memory_service_uri=_engine,
)
# Dev UI is served at root ("/"); its API (/list-apps, /run, …) is also at root.

# --- A2A (GE-facing) on the SAME app ------------------------------------------
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
_artifact_service = (
    GcsArtifactService(bucket_name=logs_bucket_name)
    if logs_bucket_name
    else InMemoryArtifactService()
)
_session_service, _memory_service = _build_services()
_runner = Runner(
    app=adk_app,
    artifact_service=_artifact_service,
    session_service=_session_service,
    memory_service=_memory_service,
)
_request_handler = DefaultRequestHandler(
    agent_executor=A2aAgentExecutor(runner=_runner),
    task_store=InMemoryTaskStore(),
)
A2A_RPC_PATH = f"/a2a/{adk_app.name}"


async def build_dynamic_agent_card() -> AgentCard:
    """Agent card: streaming on + A2UI/ADK extensions."""
    builder = AgentCardBuilder(
        agent=adk_app.root_agent,
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[
                AgentExtension(
                    uri="https://google.github.io/adk-docs/a2a/a2a-extension/",
                    description="Ability to use the new agent executor implementation",
                ),
                A2UI_EXTENSION,
            ],
        ),
        rpc_url=f"{os.getenv('APP_URL', 'http://0.0.0.0:8080')}{A2A_RPC_PATH}",
        agent_version=os.getenv("AGENT_VERSION", "0.1.0"),
    )
    return await builder.build()


# Build the card synchronously at import and register the A2A routes on `app`.
try:
    asyncio.get_running_loop()
    nest_asyncio.apply()
except RuntimeError:
    pass
_agent_card = asyncio.run(build_dynamic_agent_card())
_a2a_app = A2AFastAPIApplication(agent_card=_agent_card, http_handler=_request_handler)
_a2a_app.add_routes_to_app(
    app,
    agent_card_url=f"{A2A_RPC_PATH}{AGENT_CARD_WELL_KNOWN_PATH}",
    rpc_url=A2A_RPC_PATH,
    extended_agent_card_url=f"{A2A_RPC_PATH}{EXTENDED_AGENT_CARD_PATH}",
)

# The dev UI registers a catch-all for client-side routing; make sure the A2A
# routes are matched FIRST (Starlette matches in order) by moving them to the front.
_a2a_routes = [r for r in app.router.routes if getattr(r, "path", "").startswith("/a2a/")]
for _r in _a2a_routes:
    app.router.routes.remove(_r)
app.router.routes[:0] = _a2a_routes


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback."""
    logger.log_struct(feedback.model_dump(), severity="INFO")
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
