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

import logging

import pytest

from app.agent_runtime_app import AgentEngineApp


@pytest.fixture
def agent_app(monkeypatch: pytest.MonkeyPatch) -> AgentEngineApp:
    """Fixture to create and set up AgentEngineApp instance"""
    # Set integration test flag to mock external services
    monkeypatch.setenv("INTEGRATION_TEST", "TRUE")

    from app.agent_runtime_app import agent_runtime

    agent_runtime.set_up()
    return agent_runtime


def test_agent_runtime_is_adk_app() -> None:
    """Agent Runtime (Option 1) deploys a plain ADK AdkApp (NOT A2A). Markdown
    output + managed Sessions/Memory Bank; A2UI lives on the Cloud Run app."""
    from vertexai.agent_engines.templates.adk import AdkApp

    from app.agent_runtime_app import agent_runtime

    assert isinstance(agent_runtime, AdkApp)
    ops = agent_runtime.register_operations()
    assert any("register_feedback" in v for v in ops.values())


def test_agent_feedback(agent_app: AgentEngineApp) -> None:
    """
    Integration test for the agent feedback functionality.
    Tests that feedback can be registered successfully.
    """
    feedback_data = {
        "score": 5,
        "text": "Great response!",
        "user_id": "test-user-456",
        "session_id": "test-session-456",
    }

    # Should not raise any exceptions
    agent_app.register_feedback(feedback_data)

    # Test invalid feedback
    with pytest.raises(ValueError):
        invalid_feedback = {
            "score": "invalid",  # Score must be numeric
            "text": "Bad feedback",
            "user_id": "test-user-789",
            "session_id": "test-session-789",
        }
        agent_app.register_feedback(invalid_feedback)

    logging.info("All assertions passed for agent feedback test")
