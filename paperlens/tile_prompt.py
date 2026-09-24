"""The house style for a dashboard's tile image, and the prompt that asks a model for one.

A dashboard is listed on ``/dashboards`` as a picture tile. The picture should say what the
dashboard is about at a glance and should look like it belongs beside the others, which is what
this module is for: one style, written once, shared by the tiles Metalens generates and by the
tiles their authors generate themselves.

The style is deliberately narrow. Tiles sit next to each other on one page, so anything
photographic, three-dimensional or heavily textured breaks the row; and the card already prints
the title, the kicker and the description under the picture, so words inside the picture are
noise — which is why the style forbids them.

``build()`` turns a dashboard's own subject into the prompt. Nothing here calls a model: the
prompt is also handed to authors who publish outside Metalens (it travels in a release's
``tile-prompt.md``), so that a page on someone else's site can carry a tile of the same family.
"""
from __future__ import annotations

import re

# 1200 x 630 is what link previews and the Dashboards page expect.
SIZE = (1200, 630)

STYLE = """\
Style — follow every point:
- A flat vector editorial illustration, in the manner of a broadsheet newspaper's science section.
- Geometric and diagrammatic: circles, arcs, lines, dots, simple silhouettes. No perspective, no
  three-dimensional rendering, no drop shadows, no glossy or metallic surfaces.
- Palette, and no other colours: deep navy #122740 and slate blue #1b485e for the drawing; teal
  #367380 and pale teal #9cbcc4 for supporting shapes; a single warm orange #eb6834 used sparingly,
  for the one element that matters most; a near-white background, #f5f5f5 to #ffffff.
- Calm and sparse. Generous empty space. A single clear idea, legible when the image is only
  300 pixels wide.
- Absolutely no text, letters, numbers, labels, captions, watermarks or logos anywhere in the image.
- No photorealism, no stock-photo people, no faces, no robots, no glowing neural networks, no
  circuit boards, no brains — none of the usual visual clichés for artificial intelligence.
"""


def _subject(title: str, description: str = "", keywords: list[str] | None = None) -> str:
    """The one or two sentences the picture is about, from what the dashboard says about itself."""
    bits = [re.sub(r"\s+", " ", (title or "").strip())]
    desc = re.sub(r"\s+", " ", (description or "").strip())
    if desc:
        bits.append(desc if len(desc) <= 300 else desc[:297].rsplit(" ", 1)[0] + "…")
    kw = [re.sub(r"\s+", " ", k).strip() for k in (keywords or []) if isinstance(k, str) and k.strip()]
    if kw:
        bits.append("Themes: " + ", ".join(kw[:7]) + ".")
    return " ".join(b for b in bits if b)


def build(title: str, description: str = "", keywords: list[str] | None = None) -> str:
    """The full prompt for one tile: what to draw, then the house style."""
    return (
        f"Draw a {SIZE[0]}x{SIZE[1]} illustration for the cover of a research dashboard.\n\n"
        f"Subject — illustrate the idea, not the words: {_subject(title, description, keywords)}\n\n"
        "Find one concrete visual metaphor for that subject and draw only it. Do not try to depict "
        "every theme listed; do not draw a chart of invented data, and do not imply a finding the "
        "dashboard may not support.\n\n"
        f"{STYLE}"
    )


def doc(title: str, description: str = "", keywords: list[str] | None = None) -> str:
    """``tile-prompt.md`` for a release export: the prompt, and what to do with the picture."""
    return f"""# A tile image for your dashboard

Metalens lists a dashboard on its [Dashboards page](/dashboards) as a picture tile. If your page
publishes one, that picture is used; otherwise the tile is left plain.

Below is a prompt for generating one in the house style, so your tile sits comfortably beside the
others. Paste it into an image model of your choice, generate at **{SIZE[0]}x{SIZE[1]}**, and save
the result next to your page — then name it in your `metalens.json`:

```json
{{ "preview": "preview.png" }}
```

You can also supply an image directly when you register the dashboard, which takes precedence over
the manifest. Either way the picture is decorative: it is not evidence, and it should not state a
result.

## The prompt

```
{build(title, description, keywords)}
```

## If you would rather draw it from your data

A tile drawn from the dashboard's own figures is usually better than a generated one, because it is
specific to the page. Render one of your figures at {SIZE[0]}x{SIZE[1]} with no title, no legend and
no axis labels — the tile prints the title and description underneath, so words inside the picture
only repeat them.
"""
