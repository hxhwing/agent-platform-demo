# TODOs-for-coding-agent.md — Antigravity TO-DO (Game Character Designer demo)

> **For the Antigravity agent.** Build the whole demo **from scratch** on **Google
> Cloud Shell** — no repo pre-exists; create every project, file, and config from
> these tasks. Set the project explicitly (`gcloud config set project …`),
> location = `global`, region = `us-central1`. Assume all IAM is granted. Prefer **`agents-cli`** for scaffold /
> deploy / publish / eval and `gcloud` for infra. The human-facing walkthrough is
> `guide.md` (the only other doc).

## Conventions
- Build the **A2A Agent** (`localization-studio`) BEFORE the **Main Agent** (`game-producer`) — the Main Agent needs its Agent Card.
- The Main Agent has TWO deployments of the same code:
  - **Agent Runtime** (main) — `adk`, no A2A, Markdown output (do NOT set `A2UI_ENABLED`); per-user managed memory auto-wired.
  - **Cloud Run** (optional, A2UI card) — `adk_a2a`, A2A on, deploy with `A2UI_ENABLED=1`; reuses the Agent Runtime engine as its memory backend.
- Models: text/vision `gemini-3.5-flash`; out-image `gemini-3.1-flash-image`. Both resolve ONLY in `location=global`.
- Transient deploy `code 13` → retry. `400 FAILED_PRECONDITION` on publish → use an SA with agent-creation quota.

## Phase 0 — Setup
- [ ] Set the project (Cloud Shell has none by default): `gcloud config set project YOUR_PROJECT_ID`, then `export PROJECT="$(gcloud config get-value project)"`. Also export `PROJECT_NUMBER`, `REGION=us-central1`, `GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_GENAI_USE_VERTEXAI=TRUE`.
- [ ] Install `uv` + `uv tool install google-agents-cli`; then `agents-cli setup` (installs agents-cli skills into the coding agent); confirm in Antigravity that the ~7 google-agents-cli skills loaded.
- [ ] Enable APIs: aiplatform, run, cloudbuild, discoveryengine, modelarmor, cloudtrace.
- [ ] NO storage bucket — the portrait is delivered as inline image bytes (GE renders them; user downloads; nothing persisted).
- [ ] NO separate Agent Engine to provision — the Agent Runtime deploy (Phase 4) creates its own and auto-wires Sessions + Memory Bank.

## Phase 1 — A2A Agent (localization-studio → Cloud Run)
- [ ] `agents-cli scaffold create localization-studio --agent adk_a2a --deployment-target cloud_run --region $REGION --prototype`
- [ ] Write `localization-studio/app/agent.py`: `root_agent` gemini-3.5-flash, location=global; returns a Markdown table `Language|Name|Tagline|Lore|Signature Dialogue` (ja/es/zh-CN default), transcreation; `App(root_agent, name="app")`.
- [ ] Write `localization-studio/.env` (GENAI_USE_VERTEXAI, PROJECT, LOCATION=global).
- [ ] Deploy: `cd localization-studio && agents-cli deploy --no-confirm-project --project $PROJECT --region $REGION`. Record `LOC_URL`.

