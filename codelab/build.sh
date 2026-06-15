#!/usr/bin/env bash
# Build the codelab HTML from ../guide.md  (self-contained; run from anywhere).
#   transform guide.md -> guide-codelab.md (merge Overview, swap mermaid->svg, strip code-in-italic)
#   render mermaid -> svg (cached)  ->  claat export  ->  post-process (wider/larger CSS + prompt boxes)
# Output: ./ai-game-studio-in-a-box/index.html      Serve with: ./serve.sh
set -euo pipefail

# ---- editable metadata -------------------------------------------------------
ID="game-character-designer"                 # output folder + URL slug (unique, kebab-case)
CATEGORIES="ai, gemini-enterprise, agent-platform"   # catalog grouping labels
AUTHORS="maxxh@google.com"
SUMMARY="Build the Game Character Designer demo on the Gemini Enterprise Agent Platform — multi-agent character generation with managed memory, A2A, and Model Armor."
# -----------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUIDE="$HERE/../guide.md"
CLAAT="$HERE/bin/claat"
cd "$HERE"
mkdir -p img

# claat binary (download once if missing)
if [[ ! -x "$CLAAT" ]]; then
  echo "downloading claat..."
  curl -fsSL -o "$CLAAT" https://github.com/googlecodelabs/tools/releases/latest/download/claat-linux-amd64
  chmod +x "$CLAAT"
fi

# 1) mermaid -> svg (cached; needs node/npx the first time)
if [[ ! -f img/architecture.svg || ! -f img/turn.svg ]]; then
  echo '{"args":["--no-sandbox","--disable-setuid-sandbox"]}' > pconfig.json
  npx -y @mermaid-js/mermaid-cli -i img/architecture.mmd -o img/architecture.svg -b transparent -p pconfig.json
  npx -y @mermaid-js/mermaid-cli -i img/turn.mmd        -o img/turn.svg        -b transparent -p pconfig.json
fi

# 2) transform guide.md -> guide-codelab.md
GUIDE="$GUIDE" ID="$ID" CATEGORIES="$CATEGORIES" AUTHORS="$AUTHORS" SUMMARY="$SUMMARY" python3 - <<'PY'
import re, os
src = open(os.environ['GUIDE'], encoding='utf-8').read()
lines = src.split('\n')
h1_i      = next(i for i,l in enumerate(lines) if l.startswith('# '))
contents_i= next(i for i,l in enumerate(lines) if l.strip()=='## Contents')
# headings are number-free; claat auto-numbers steps. First "## " after Contents is
# "What you build" (merged into Overview); the second begins the rest of the guide.
heads     = [i for i,l in enumerate(lines) if l.startswith('## ') and i > contents_i]
s1_i, s2_i = heads[0], heads[1]
title = lines[h1_i]
intro = lines[h1_i+1:contents_i]
while intro and (intro[-1].strip() in ('','---')): intro.pop()
overview_body = lines[s1_i+1:s2_i]
rest          = lines[s2_i:]
meta = ("summary: %s\n" % os.environ['SUMMARY']
      + "id: %s\n" % os.environ['ID']
      + "categories: %s\n" % os.environ['CATEGORIES']
      + "status: Published\n"
      + "authors: %s\n" % os.environ['AUTHORS'])
out = [meta, '', title, '', '## Overview', 'Duration: 0:03', ''] + intro + [''] + overview_body + [''] + rest
doc = '\n'.join(out)
caps = ['Architecture', 'What happens in a single turn']
imgs = ['img/architecture.svg', 'img/turn.svg']
k=[0]
def repl(m):
    i=k[0]; k[0]+=1; return f'![{caps[i]}]({imgs[i]})'
doc = re.sub(r'```mermaid.*?```', repl, doc, flags=re.S)
doc = doc.replace('*"', '"').replace('"*', '"')   # claat breaks `code` nested in *italic*
open('guide-codelab.md','w',encoding='utf-8').write(doc)
print("transform ok")
PY

