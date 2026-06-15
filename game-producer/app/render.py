"""A2UI v0.8 rich-content rendering for the Game Producer.

`render_character_card` assembles the final deliverable: a declarative A2UI v0.8
"character card" (portrait + lore + stats + skills + localization). Gemini
Enterprise renders this as a native card instead of plain text.

How it reaches Gemini Enterprise
--------------------------------
GE renders A2UI **only** over the A2A path: the agent card must advertise the
A2UI v0.8 extension (see app/agent_runtime_app.py) and the UI must arrive as A2A
``DataPart``s whose ``metadata.mimeType`` is ``application/json+a2ui``.

A2UI v0.8 uses an *adjacency list* model — a flat list of components referenced
by ``id`` — delivered as a sequence of messages:

  1. ``surfaceUpdate`` — the flat component list for a surface.
  2. ``beginRendering`` — declares the ``root`` component id; "draw now".

Each A2UI message is wrapped in its own A2A ``DataPart`` (matching the official
``a2ui.a2a.create_a2ui_part`` pattern, where ``DataPart.data`` is a single
message dict). ADK has no public API to emit a raw ``DataPart`` from a tool, so
we use ADK's documented escape hatch: a ``text/plain`` inline-data part whose
bytes are wrapped in ``<a2a_datapart_json>…</a2a_datapart_json>`` is converted
verbatim into the enclosed ``DataPart`` by
``google.adk.a2a.converters.part_converter`` — see ``a2ui_card_parts`` below.

``render_character_card`` stashes the A2UI messages in session state; the root
agent's ``after_agent_callback`` (app/agent.py) reads them and emits the parts as
the final turn. The tool still returns a Markdown ``summary`` so the card stays
readable in the local ADK Web UI, which does not render A2UI.
"""

import json
import re
import uuid

from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

A2UI_MIME_TYPE = "application/json+a2ui"
_SURFACE_BASE = "character_card"
# Accent color for the card theme (passed via beginRendering styles).
_THEME_PRIMARY_COLOR = "#7C3AED"

# ADK escape-hatch tags: part_converter turns a text/plain inline-data part whose
# bytes are wrapped in these tags into the literal A2A DataPart they contain.
_A2A_DATA_PART_START_TAG = b"<a2a_datapart_json>"
_A2A_DATA_PART_END_TAG = b"</a2a_datapart_json>"

# Session-state key the after_agent_callback reads to emit the card.
A2UI_STATE_KEY = "a2ui_card"


def _text(node_id: str, value: str, *, hint: str = "body") -> dict:
    """A Text component. `hint` must be a v0.8 enum: h1-h5, caption, body."""
    return {
        "id": node_id,
        "component": {"Text": {"text": {"literalString": value}, "usageHint": hint}},
    }


def _icon(node_id: str, name: str) -> dict:
    """An Icon component. `name` must be in the v0.8 standard icon set
    (e.g. favorite, star, locationOn, mail, shield is NOT available)."""
    return {"id": node_id, "component": {"Icon": {"name": {"literalString": name}}}}


def _datapart_message_to_part(message: dict) -> genai_types.Part:
    """Wrap a single A2UI message in an A2A DataPart, encoded as an ADK part.

    The returned genai Part carries the DataPart JSON inside the
    ``<a2a_datapart_json>`` tags; ADK's part_converter emits it verbatim as an
    A2A ``DataPart`` with ``metadata.mimeType = application/json+a2ui``.
    """
    data_part = {
        "kind": "data",
        "data": message,
        "metadata": {"mimeType": A2UI_MIME_TYPE},
    }
    payload = (
        _A2A_DATA_PART_START_TAG
        + json.dumps(data_part).encode("utf-8")
        + _A2A_DATA_PART_END_TAG
    )
    return genai_types.Part(
        inline_data=genai_types.Blob(mime_type="text/plain", data=payload)
    )


def a2ui_card_parts(messages: list[dict]) -> list[genai_types.Part]:
    """Convert stored A2UI messages into emittable ADK parts (one per message)."""
    return [_datapart_message_to_part(m) for m in messages]


def _lang_rank(cell: str) -> int:
    """Sort key for the localization language column: Chinese, Spanish, Japanese.

    Robust to markdown/prefixes (e.g. ``**zh-CN**``) and to code or full-name forms.
    """
    raw = cell.lower()
    letters = re.sub(r"[^a-z]", "", raw)  # strip **, spaces, hyphens, digits
    if letters.startswith("zh") or "chinese" in letters or "中文" in raw or "简" in raw:
        return 0
    if letters.startswith("es") or "span" in letters:
        return 1
    if letters.startswith("ja") or "japan" in letters or "日" in raw:
        return 2
    return 3


