// Desk document styles for pdf_create.py: report, memo, letter and plain.
// Used through templates/pandoc.typ (Markdown/HTML input) or directly from your own .typ file:
//   #import "desk.typ": desk-doc
//   #show: desk-doc.with(style: "report", title: [My report], authors: ([Ann],), toc: true)

#let _nonempty(x) = x != none and x != "" and x != [] and x != ()

#let _placeholders(s, title-text) = context {
  let n = counter(page).get().first()
  let total = counter(page).final().first()
  s.replace("{page}", str(n)).replace("{pages}", str(total)).replace("{title}", title-text)
}

#let _lines(x) = if type(x) == array { x.join(linebreak()) } else { x }

// Plain text of content (for PDF metadata).
#let _plain(c) = {
  if c == none { return "" }
  if type(c) == str { return c }
  if type(c) != content { return str(c) }
  if c.has("text") { return c.text }
  if c.has("children") { return c.children.map(_plain).join("") }
  if c.has("body") { return _plain(c.body) }
  if c == [ ] { return " " }
  ""
}

#let desk-doc(
  style: "report",
  title: none,
  subtitle: none,
  authors: (),
  date: none,
  abstract: none,
  title-text: "",
  toc: false,
  toc-depth: 3,
  number-sections: false,
  paper: "a4",
  page-width: none,
  page-height: none,
  margin: (x: 2.2cm, y: 2.4cm),
  font: none,
  fontsize: 11pt,
  lang: "en",
  header: none,
  footer: none,
  accent: rgb("#1f4e79"),
  justify: true,
  // memo
  to: none,
  from: none,
  cc: none,
  subject: none,
  // letter
  sender: none,
  recipient: none,
  place: none,
  closing: none,
  signature: none,
  doc,
) = {
  let body-fonts = if font != none { (font, "Libertinus Serif") } else { ("Libertinus Serif",) }
  let mono-fonts = ("DejaVu Sans Mono",)
  let author-text = if authors.len() > 0 { authors.map(a => if type(a) == str { a } else { a }).join(", ") } else { none }

  // "today" (front matter or --date) becomes today's date, e.g. "25 September 2026".
  if date != none and lower(_plain(date).trim()) == "today" {
    date = datetime.today().display("[day padding:none] [month repr:long] [year]")
  }
  let meta-title = if title-text != "" { title-text } else if title != none { _plain(title) } else if _nonempty(subject) { _plain(subject) } else { none }
  if title-text == "" and meta-title != none { title-text = meta-title }
  set document(title: meta-title)
  set document(author: authors.map(_plain)) if authors.len() > 0
  set text(font: body-fonts, size: fontsize, lang: lang)
  set par(justify: justify, leading: 0.62em, spacing: 0.95em)
  if page-width != none {
    set page(width: page-width, height: page-height)
  }
  set page(paper: paper) if page-width == none
  set page(margin: margin)

  // Running header and footer: none on the first page of a report/letter; custom strings take {page}, {pages}, {title}.
  let first-page-plain = style in ("report", "letter")
  let head = context {
    let n = counter(page).get().first()
    if n == 1 and first-page-plain { return }
    set text(8.5pt, fill: luma(100))
    if header != none {
      _placeholders(header, title-text)
    } else if style == "report" and title != none {
      title
      h(1fr)
      if date != none { date }
      v(-6pt)
      line(length: 100%, stroke: 0.4pt + luma(190))
    }
  }
  let foot = context {
    let n = counter(page).get().first()
    let total = counter(page).final().first()
    if n == 1 and style == "letter" { return }
    if n == 1 and style == "report" and title != none and footer == none { return }
    set text(8.5pt, fill: luma(100))
    if footer != none {
      align(center, _placeholders(footer, title-text))
    } else if style == "letter" {
      align(right)[#n / #total]
    } else {
      align(center)[#n / #total]
    }
  }
  set page(header: head, footer: foot)

  // Headings, lists, code, quotes, tables, figures, links.
  set heading(numbering: if number-sections { "1.1.1" } else { none })
  show heading: set text(fill: accent, weight: "bold")
  show heading.where(level: 1): set text(size: 1.45em)
  show heading.where(level: 2): set text(size: 1.2em)
  show heading.where(level: 3): set text(size: 1.05em)
  show heading: set block(above: 1.4em, below: 0.75em)
  show heading: set par(justify: false)
  show heading: set text(hyphenate: false)
  set list(indent: 0.6em, body-indent: 0.5em)
  set enum(indent: 0.6em, body-indent: 0.5em)
  show raw: set text(font: mono-fonts, size: 0.88em)
  show raw.where(block: true): it => block(fill: luma(246), stroke: 0.5pt + luma(225), inset: 8pt, radius: 3pt, width: 100%, it)
  show raw.where(block: false): box.with(fill: luma(242), inset: (x: 2.5pt), outset: (y: 2.5pt), radius: 2pt)
  show quote.where(block: true): it => block(stroke: (left: 2.5pt + accent.lighten(60%)), inset: (left: 11pt, y: 3pt), text(fill: luma(60), it.body))
  set table(
    stroke: (x, y) => (top: if y == 0 { 0.9pt + luma(110) } else if y == 1 { 0.7pt + luma(150) } else { 0.35pt + luma(210) }, bottom: 0.9pt + luma(110)),
    fill: (x, y) => if y == 0 { accent.lighten(88%) } else if calc.even(y) { luma(248) } else { none },
    inset: (x: 6pt, y: 4.5pt),
  )
  show table.cell.where(y: 0): set text(weight: "bold")
  // Cells: ragged right and no hyphenation (justified cells gape and split words), and columns without an explicit
  // alignment start at the left even inside pandoc's centered figure.
  show table.cell: set par(justify: false)
  show table.cell: set text(hyphenate: false)
  show table.cell: set align(start)
  show figure.caption: set text(size: 0.88em, fill: luma(70))
  show figure: set block(breakable: true)
  show link: set text(fill: accent.darken(10%))
  show footnote.entry: set text(size: 0.85em)
  set math.equation(numbering: none)
  show math.equation: set text(font: ("New Computer Modern Math",))

  // Front matter by style.
  if style == "report" and title != none {
    page(header: none, footer: none)[
      #v(1fr)
      #set par(justify: false)
      #block(width: 100%, stroke: (left: 4pt + accent), inset: (left: 16pt, y: 8pt))[
        #text(28pt, weight: "bold", fill: accent, hyphenate: false)[#title]
        #if _nonempty(subtitle) { v(6pt); text(15pt, fill: luma(80))[#subtitle] }
      ]
      #v(22pt)
      #if author-text != none { text(12.5pt)[#author-text]; linebreak() }
      #if date != none { text(11pt, fill: luma(90))[#date] }
      #v(2fr)
      #if _nonempty(abstract) {
        block(width: 100%, inset: (x: 8pt, y: 10pt), fill: luma(247), radius: 3pt)[
          #text(weight: "bold", fill: accent)[Abstract] \
          #abstract
        ]
      }
      #v(0.5fr)
    ]
    if toc {
      outline(title: [Contents], depth: toc-depth, indent: auto)
      pagebreak(weak: true)
    }
  } else if style == "memo" {
    block(below: 14pt)[
      #set par(justify: false)
      #text(22pt, weight: "bold", fill: accent)[Memorandum]
      #v(6pt)
      #let rows = (("To", to), ("From", from), ("CC", cc), ("Date", date), ("Subject", if _nonempty(subject) { subject } else { title })).filter(p => _nonempty(p.at(1)))
      #grid(columns: (auto, 1fr), column-gutter: 14pt, row-gutter: 7pt, ..rows.map(p => (text(weight: "bold")[#p.at(0):], _lines(p.at(1)))).flatten())
      #v(4pt)
      #line(length: 100%, stroke: 1.2pt + accent)
    ]
    if toc {
      outline(title: [Contents], depth: toc-depth, indent: auto)
    }
  } else if style == "letter" {
    if _nonempty(sender) {
      align(right, block(width: 55%, align(left, text(10pt)[#_lines(sender)])))
      v(12pt)
    }
    if _nonempty(recipient) {
      block(width: 60%, _lines(recipient))
      v(14pt)
    }
    if date != none or _nonempty(place) {
      align(right)[#if _nonempty(place) [#place, ] #date]
      v(10pt)
    }
    let subj = if _nonempty(subject) { subject } else { title }
    if subj != none {
      text(weight: "bold")[#subj]
      v(6pt)
    }
  } else if title != none {
    align(center, block(below: 16pt)[
      #set par(justify: false)
      #text(20pt, weight: "bold", fill: accent, hyphenate: false)[#title]
      #if _nonempty(subtitle) { linebreak(); text(13pt, fill: luma(80))[#subtitle] }
      #if author-text != none { v(4pt); text(11pt)[#author-text] }
      #if date != none { linebreak(); text(10pt, fill: luma(90))[#date] }
    ])
    if _nonempty(abstract) {
      block(inset: (x: 2em, bottom: 10pt))[#text(weight: "bold")[Abstract.] #abstract]
    }
    if toc {
      outline(title: [Contents], depth: toc-depth, indent: auto)
    }
  } else if toc {
    outline(title: [Contents], depth: toc-depth, indent: auto)
  }

  doc

  if style == "letter" and (_nonempty(closing) or _nonempty(signature)) {
    v(14pt)
    block(breakable: false)[
      #if _nonempty(closing) { closing }
      #v(2.6em)
      #if _nonempty(signature) { _lines(signature) }
    ]
  }
}
