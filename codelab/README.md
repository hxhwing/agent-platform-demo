# codelab/ — publish `guide.md` as a Google Codelab (HTML)

Converts the repo's **`../guide.md`** into a [claat](https://github.com/googlecodelabs/tools)
codelab. This is **authoring tooling only** — not part of the demo that Antigravity builds.

## Use

```bash
cd codelab
./build.sh      # transform guide.md → claat export → post-process  (output: game-character-designer/)
./serve.sh      # serve on :8080 (Cloud Shell → Web Preview, or forward the port)
```

Re-run `./build.sh` whenever `../guide.md` changes.

## What the build does (and why)

claat's Markdown dialect differs from normal Markdown, so `build.sh` adapts on the fly
(leaving `guide.md` pristine):

- **Metadata header** — claat needs `summary / id / categories / status / authors` at the
  top. Edit these at the top of `build.sh`.
  - `id` = output folder + URL slug (unique, kebab-case).
  - `categories` = catalog grouping labels (cosmetic).
- **Steps** — every `##` becomes a codelab step. The intro + "What you build" are merged
  into a single **1. Overview** step; the manual Contents is dropped (claat builds its own nav).
- **Mermaid → SVG** — claat can't render Mermaid, so the two diagrams (`img/*.mmd`) are
  pre-rendered to SVG (`img/*.svg`, cached) and swapped in.
- **`code` inside *italic*** — claat splits this onto separate lines, so the italic-quote
  wrappers around Antigravity prompts are removed (inline `code` is kept).
- **Post-process `index.html`** — injects CSS for a wider/larger reading column and wraps
  each Antigravity prompt in a styled box.

## Files

| Path | Purpose |
|---|---|
| `build.sh` | the pipeline (edit metadata at top) |
| `serve.sh` | local preview server |
| `bin/claat` | claat binary (auto-downloaded if missing) |
| `img/*.mmd` | Mermaid diagram sources |
| `img/*.svg` | rendered diagrams (cached; delete to re-render) |
| `game-character-designer/` | build output (regenerated) |

Diagrams render via `npx @mermaid-js/mermaid-cli` (needs `node`/`npx` the first time).