def _parse_localization(md: str) -> tuple[list[str], list[list[str]], list[str]]:
    """Parse a Markdown localization table into (header, rows, notes).

    Rows are reordered to **Chinese, Spanish, Japanese**. `notes` holds any
    non-table prose lines (e.g. the studio's adaptation note).
    """
    header: list[str] = []
    rows: list[list[str]] = []
    notes: list[str] = []
    for raw in md.splitlines():
        s = raw.strip()
        if not s or set(s) <= set("-:| "):  # blank or Markdown separator → skip
            continue
        if "|" in s:
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
            if not header:
                header = cells
            else:
                rows.append(cells)
        else:
            notes.append(s)
    rows.sort(key=lambda r: _lang_rank(r[0] if r else ""))
    return header, rows, notes


def render_character_card(
    name: str,
    tagline: str,
    lore: str,
    stats_json: str,
    skills_json: str,
    localization_markdown: str,
    world: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Render the finished character as an A2UI v0.8 card (the demo deliverable).

    Call this LAST, after you have the lore, stats, skills and the localization
    table. The card (and the portrait) are emitted to Gemini Enterprise
    automatically at the end of the turn; you only need to give the user a short
    confirmation. The portrait is attached as an inline image — you do not pass it
    here.

    Args:
        name: Character name.
        tagline: One-line hook / title.
        lore: Short backstory paragraph.
        stats_json: JSON object string of numeric stats, e.g.
            '{"HP": 120, "ATK": 85, "DEF": 60, "SPD": 95}'.
        skills_json: JSON array string of skills, each like
            '[{"name": "...", "desc": "...", "cost": 30}]'.
        localization_markdown: The Markdown table returned by the localization studio.
        world: Optional world/IP name this character belongs to (for continuity).
    """
    stats = json.loads(stats_json) if stats_json else {}
    skills = json.loads(skills_json) if skills_json else []
    skill_lines = "\n".join(
        f"- **{s.get('name', '?')}** — {s.get('desc', '')}"
        + (f"  _(cost {s['cost']})_" if s.get("cost") is not None else "")
        for s in skills
    )

    # --- Build the A2UI v0.8 adjacency list (flat components, id references) ---
    # `components` holds every node; `order` is the root Column's child id list.
    # Intermediate nodes (stat blocks, skill-card internals) are added to
    # `components` only — just their container id goes into `order`.
    components: list[dict] = []
    order: list[str] = []

    def add(comp: dict, *, root: bool = True) -> str:
        components.append(comp)
        if root:
            order.append(comp["id"])
        return comp["id"]

    def add_heading(key: str, icon_name: str, label: str) -> None:
        """A section heading: an Icon + h5 Text laid out in a Row."""
        iid, tid, rid = f"{key}_i", f"{key}_t", f"{key}_row"
        add(_icon(iid, icon_name), root=False)
        add(_text(tid, label, hint="h5"), root=False)
        add({"id": rid, "component": {"Row": {
            "children": {"explicitList": [iid, tid]}, "alignment": "center"}}})

    # Header
    add(_text("title", name, hint="h1"))
    add(_text("subtitle", tagline, hint="h3"))
    if world:
        add(_icon("world_i", "locationOn"), root=False)
        add(_text("world_t", world, hint="caption"), root=False)
        add({"id": "world_row", "component": {"Row": {
            "children": {"explicitList": ["world_i", "world_t"]}, "alignment": "center"}}})

    # (The portrait is attached as a separate inline image part by the end-of-turn
    # callback — A2UI can't embed raw bytes and we use no bucket/URL.)
    add({"id": "div1", "component": {"Divider": {"axis": "horizontal"}}})

    # Lore
    add(_text("lore", lore, hint="body"))

    # Stats — a horizontal panel of value/label blocks inside a Card.
    if stats:
        add_heading("stats", "favorite", "Stats")
        block_ids = []
        for k, v in stats.items():
            vid, lid, colid = f"stat_{k}_v", f"stat_{k}_l", f"stat_{k}_c"
            add(_text(vid, str(v), hint="h3"), root=False)
            add(_text(lid, str(k), hint="caption"), root=False)
            add({"id": colid, "component": {"Column": {
                "children": {"explicitList": [vid, lid]}, "alignment": "center"}}}, root=False)
            block_ids.append(colid)
        add({"id": "stats_blocks", "component": {"Row": {
            "children": {"explicitList": block_ids},
            "distribution": "spaceBetween", "alignment": "center"}}}, root=False)
        add({"id": "stats_panel", "component": {"Card": {"child": "stats_blocks"}}})

    # Skills — each skill as its own Card (name / desc / cost).
    if skills:
        add_heading("skills", "star", "Skills")
        for i, s in enumerate(skills):
            nid, did, cid, colid, cardid = (
                f"skill_n_{i}", f"skill_d_{i}", f"skill_c_{i}",
                f"skill_col_{i}", f"skill_card_{i}",
            )
            add(_text(nid, s.get("name", "?"), hint="h5"), root=False)
            add(_text(did, s.get("desc", ""), hint="body"), root=False)
            kids = [nid, did]
            if s.get("cost") is not None:
                add(_text(cid, f"Cost {s['cost']}", hint="caption"), root=False)
                kids.append(cid)
            add({"id": colid, "component": {"Column": {
                "children": {"explicitList": kids}, "alignment": "start"}}}, root=False)
            add({"id": cardid, "component": {"Card": {"child": colid}}})

    # Localization — one Card per language (A2UI v0.8 has no Table component), with
    # each field on its own labelled line. Order: Chinese, Spanish, Japanese.
    header, loc_rows, loc_notes = (
        _parse_localization(localization_markdown) if localization_markdown else ([], [], [])
    )
    if loc_rows:
        add({"id": "div2", "component": {"Divider": {"axis": "horizontal"}}})
        add_heading("loc", "mail", "Localization")
        for i, row in enumerate(loc_rows):
            field_ids = []
            lang_id = f"loc_{i}_lang"
            add(_text(lang_id, row[0], hint="h5"), root=False)  # language as card title
            field_ids.append(lang_id)
            for j, value in enumerate(row[1:], start=1):
                label = header[j] if j < len(header) else ""
                fid = f"loc_{i}_f{j}"
                add(_text(fid, f"{label}: {value}" if label else value, hint="body"), root=False)
                field_ids.append(fid)
            col_id, card_id = f"loc_{i}_col", f"loc_{i}_card"
            add({"id": col_id, "component": {"Column": {
                "children": {"explicitList": field_ids}, "alignment": "start"}}}, root=False)
            add({"id": card_id, "component": {"Card": {"child": col_id}}})
        for k, note in enumerate(loc_notes):
            add(_text(f"loc_note_{k}", note, hint="caption"))

    # Root Card → Column listing the root-level components in order.
    add({"id": "card_col", "component": {"Column": {
        "children": {"explicitList": order.copy()},
        "distribution": "start", "alignment": "stretch"}}}, root=False)
    components.append({"id": "card", "component": {"Card": {"child": "card_col"}}})

    # Unique surface id per render — GE drops a repeated surfaceId (only the first
    # turn would show), so each card gets its own surface.
    surface_id = f"{_SURFACE_BASE}_{uuid.uuid4().hex[:8]}"
    messages = [
        {"surfaceUpdate": {"surfaceId": surface_id, "components": components}},
        {"beginRendering": {
            "surfaceId": surface_id, "root": "card",
            "styles": {"primaryColor": _THEME_PRIMARY_COLOR}}},
    ]

    # Stash for the after_agent_callback to emit as A2A DataParts.
    if tool_context is not None:
        tool_context.state[A2UI_STATE_KEY] = messages

    # Localization as a Markdown table, reordered Chinese → Spanish → Japanese
    # (reuse the parsed/ordered rows above; fall back to the raw studio markdown).
    if header and loc_rows:
        loc_md = (
            "| " + " | ".join(header) + " |\n"
            + "|" + "|".join(["---"] * len(header)) + "|\n"
            + "\n".join("| " + " | ".join(r) + " |" for r in loc_rows)
        )
        if loc_notes:
            loc_md += "\n\n" + "\n".join(loc_notes)
    else:
        loc_md = localization_markdown

    stat_md = "  ·  ".join(f"**{k}** {v}" for k, v in stats.items())
    # Split the Markdown sheet into TOP (title/tagline/World) and BOTTOM
    # (Lore/Stats/Skills/Localization) so the agent's after_agent_callback can build
    # the final response as [md_top, portrait BYTES, md_bottom] — i.e. the image
    # rendered BETWEEN "World" and "Lore". (GE renders image bytes; no bucket/URL.)
    md_top = (
        f"# {name}\n"
        f"> *{tagline}*\n"
        + (f"\n**🌍 World:** {world}\n" if world else "")
    )
    # IMPORTANT: lead md_bottom with a BLANK LINE before the first heading. GE
    # concatenates the text parts when rendering, so without it the stream is
    # "…World\n## Lore" (single newline) and GE shows "## Lore" as literal text
    # instead of a heading. The other headings already have a blank line before them.
    md_bottom = (
        f"\n\n## 📖 Lore\n{lore}\n"
        f"\n## 📊 Stats\n{stat_md}\n"
        f"\n## ✨ Skills\n{skill_lines}\n"
        f"\n## 🌐 Localization\n{loc_md}\n"
    )
    if tool_context is not None and getattr(tool_context, "state", None) is not None:
        tool_context.state["md_top"] = md_top
        tool_context.state["md_bottom"] = md_bottom

    return {"status": "ok", "name": name}
