# CV designs

There is one Typst engine (`jobquill/templates/engine/cv.typ` and `letter.typ`). A design is a YAML preset of its
building blocks in `jobquill/templates/designs/<name>.yaml`:

```yaml
name: My design
family: Modern            # Traditional | Modern | Compact | Specialist | Simple
description: One sentence people see in the gallery.
style: {font: Lato, heading_font: Lato, accent: "#2F5BD3", section_style: caps, header: band, layout: single}
order: [summary, experience, education, skills, languages]   # optional
sidebar: [skills, languages]                                 # optional, with layout: sidebar
pages: 1                                                     # optional page limit
```

Style keys and values: `layout` single | sidebar, `side` left | right, `header` left | center | band,
`section_style` rule | caps | bar | plain, `date_pos` right | left, `skills_style` lines | tags | stacked,
`languages_style` text | dots, `photo` circle | square | none, `font`/`heading_font` (bundled: Libertinus Serif,
New Computer Modern, Lato, Fira Sans), `size`, `leading`, `margin_x`, `margin_y`, `accent`, `muted`, `side_fill`,
`bullet`, `group` (group jobs by employer).

People change any of these in the designer and save their own "looks"; those stay in their data folder.

To add a design: add the YAML file and run `uv run pytest tests/test_designs.py` (every design is rendered for two
example people). Fonts must be OFL-licensed; add new ones to `jobquill/templates/fonts/` with their licence.
