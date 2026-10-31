// JobGambit cover letter. Letterhead follows the CV design (same style values); layout: modern | formal.
#let d = json(bytes(sys.inputs.data))
#let s = d.style
#let accent = rgb(s.accent)

#set document(title: d.name + " Cover Letter", author: d.name)
#let k = d.at("fit", default: 1)  // < 1 shrinks text, margins and gaps so a long letter still fits one page (render.letter_pdf)
#set page(paper: s.paper, margin: if d.layout == "formal" { (left: 2.5cm * k, right: 2cm * k, top: 1.8cm * k, bottom: 1.8cm * k) } else { (x: 2.2cm * k, y: 1.8cm * k) })
#set text(font: s.font, size: (s.size + 0.5) * k * 1pt, lang: d.lang, hyphenate: false)
#set par(justify: false, leading: 0.62em * k, spacing: 1.05em * k)

#let md(t) = t.split("**").enumerate().map(((i, p)) => if calc.odd(i) { strong(p) } else { p }).join()

#if s.header == "band" {
  block(width: 100%, fill: accent, inset: (x: 12pt, y: 10pt), radius: 3pt)[
    #text(fill: white, size: 1.7em, weight: "bold", font: s.heading_font, d.name) \
    #text(fill: white, size: 0.92em, d.contact.join(" · "))
  ]
} else {
  text(size: 1.7em, weight: "bold", fill: accent, font: s.heading_font, d.name)
  linebreak()
  text(size: 0.92em, d.contact.join("  |  "))
  v(-0.4em)
  line(length: 100%, stroke: 0.6pt + accent)
}

#v(1em)
#if d.recipient.len() > 0 { d.recipient.join(linebreak()) }
#v(0.6em)
#align(right, d.place_date)
#v(0.6em)
#strong(d.subject)
#v(0.4em)
#d.salutation

#for p in d.paragraphs [#md(p)

]
#block(breakable: false)[
  #d.closing
  #v(1.2em)
  #if d.signature != none { image(d.signature, height: 1.2cm) }
  #d.name
]
