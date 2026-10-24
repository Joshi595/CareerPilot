// CareerPilot CV engine. Python (core/render.py) decides content, order, titles and dates;
// this file only draws it. A design is a preset of the `style` values below (templates/designs/*.yaml).
#let d = json(bytes(sys.inputs.data))
#let s = d.style
#let accent = rgb(s.accent)
#let muted = rgb(s.muted)
#let side-fill = rgb(s.side_fill)

#set document(title: d.header.name + " CV", author: d.header.name)
#set page(paper: s.paper, margin: (x: s.margin_x * 1cm, y: s.margin_y * 1cm))
#set text(font: s.font, size: s.size * 1pt, lang: d.lang, hyphenate: false)
#set par(justify: false, leading: s.leading * 1em, spacing: s.leading * 1.6em)
#set list(marker: s.bullet, indent: 0.2em, body-indent: 0.5em, spacing: s.leading * 1em)
#show link: set text(fill: accent)

// **bold** in the person's own text
#let md(t) = {
  let parts = t.split("**")
  parts.enumerate().map(((i, p)) => if calc.odd(i) { strong(p) } else { p }).join()
}

#let title(t) = {
  set text(font: s.heading_font)
  if s.section_style == "rule" {
    block(above: 1.1em, below: 0.55em, sticky: true)[
      #text(fill: accent, weight: "bold", size: 1.15em, t)
      #v(-0.75em)
      #line(length: 100%, stroke: 0.6pt + accent)
    ]
  } else if s.section_style == "caps" {
    block(above: 1.2em, below: 0.55em, sticky: true, text(fill: accent, weight: "bold", tracking: 0.08em, size: 0.95em, upper(t)))
  } else if s.section_style == "bar" {
    block(above: 1.1em, below: 0.55em, sticky: true, fill: accent.lighten(85%), inset: (x: 6pt, y: 4pt), width: 100%,
      text(fill: accent, weight: "bold", size: 1.05em, t))
  } else {
    block(above: 1.1em, below: 0.45em, sticky: true, text(weight: "bold", size: 1.1em, t))
  }
}

#let dates(x) = text(fill: muted, size: 0.95em, x)

#let entry(e, show-org) = block(above: 0.75em, below: 0.4em, breakable: true)[
  // sticky: the title row and link never end a page without the entry's first lines
  #block(sticky: true)[
  #if s.date_pos == "left" {
    grid(columns: (7.2em, 1fr), column-gutter: 0.8em, dates(e.dates), [
      #strong(e.title)#if show-org and e.org != "" [, #e.org]#if e.location != "" [ #text(fill: muted)[· #e.location]]
    ])
  } else {
    grid(columns: (1fr, auto), column-gutter: 0.8em,
      [#strong(e.title)#if show-org and e.org != "" [, #e.org]#if e.location != "" [ #text(fill: muted)[· #e.location]]],
      dates(e.dates))
  }
  #if e.link != "" { text(size: 0.9em, link(e.link)) }
  ]
  #if e.bullets.len() > 0 {
    v(0.15em)
    list(..e.bullets.map(md))
  }
]

#let entries(sec) = {
  let prev = none
  for e in sec.items {
    if sec.group and e.org != "" and e.org != prev {
      block(above: 0.9em, below: 0.1em, sticky: true, text(fill: accent, weight: "bold", e.org + if e.unit != "" { " | " + e.unit } else { "" }))
    }
    entry(e, not sec.group)
    prev = e.org
  }
}

#let education(sec) = for e in sec.items [
  #block(above: 0.7em, below: 0.3em, breakable: false)[
    #grid(columns: (1fr, auto), column-gutter: 0.8em, strong(e.degree), dates(e.dates))
    #e.school#if e.location != "" [ · #e.location]
    #if e.details != "" { linebreak(); text(size: 0.95em, md(e.details)) }
  ]
]

#let items(sec, side) = for i in sec.items [
  #block(above: 0.45em, below: 0.3em)[
    #if side [
      #strong(i.name) \ #text(fill: muted, size: 0.95em)[#i.issuer#if i.issuer != "" and i.date != "" [ · ]#i.date]
    ] else {
      grid(columns: (1fr, auto), column-gutter: 0.8em,
        [#strong(i.name)#if i.issuer != "" [, #i.issuer]#if i.link != "" [ · #link(i.link)]], dates(i.date))
    }
  ]
]

