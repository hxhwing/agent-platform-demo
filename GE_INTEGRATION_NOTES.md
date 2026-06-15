# Gemini Enterprise Integration Notes — Agent Runtime vs Cloud Run

> Field notes from building the **AI Game Studio** demo (`game-producer`) and
> registering it to **Gemini Enterprise (GE)**. Captures what each hosting +
> registration path supports, the gotchas hit, and the workarounds. As of
> 2026-06-08, against `agents-cli` 0.3.0, `google-cloud-aiplatform` 1.156.0,
> `google-adk` 1.34.1, `a2a-sdk` 0.3.26, project `<your-gcp-project-id>`.

This project ships **two interchangeable deployments** of the same agent
(toggled by `agents-cli-manifest.yaml`):

| | **Option 1 — Agent Runtime (ADK-native)** | **Option 2 — Cloud Run (A2A + A2UI)** |
|---|---|---|
| Entrypoint | `app/agent_runtime_app.py` (`AdkApp`) | `app/fast_api_app.py` (`A2AFastAPIApplication`) + `Dockerfile` |
| GE registration | **Add Agent via Agent Engine** (ADK) | **Add A2A agent** (by agent-card URL) |
| `agents-cli publish` | `--registration-type adk` | `--registration-type a2a --agent-card-url …` |
| Rich output | **Markdown** (incl. image) | **A2UI v0.8 card** |
| Intermediate steps in GE chat | text-narration only (see §3) | text-narration only (see §3) |
| Structured tool/agent trace | Cloud Trace (observability) | Cloud Trace (observability) |
| GE → agent auth | reasoning-engine invoke (+ OAuth for tools) | Discovery Engine SA + `run.invoker` |
| End-user identity forwarded? | ✅ yes → **per-user Memory Bank** | ❌ no → per-conversation only |
| Managed Sessions + Memory Bank | ✅ auto-wired by `AdkApp` | manual `VertexAi*Service` in the Runner |
| Streaming | non-streaming runtime, but events still stream to GE | `streaming=True` |

---

## 1. The two GE integration paths

GE has **two distinct ways** to add a custom agent; they are NOT interchangeable:

1. **"Add Agent via Agent Engine"** (ADK / reasoning-engine). Registers a
   deployed Agent Runtime reasoning engine directly (`adkAgentDefinition` /
   `provisionedReasoningEngine`). This is the native path for ADK agents.
2. **"Add A2A agent"**. Registers ANY A2A endpoint by its agent-card URL
   (`a2aAgentDefinition.jsonAgentCard`). Transport-agnostic.