## Phase 2 — Main Agent (game-producer: scaffold + code)
- [ ] `agents-cli scaffold create game-producer --agent adk --deployment-target agent_runtime --region $REGION --prototype`
- [ ] `app/tools.py`: async `generate_character_portrait(art_brief, tool_context, aspect_ratio="1:1", image_size="1K")` → `gemini-3.1-flash-image` with `ImageConfig(aspect_ratio, image_size)`; keep raw PNG bytes in an in-process cache (module dict) keyed by a token, stash only that token as `portrait_key` in state (end-of-turn callback attaches the bytes inline — NO bucket/URL). Allowed aspect_ratio {1:1,3:2,2:3,3:4,1:4,4:1,4:3,4:5,5:4,1:8,8:1,9:16,16:9,21:9,9:21}; image_size {512,1K,2K,4K}; validate+fallback; docstring lists exact allowed values (LLM reads it).
- [ ] `app/render.py`: `render_character_card(name, tagline, lore, stats_json, skills_json, localization_markdown, world='', tool_context=None)` builds the **Markdown sheet** split into `md_top`=title/tagline/World and `md_bottom`=Lore/Stats/Skills/Localization (**`md_bottom` starts with a blank line before its first `##` heading** so GE doesn't show it as literal text); localization reordered zh-CN→es→ja; stash md_top/md_bottom in state; return `{status,name}`.
- [ ] `app/agent.py`: root `Agent` + 5 AgentTools (researcher=google_search-only (IP check + research the game/genre's art style, story themes, trends); art_creator owns the portrait tool and passes aspect_ratio/image_size; story_writer; skill_designer; localization_agent=`RemoteA2aAgent` → `$LOCALIZATION_AGENT_URL/a2a/app/.well-known/agent-card.json`). `PreloadMemoryTool` + `after_agent_callback _finalize_turn`: save memory; take cached portrait bytes via `portrait_key`; return interleaved `[text(md_top), portrait BYTES, text(md_bottom)]`. Instruction: reply in user's language; narrate each step (`**Specialist** emoji status` + blank line) before each tool call; Markdown path emits NO image/link.
- [ ] `app/agent_runtime_app.py`: plain `AdkApp` (Agent Runtime path; managed memory auto-wired to this deployment's own engine).
- [ ] `game-producer/.env` (**local** config, in-memory): GENAI_USE_VERTEXAI, PROJECT, LOCATION=global, LOCALIZATION_AGENT_URL=http://localhost:8000 (deploy overrides to $LOC_URL). NO `AGENT_ENGINE_ID`/`AGENT_ENGINE_LOCATION` (managed memory wired at deploy time); NO bucket vars.

## Phase 3 — Local run + eval
- [ ] Start loc server (`cd localization-studio && uv run uvicorn app.fast_api_app:app :8000`) + `cd game-producer && agents-cli playground --port 8080`. Local = **in-memory** sessions/memory (no engine URIs). Managed Sessions + Memory Bank are demonstrated on the deployed agent.
- [ ] `agents-cli eval dataset synthesize && agents-cli eval generate && agents-cli eval grade`; `uv run pytest tests/unit tests/integration`.

## Phase 4 — Deploy to Agent Runtime (main path)
- [ ] `agents-cli deploy --no-confirm-project --project $PROJECT --update-env-vars LOCALIZATION_AGENT_URL=$LOC_URL` (NO A2UI_ENABLED; NO AGENT_ENGINE_* — Agent Runtime auto-wires its own engine).
- [ ] From the deploy output, capture the Reasoning Engine numeric ID as `AGENT_ENGINE_ID` (reused by the optional Cloud Run path).
- [ ] Find the GE app id with `agents-cli publish gemini-enterprise --list`, then register (from the project dir; `publish` auto-reads `deployment_metadata.json` for the engine id / type / project): `agents-cli publish gemini-enterprise --gemini-enterprise-app-id <GE_APP> --display-name "Game Character Designer" --description "…"` (SA with quota).

## Phase 5 — Deploy to Cloud Run — OPTIONAL (A2UI-card demo; see guide.md Appendix A)
- [ ] Prerequisite: Phase 4 done (Cloud Run reuses its `AGENT_ENGINE_ID`).
- [ ] Extend `app/render.py`: in `render_character_card` also build an A2UI v0.8 message list (flat surfaceUpdate adjacency list, unique surfaceId, beginRendering+styles.primaryColor — NO Image component; portrait attached separately); stash a2ui messages in state; add `a2ui_card_parts()` (wrap each msg as A2A DataPart via `<a2a_datapart_json>`, mime application/json+a2ui).
- [ ] Extend `app/agent.py` `_finalize_turn`: when `A2UI_ENABLED` is set, emit A2UI DataParts + portrait as a separate inline image part (instead of the Markdown sheet).
- [ ] Create `app/fast_api_app.py` + `Dockerfile`: `get_fast_api_app(web=True)` (dev-ui) + our A2A routes (`A2AFastAPIApplication`, streaming=True, card advertises A2UI v0.8 ext); move `/a2a/*` routes to front of `app.router.routes`; wire managed memory from the `AGENT_ENGINE_ID` env var.
- [ ] Add the Cloud Run / A2A target: `agents-cli scaffold enhance --agent adk_a2a --deployment-target cloud_run --region $REGION`.
- [ ] `agents-cli deploy --no-confirm-project --project $PROJECT --region $REGION --update-env-vars A2UI_ENABLED=1,LOCALIZATION_AGENT_URL=$LOC_URL,AGENT_ENGINE_ID=$AGENT_ENGINE_ID,AGENT_ENGINE_LOCATION=$REGION`. Record `GP_URL`.
- [ ] Grant `roles/run.invoker` on the service to `service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com` (and to the publishing identity).
- [ ] Register: `agents-cli publish gemini-enterprise --registration-type a2a --deployment-target cloud_run --agent-card-url $GP_URL/a2a/app/.well-known/agent-card.json --gemini-enterprise-app-id <GE_APP> --project-id $PROJECT --display-name "Game Character Designer (Cloud Run)" --description "…"`.
- [ ] dev-ui access note: `gcloud run services proxy game-producer --region $REGION` → `/dev-ui/`.

## Phase 6 — Govern + Observe
- [ ] Model Armor (Gemini Enterprise path — NOT Vertex floor settings). Three steps:
  1. **Create a template** (multi-region `us`; gcloud needs `gcloud config set api_endpoint_overrides/modelarmor https://modelarmor.us.rep.googleapis.com/`): `gcloud model-armor templates create ge-game-studio-armor --location=us --pi-and-jailbreak-filter-settings-enforcement=enabled --pi-and-jailbreak-filter-settings-confidence-level=MEDIUM_AND_ABOVE --malicious-uri-filter-settings-enforcement=enabled --basic-config-filter-enforcement=enabled`. Then **via REST** (one PATCH) enable multi-language detection + request/response logging: `templateMetadata.multiLanguageDetection.enableMultiLanguageDetection=true` and `templateMetadata.logSanitizeOperations=true`. (No Responsible AI filters.)
  2. **Enable on the GE app**: PATCH the `default_assistant` `…?update_mask=customerPolicy` with `customerPolicy.modelArmorConfig` → `userPromptTemplate` + `responseTemplate` = the template resource name, `failureMode: FAIL_CLOSED` (screens prompts AND responses).
  3. **Grant** the Discovery Engine service agent `service-$PROJECT_NUMBER@gcp-sa-discoveryengine.iam.gserviceaccount.com` → `roles/modelarmor.user`, else FAIL_CLOSED blocks every message.
- [ ] `agents-cli infra single-project` (Cloud Trace + BigQuery analytics).

## Acceptance
- [ ] Local: sketch+brief → portrait + lore + stats + 3-lang localization; A2A hop fires.
- [ ] Agent Runtime in GE: Markdown sheet, portrait between World and Lore, narrated steps, per-user memory across sessions.
- [ ] (Optional) Cloud Run in GE: A2UI card renders; dev-ui reachable via proxy.
- [ ] Govern: injection / malicious-URL / sensitive-data blocked by Model Armor (prompt + response). Observe: Cloud Trace waterfall present.