#let skills(sec, side) = {
  if s.skills_style == "tags" {
    for g in sec.items {
      block(above: 0.5em, below: 0.2em, text(weight: "bold", size: 0.95em, g.cat))
      g.items.map(x => box(fill: accent.lighten(88%), inset: (x: 5pt, y: 2.5pt), radius: 3pt, text(size: 0.9em, x))).join(h(4pt))
    }
  } else if side or s.skills_style == "stacked" {
    for g in sec.items {
      block(above: 0.55em, below: 0.2em, text(weight: "bold", g.cat))
      text(size: 0.95em, g.items.join(", "))
    }
  } else {
    for g in sec.items [#block(above: 0.35em, below: 0.25em)[#strong(g.cat + ":") #g.items.join(", ")]]
  }
}

#let languages(sec, side) = {
  if s.languages_style == "dots" {
    for l in sec.items [
      #block(above: 0.4em, below: 0.2em, grid(columns: (1fr, auto), l.name,
        range(5).map(i => box(circle(radius: 2.6pt, fill: if i < l.dots { accent } else { accent.lighten(80%) }, stroke: none))).join(h(2pt))))
    ]
  } else if side {
    for l in sec.items [#block(above: 0.35em, below: 0.2em)[#strong(l.name) #if l.level != "" [ \ #text(fill: muted, l.level)]]]
  } else {
    sec.items.map(l => [#l.name#if l.level != "" [ (#l.level)]]).join(" · ")
  }
}

#let section(sec, side: false) = {
  title(sec.title)
  if sec.kind == "entries" { entries(sec) }
  else if sec.kind == "education" { education(sec) }
  else if sec.kind == "items" { items(sec, side) }
  else if sec.kind == "skills" { skills(sec, side) }
  else if sec.kind == "languages" { languages(sec, side) }
  else if sec.kind == "tags" { sec.items.join(", ") }
  else if sec.kind == "text" { md(sec.items) }
}

#let photo(size) = if d.header.photo != none and s.photo != "none" {
  box(clip: true, radius: if s.photo == "circle" { 50% } else { 4pt }, image(d.header.photo, width: size, height: size, fit: "cover"))
}

#let contact(sep) = d.header.contact.join(sep)

#let header() = {
  let h = d.header
  if s.header == "band" {
    block(width: 100%, fill: accent, inset: (x: 14pt, y: 12pt), radius: 3pt, grid(columns: (1fr, auto), column-gutter: 10pt, align: horizon, [
      #text(fill: white, size: 2.1em, weight: "bold", font: s.heading_font, h.name)
      #if h.headline != "" { linebreak(); text(fill: white.darken(8%), size: 1.1em, h.headline) }
      #v(0.2em)
      #text(fill: white, size: 0.92em, contact(" · "))
      #if h.personal.len() > 0 { linebreak(); text(fill: white, size: 0.92em, h.personal.join(" · ")) }
    ], photo(2.4cm)))
  } else if s.header == "center" {
    align(center)[
      #photo(2.4cm)
      #text(size: 2.1em, weight: "bold", fill: accent, font: s.heading_font, h.name) \
      #if h.headline != "" [#text(size: 1.1em, h.headline) \ ]
      #text(size: 0.95em, contact("  |  "))
      #if h.personal.len() > 0 [ \ #text(size: 0.95em, h.personal.join("  |  "))]
    ]
  } else {
    grid(columns: (1fr, auto), column-gutter: 12pt, align: horizon, [
      #text(size: 2.1em, weight: "bold", fill: accent, font: s.heading_font, h.name)
      #if h.headline != "" { linebreak(); text(size: 1.1em, h.headline) }
      #v(0.15em)
      #text(size: 0.95em, contact("  |  "))
      #if h.personal.len() > 0 { linebreak(); text(size: 0.95em, h.personal.join("  |  ")) }
    ], photo(2.6cm))
  }
}

#let main-secs = d.sections.filter(x => x.place == "main")
#let side-secs = d.sections.filter(x => x.place == "side")

#if s.layout == "sidebar" and side-secs.len() > 0 {
  let side = block(width: 100%, fill: side-fill, inset: 10pt, radius: 3pt)[
    #set text(size: 0.95em)
    #align(center, photo(2.8cm))
    #title(d.labels.contact)
    #d.header.contact.join(linebreak())
    #if d.header.personal.len() > 0 { linebreak(); d.header.personal.join(linebreak()) }
    #for sec in side-secs { section(sec, side: true) }
  ]
  let main = [
    #text(size: 2.2em, weight: "bold", fill: accent, font: s.heading_font, d.header.name)
    #if d.header.headline != "" { linebreak(); text(size: 1.15em, d.header.headline) }
    #for sec in main-secs { section(sec) }
  ]
  if s.side == "right" { grid(columns: (1fr, 31%), column-gutter: 14pt, main, side) }
  else { grid(columns: (31%, 1fr), column-gutter: 14pt, side, main) }
} else {
  header()
  for sec in d.sections { section(sec) }
}