`agents-cli publish gemini-enterprise` implements both via `--registration-type`
(`adk` | `a2a`). **Gotcha:** the CLI hard-blocks `a2a` + `deployment_target=
agent_runtime` ("Gemini Enterprise does not yet support invoking A2A agents
hosted on Agent Runtime"). To register an Agent-Runtime A2A endpoint via the
"Add A2A agent" path anyway, pass `--agent-card-url …` and **omit**
`--deployment-target agent_runtime` (it defaults to `cloud_run`, skips the guard,
and just POSTs the fetched card). We used this earlier, but **A2A-on-Agent-Runtime
then fails at invocation** (see §4) — so it's not recommended; use Cloud Run for A2A.

---

## 2. Rich output: A2UI vs Markdown

- **A2UI renders ONLY over A2A.** The agent card must advertise the extension
  `https://a2ui.org/a2a-extension/a2ui/v0.8` (+ `supportedCatalogIds`), and the UI
  must be emitted as an A2A **`DataPart`** with `metadata.mimeType =
  application/json+a2ui`. GE supports **v0.8 only**.
- **A2UI v0.8 format** is a *flat component adjacency list*: `surfaceUpdate`
  (components keyed by `id`; `Card.child` = id string, `Column.children` =
  `{"explicitList":[ids]}`) then `beginRendering` (declares `root`, optional
  `styles.primaryColor`). Enums matter: `Text.usageHint` ∈ h1–h5/caption/body;
  `Image.usageHint` ∈ icon/avatar/…/largeFeature/header; `Divider.axis`. There is
  **no Table** component and only a fixed **Icon** set; only `primaryColor`/`font`
  global styling — no per-component colors.
- **Use a UNIQUE `surfaceId` per render** — GE drops a repeated surfaceId, so only
  the first turn would show.
- **Emitting a DataPart from ADK:** ADK has no public API for this. Trick: emit a
  `text/plain` inline-data part wrapped in `<a2a_datapart_json>…</a2a_datapart_json>`;
  `google.adk.a2a.converters.part_converter` converts it verbatim to the DataPart.
- **Markdown (ADK-native path):** the agent replies with Markdown. **Confirmed
  (2026-06-08):** GE renders inline image **BYTES** (the ADK **artifact** that
  `generate_character_portrait` saves via `tool_context.save_artifact`) — the
  portrait shows in the chat — but GE does **NOT** render a Markdown image URL
  `![](https://…)` (it appears as raw text). So on the Markdown path: **omit the
  `![](url)` line** and rely on the saved artifact for the image. Text / headings /
  bold / Markdown tables all render fine. (The artifact image appears at the point
  the portrait tool runs, i.e. mid-stream, not embedded in the final sheet.)
- **Headings need a blank line before them — across part boundaries.** **Confirmed
  (2026-06-09):** when the final reply is split into multiple text parts
  (`[md_top, image, md_bottom]`), GE **concatenates the text** when rendering. If
  `md_bottom` starts directly with `## Lore` and `md_top` ended with a single
  newline, the stream is `…World\n## Lore` and GE shows **`## Lore` as literal
  text**. Fix: lead `md_bottom` with a **blank line** (`\n\n`) before its first
  heading. (Subsequent headings were fine because they already had a blank line.)

---

## 3. Showing the process (tool_call / agent transfer)

⚠️ **Empirical (2026-06-08): the GE chat does NOT render structured `tool_call` /
agent-transfer chips — on EITHER path.** It renders the agent's streamed **text**.
The structured tool/agent breakdown is captured in **Cloud Trace** (observability),
not the chat surface.

- The runtime DOES stream the events: ADK-native exposes `function_call` /
  `function_response` / sub-agent events via the reasoning-engine `streamQuery`
  API; Cloud Run A2A streams `TaskStatusUpdateEvent`s (with `function_call` data
  parts). But GE's chat surfaces neither as structured activity.
- **So the practical way to show the process in the chat is to make the agent
  NARRATE each step as a short text line** (`**Agent** 🎨 …` + a blank line) right
  before each tool call. This works the same on both paths. (Streamed text is
  concatenated → use `\n\n` between lines so steps don't run together.)
- Note Agent Runtime A2A is also non-streaming (`streaming=False`) — another reason
  it can't show live steps; Cloud Run A2A can stream but GE still only shows text.
- For the real structured trace (spans for each LLM/tool call), open **Cloud Trace**
  (enable observability via `agents-cli infra` / the observability skill).

---

## 4. GE → agent authentication

- **ADK-native Agent Runtime:** GE invokes the reasoning engine directly. Tools
  that call Google APIs on the user's behalf use an OAuth **Authorization**
  (`authorizationConfig.toolAuthorizations`).
- **A2A on Cloud Run:** GE invokes with its **Discovery Engine service agent**
  (`service-<PROJECT_NUMBER>@gcp-sa-discoveryengine.iam.gserviceaccount.com`) using
  an ID token; grant it `roles/run.invoker` on the service. The **identity running
  `publish` also needs `run.invoker`** (the CLI fetches the private card at register
  time). No per-user OAuth. `401 … text/html` at invocation = the `run.invoker`
  grant is missing/not propagated (GE got the Cloud Run 403 HTML page).
- **A2A on Agent Runtime:** GE calls `…/reasoningEngines/<id>/a2a/v1/message:send`
  and gets **401** — GE has no token the endpoint accepts. Requires an OAuth
  **Authorization** (`serverSideOauth2`, scope `cloud-platform`, GE redirect URIs
  `https://vertexaisearch.cloud.google.com/oauth-redirect` + `/static/oauth/oauth.html`)
  referenced via `--authorization-id`; the consenting user needs `roles/aiplatform.user`.
  This works but is the path the CLI flags as unsupported — prefer Cloud Run for A2A.

---

## 4a. ⚠️ A2A on Agent Runtime — known problems (AVOID)

We tried registering an **A2A** endpoint that is **hosted on Agent Runtime** (i.e.
A2A + Agent Engine, not Cloud Run). It can be made to work but only by stacking
workarounds, and Google flags it as unsupported. Issues hit, in order:

1. **`agents-cli publish` blocks it.** `--registration-type a2a` + `deployment_target=
   agent_runtime` raises *"Gemini Enterprise does not yet support invoking A2A agents
   hosted on Agent Runtime."* Route-around: register via the "Add A2A agent" path
   (`--agent-card-url …`, omit `--deployment-target agent_runtime`).
2. **Deploy fails at introspection:** `'AgentCard' object has no attribute
   'DESCRIPTOR'` — the SDK serializes the pydantic `AgentCard` with proto-only
   `MessageToJson`. Needs an import-time shim (`ToProto.agent_card`).
3. **Invocation returns 401.** GE calls `…/reasoningEngines/<id>/a2a/v1/message:send`
   with no token the endpoint accepts. Requires a GE OAuth **Authorization**
   (`serverSideOauth2`, scope `cloud-platform`) + `--authorization-id` + per-user
   consent + `roles/aiplatform.user`. Fragile, and it's per-user consent UX.
4. **Non-streaming** (`streaming=False`) → no live step display.
5. **No per-user memory** (A2A doesn't forward the end-user, §5) and managed
   Sessions/Memory must be wired manually (§6).

**Verdict:** don't use A2A-on-Agent-Runtime. For A2A use **Cloud Run** (Option 2);
for Agent Runtime use **ADK-native** (Option 1). The shim/Authorization code from
this experiment was removed when Agent Runtime was switched back to ADK-native.

## 5. End-user identity & per-user Memory Bank ★

- **A2A: GE does NOT forward the signed-in end user.** Proven by logging the
  inbound A2A request: `call_context.user` is empty, message/context metadata carry
  only A2UI capabilities, and the only credential is GE's **service** ID token
  (`aud=<engine>`, `azp==sub=<service id>`, **no email/name**). ADK therefore falls
  back to `user_id = A2A_USER_<context_id>` → **Sessions + Memory Bank are keyed
  per-conversation, NOT per user**. Memory doesn't persist across a user's chats and
  isn't isolated per GE user. A custom request converter that scans
  request/message metadata can't fix it because the identity simply isn't sent.
- **ADK-native: GE forwards the end user**, so Memory Bank is keyed by the real
  user. **This is the only path that gives true per-user memory in GE.**
- **Net:** in GE today it's effectively **A2UI card XOR per-user memory** — you
  can't get both. (Option 1 = memory; Option 2 = card.)

---

## 6. Managed Sessions + Memory Bank wiring

- **ADK-native Agent Runtime:** `AdkApp` **auto-wires** `VertexAiSessionService` +
  `VertexAiMemoryBankService` to this deployment's OWN engine (platform injects
  `GOOGLE_CLOUD_AGENT_ENGINE_ID`). Pass **no** `memory_service_builder`.
- **A2A (Agent Runtime or Cloud Run):** the A2A `Runner` does **not** auto-wire
  memory — build the two services manually. On Cloud Run there's **no**
  `GOOGLE_CLOUD_AGENT_ENGINE_ID` injection, so pass `AGENT_ENGINE_ID` as an env var.
- The Agent Engine is **regional** (us-central1) while the Gemini model needs
  `location=global` — keep them separate (`AGENT_ENGINE_LOCATION=us-central1`).

---

## 7. Deploy gotchas

- **A2A → Agent Runtime introspection bug:** `'AgentCard' object has no attribute
  'DESCRIPTOR'` — the SDK serializes the pydantic `AgentCard` with proto-only
  `MessageToJson`. Workaround: an import-time shim converting pydantic→proto via
  `a2a.utils.proto_utils.ToProto.agent_card`. (Moot now that Agent Runtime is ADK.)
- **Cloud Run deploy `code 13` INTERNAL:** usually transient — just re-run.
- **`400 FAILED_PRECONDITION: Failed to allocate quota for agent creation`:** the
  agent-creation quota is **per service account** — run `publish` as an SA that has
  it (≠ a 403, which is an IAM problem).
- **GE "Session not found" after registering:** upgrade `google-cloud-aiplatform`
  and redeploy.
- **Models:** Gemini 3 (`gemini-3.5-flash`, `gemini-3.1-flash-image`)
  resolve **only in `location=global`** on this project (us-central1 → 404).
- **Image params:** Nano Banana 2 honors `ImageConfig(aspect_ratio, image_size)`
  (verified: 16:9 → 1376×768). Pass them deterministically (extract from the user
  message into state) — the LLM is unreliable about forwarding tool args.

---

## 8. Recommendation

- **Demo "complete Agent Platform"** (managed sessions + **per-user** memory +
  native step streaming): **Option 1 — Agent Runtime, ADK-native, Markdown.**
- **Demo rich interactive UI**: **Option 2 — Cloud Run, A2A + A2UI card** (accept
  per-conversation memory; surface steps via narration).
- You cannot currently have **A2UI card + per-user memory** in the same GE agent.

Toggle in `agents-cli-manifest.yaml`:
- Option 1: `deployment_target: agent_runtime`, `base_template: adk`, `is_a2a: false`; deploy (no `A2UI_ENABLED`).
- Option 2: `deployment_target: cloud_run`, `base_template: adk_a2a`, `is_a2a: true`; deploy `--update-env-vars A2UI_ENABLED=1,AGENT_ENGINE_ID=<engine>,…` + grant `run.invoker`.