# 3) claat export
rm -rf "$ID"
"$CLAAT" export guide-codelab.md >/dev/null
echo "claat export ok"

# 4) post-process index.html: wider/larger CSS + styled Antigravity prompt boxes
ID="$ID" python3 - <<'PY'
import re, os
f = os.environ['ID'] + '/index.html'
h = open(f, encoding='utf-8').read()
css = """
<style>
/* wider + larger reading column */
google-codelab-step .instructions, .instructions { max-width: 1080px !important; font-size: 16px !important; }
.codelab-title { max-width: 1080px !important; }
google-codelab-step .instructions p,
google-codelab-step .instructions li { line-height: 1.7 !important; }
google-codelab-step .instructions h2 { font-size: 26px !important; }
google-codelab-step .instructions h3 { font-size: 20px !important; }
google-codelab-step .instructions code { font-size: 0.95em !important; }
/* Antigravity prompt box */
.agy-prompt { border:1px solid #e4dcff; border-left:4px solid #7C3AED; border-radius:8px;
  background:#f7f4ff; padding:12px 16px 14px; margin:18px 0; }
.agy-head { font-weight:700; color:#6D28D9; font-size:12.5px; text-transform:uppercase;
  letter-spacing:.05em; margin-bottom:6px; }
.agy-body { color:#1f2328; }
.agy-body code { background:#ece3ff !important; }
/* callout boxes (claat ignores `>` blockquotes, so we rebuild them here) */
.cl-callout { border-left:4px solid; border-radius:8px; padding:10px 16px; margin:16px 0; }
.cl-callout p { margin:.3em 0 !important; }
.cl-callout ul { margin:.3em 0 !important; }
.cl-note { background:#E8F0FE; border-color:#185ABC; }   /* 📌 note  */
.cl-tip  { background:#E6F4EA; border-color:#137333; }   /* 💡 tip   */
.cl-warn { background:#FEF7E0; border-color:#EA8600; }   /* ⚠️ warn  */
</style>
"""
h = h.replace('</head>', css + '</head>', 1)
def box(m):
    body = m.group(1)
    # claat can split one prompt into several <p> (it breaks on inline `code` nested
    # in **bold**/*italic*). Flatten any stray paragraph tags back into one inline body.
    body = re.sub(r'</?p[^>]*>', ' ', body)
    body = re.sub(r'\s+', ' ', body).strip()
    return ('<div class="agy-prompt"><div class="agy-head">🤖 Antigravity prompt</div>'
            '<div class="agy-body">%s</div></div>' % body)
# Match from the opening 🤖 line to the prompt's CLOSING quote (across any split <p>s),
# so the whole prompt is captured even when claat fragmented it.
h, n = re.subn(r'<p>🤖 <strong>Antigravity</strong> — &#34;(.*?)&#34;\s*</p>', box, h, flags=re.S)

# Rebuild 📌/💡/⚠️ blockquote callouts (claat flattened them to plain <p>) into boxes;
# absorb an immediately-following <ul> (e.g. the "Notes" lists in the appendices).
_CLS = {"📌": "cl-note", "💡": "cl-tip", "⚠️": "cl-warn"}
def callout(m):
    emoji, body, lst = m.group(1), m.group(2), m.group(3) or ""
    return ('<div class="cl-callout %s"><p>%s%s</p>%s</div>'
            % (_CLS[emoji], emoji, body, lst))
h, c = re.subn(r'<p>(📌|💡|⚠️)(.*?)</p>(\s*<ul>.*?</ul>)?', callout, h, flags=re.S)

open(f, 'w', encoding='utf-8').write(h)
print("post-process ok: %d prompt boxes, %d callouts, css injected" % (n, c))
PY
echo "DONE -> $HERE/$ID/index.html   (serve: ./serve.sh)"
