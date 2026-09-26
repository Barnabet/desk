// Desk document styles for markup-ebooks (mk_convert.py, mk_render.py): article, report and book.
// Used through templates/pandoc.typ for converted input, or directly from your own .typ file:
//   #import "markup.typ": desk-markup
//   #show: desk-markup.with(style: "report", title: [Annual review], authors: ([Ann Lee],), toc: true)
// Every parameter is optional; see references/templates.md.

#let _nonempty(x) = x != none and x != "" and x != [] and x != ()

// Plain text of content (PDF metadata, placeholders).
#let _plain(c) = {
  if c == none { return "" }
  if type(c) == str { return c }
  if type(c) != content { return str(c) }
  if c.has("text") { return if type(c.text) == str { c.text } else { _plain(c.text) } }
  if c.has("children") { return c.children.map(_plain).join("") }
  if c.has("body") { return _plain(c.body) }
  if c == [ ] { return " " }
  ""
}

#let _chapter-word = (
  en: "Chapter", fr: "Chapitre", de: "Kapitel", es: "Capítulo", it: "Capitolo", pt: "Capítulo",
  nl: "Hoofdstuk", sv: "Kapitel", da: "Kapitel", nb: "Kapittel", fi: "Luku", pl: "Rozdział", cs: "Kapitola",
  ru: "Глава", uk: "Розділ", tr: "Bölüm", el: "Κεφάλαιο", ja: "章", zh: "章", ko: "장",
)

#let _sans = ("Inter", "Source Sans 3", "Helvetica Neue", "Segoe UI", "Helvetica", "Arial", "Liberation Sans", "DejaVu Sans", "Libertinus Serif")

// Text of the last heading of `level` that starts on or before this page (for running heads). Headings left out of
// the outline (the "Contents" title itself) never name a page.
#let _current-heading(level) = {
  let here-page = here().page()
  let on-page = query(heading.where(level: level, outlined: true)).filter(h => h.location().page() == here-page)
  if on-page.len() > 0 { return on-page.first() }
  let before = query(heading.where(level: level, outlined: true).before(here()))
  if before.len() > 0 { before.last() } else { none }
}

// True on the page where the main matter starts. Its page counter update comes after the running head in the page,
// so the head would still show the old number: these markers tell the head that the numbering restarts here.
#let _starts-here(marker) = query(marker).any(m => m.location().page() == here().page())

#let _fill(s, title-text, author-text, date-text) = context {
  let n = if _starts-here(<desk-main-start>) { 1 } else { counter(page).get().first() }
  let total = counter(page).final().first()
  let sec = _current-heading(1)
  let sec-text = if sec != none { _plain(sec.body) } else { "" }
  s.replace("{page}", str(n)).replace("{pages}", str(total)).replace("{title}", title-text)
    .replace("{section}", sec-text).replace("{chapter}", sec-text).replace("{author}", author-text).replace("{date}", date-text)
}

#let desk-markup(
  style: "article",
  title: none,
  subtitle: none,
  authors: (),
  date: none,
  abstract: none,
  abstract-title: none,
  keywords: (),
  publisher: none,
  rights: none,
  cover: none,
  toc: false,
  toc-depth: 3,
  lof: false,
  lot: false,
  number-sections: false,
  paper: none,
  page-width: none,
  page-height: none,
  margin: none,
  columns: 1,
  font: none,
  heading-font: none,
  code-font: none,
  fontsize: none,
  linestretch: 1.0,
  lang: "en",
  region: none,
  header: none,
  footer: none,
  accent: rgb("#1f4e79"),
  justify: true,
  doc,
) = {
  let is-book = style == "book"
  let is-report = style == "report"
  let body-fonts = if font != none { (font, "Libertinus Serif") } else { ("Libertinus Serif",) }
  let head-fonts = if heading-font != none { (heading-font, .._sans) } else { _sans }
  let mono-fonts = if code-font != none { (code-font, "DejaVu Sans Mono") } else { ("DejaVu Sans Mono",) }
  let size = if fontsize != none { fontsize } else if is-book { 10.5pt } else { 11pt }
  let authors = if authors == none { () } else if type(authors) == array { authors } else { (authors,) }
  let author-text = authors.map(_plain).join(", ")
  let title-text = _plain(title)
  let date-text = _plain(date)

  set document(title: if title != none { title-text } else { none })
  set document(author: authors.map(_plain)) if authors.len() > 0
  set document(keywords: keywords.map(_plain)) if keywords.len() > 0
  set text(font: body-fonts, size: size, lang: lang)
  set text(region: region) if region != none
  set par(justify: justify, leading: 0.62em * linestretch, spacing: if is-book { 0.62em * linestretch } else { 1.0em })
  set par(first-line-indent: if is-book { (amount: 1.2em, all: false) } else { 0pt })

  // Page geometry: A4 article/report, A5 book with inside/outside margins, or an explicit size.
  let default-margin = if is-book { (inside: 2.1cm, outside: 1.6cm, top: 2.0cm, bottom: 2.2cm) } else if columns > 1 { (x: 1.7cm, y: 2.2cm) } else { (x: 2.3cm, y: 2.5cm) }
  if page-width != none {
    set page(width: page-width, height: page-height)
  }
  set page(paper: if paper != none { paper } else if is-book { "a5" } else { "a4" }) if page-width == none
  set page(margin: if margin != none { margin } else { default-margin }, columns: columns)

  // State: front matter pages are numbered i, ii, …; the main matter restarts at 1.
  let front = state("desk-front", false)

  let num-now() = {
    if _starts-here(<desk-main-start>) { return "1" }
    if _starts-here(<desk-front-start>) { return "i" }
    let n = counter(page).get().first()
    if front.get() { numbering("i", n) } else { str(n) }
  }
  let in-front() = front.get() and not _starts-here(<desk-main-start>)
  // The blank page a book may need so that page 1 is a right-hand page (between the gap and main-start markers).
  let blank-page() = {
    let gap = query(<desk-gap>)
    let main = query(<desk-main-start>)
    gap.len() > 0 and main.len() > 0 and gap.first().location().page() <= here().page() and here().page() < main.first().location().page()
  }
  let opening-page() = {
    let p = here().page()
    query(heading.where(level: 1)).any(h => h.location().page() == p)
  }
  let head = context {
    set text(size: 0.78em, fill: luma(105), font: head-fonts)
    if blank-page() { return }
    if header != none {
      if header == "" { return }
      _fill(header, title-text, author-text, date-text)
      return
    }
    if in-front() { return }
    if (is-book or is-report) and opening-page() { return }
    let sec = _current-heading(1)
    let sec-body = if sec != none { sec.body } else { none }
    if is-book {
      if calc.even(here().page()) {
        num-now(); h(1.2em); if title != none { smallcaps(title) }
      } else {
        h(1fr); if sec-body != none { sec-body }; h(1.2em); num-now()
      }
    } else {
      if here().page() == 1 { return }
      if title != none { title } else { none }
      h(1fr)
      if sec-body != none and columns == 1 { sec-body }
      v(-5pt)
      line(length: 100%, stroke: 0.4pt + luma(200))
    }
  }
  let foot = context {
    set text(size: 0.8em, fill: luma(105), font: head-fonts)
    if blank-page() { return }
    if footer != none {
      if footer == "" { return }
      align(center, _fill(footer, title-text, author-text, date-text))
      return
    }
    if is-book {
      // Page numbers sit in the running head; chapter openers show theirs at the foot.
      if opening-page() { align(center, num-now()) }
      if in-front() and not opening-page() { align(center, num-now()) }
      return
    }
    if in-front() { align(center, num-now()); return }
    let total = counter(page).final().first()
    if is-report { align(right)[#num-now() / #total] } else { align(center, num-now()) }
  }
  set page(header: head, footer: foot)

  // Headings.
  set heading(numbering: if number-sections { "1.1.1" } else { none })
  show heading: set text(font: head-fonts, weight: "bold", hyphenate: false)
  show heading: set par(justify: false, first-line-indent: 0pt)
  show heading: set block(above: 1.5em, below: 0.8em, sticky: true)
  show heading.where(level: 1): set text(size: 1.5em, fill: accent)
  show heading.where(level: 2): set text(size: 1.22em, fill: accent.darken(15%))
  show heading.where(level: 3): set text(size: 1.07em)
  show heading.where(level: 4): set text(size: 1em, style: "italic")
  show heading.where(level: 5): set text(size: 1em, weight: "regular", style: "italic")
  let word = _chapter-word.at(lang, default: "Chapter")
  show heading.where(level: 1): it => {
    if is-book {
      pagebreak(weak: true)
      v(12%)
      if it.numbering != none {
        text(font: head-fonts, size: 0.95em, fill: accent, tracking: 0.08em, upper(word + " " + counter(heading).display("1")))
        v(0.5em)
      }
      block(below: 2.4em, text(font: head-fonts, size: 1.9em, weight: "bold", fill: luma(20), hyphenate: false, it.body))
    } else if is-report {
      pagebreak(weak: true)
      block(above: 0pt, below: 1.2em, width: 100%, stroke: (bottom: 0.8pt + accent.lighten(40%)), inset: (bottom: 0.45em))[
        #text(font: head-fonts, size: 1.55em, weight: "bold", fill: accent, hyphenate: false)[
          #if it.numbering != none { counter(heading).display(it.numbering); h(0.6em) }#it.body
        ]
      ]
    } else {
      it
    }
  }

  // Body elements: lists, code, quotes, tables, figures, links, notes, math.
  set list(indent: 0.4em, body-indent: 0.55em, marker: ([•], [‣], [–]))
  set enum(indent: 0.4em, body-indent: 0.55em)
  set terms(indent: 0pt, hanging-indent: 1.5em)
  show raw: set text(font: mono-fonts, size: 0.86em)
  show raw.where(block: true): set par(justify: false)
  show raw.where(block: true): it => block(width: 100%, fill: luma(246), stroke: (left: 2.5pt + accent.lighten(55%)), inset: (x: 9pt, y: 7pt), radius: (right: 3pt), breakable: true, it)
  show raw.where(block: false): box.with(fill: luma(240), inset: (x: 2.5pt), outset: (y: 2.5pt), radius: 2pt)
  show quote.where(block: true): it => block(stroke: (left: 2pt + luma(190)), inset: (left: 12pt, y: 3pt), above: 1em, below: 1em, text(fill: luma(55), style: "italic", it.body) + if it.attribution != none { align(right, text(size: 0.9em, [— #it.attribution])) })
  set table(
    stroke: (x, y) => (
      top: if y == 0 { 1pt + luma(60) } else { 0pt },
      bottom: 0.4pt + luma(215),
    ),
    fill: (x, y) => if y == 0 { accent.lighten(90%) } else if calc.even(y) { luma(250) } else { none },
    inset: (x: 6pt, y: 4.5pt),
  )
  show table: set text(size: 0.92em)
  show table: set par(justify: false)
  show table.cell.where(y: 0): set text(weight: "bold")
  show figure: set block(breakable: true, above: 1.2em, below: 1.2em)
  show figure.caption: set text(size: 0.88em, fill: luma(70))
  show figure.caption: it => [#text(weight: "bold", fill: luma(50))[#it.supplement #context it.counter.display(it.numbering)#it.separator]#it.body]
  show figure.where(kind: table): set figure.caption(position: top)
  show image: it => align(center, it)
  show link: set text(fill: accent.darken(5%))
  show footnote.entry: set text(size: 0.84em)
  show footnote.entry: set par(justify: false)
  set footnote.entry(separator: line(length: 30%, stroke: 0.5pt + luma(160)))
  show math.equation: set text(font: ("New Computer Modern Math",))
  show outline.entry.where(level: 1): set text(weight: "bold")
  show outline.entry.where(level: 1): set block(above: 0.9em)
  set outline(indent: auto)

  let title-block() = {
    block(width: 100%, below: 1.4em)[
      #text(font: head-fonts, size: 2.0em, weight: "bold", fill: luma(15), hyphenate: false)[#title]
      #if _nonempty(subtitle) { v(0.25em); text(font: head-fonts, size: 1.25em, fill: luma(80), hyphenate: false)[#subtitle] }
      #if author-text != "" or date != none {
        v(0.5em)
        set text(size: 0.98em, fill: luma(60))
        if author-text != "" { authors.join(", ") }
        if author-text != "" and date != none { h(0.6em); [·]; h(0.6em) }
        if date != none { date }
      }
      #v(0.35em)
      #line(length: 100%, stroke: 1.2pt + accent)
    ]
    if _nonempty(abstract) {
      block(width: 100%, inset: (x: 1.2em, y: 0.2em), below: 1.6em)[
        #set par(justify: true)
        #set text(size: 0.93em)
        #text(font: head-fonts, weight: "bold", fill: accent)[#if abstract-title != none { abstract-title } else { [Abstract] }]
        #h(0.6em) #abstract
      ]
    }
  }

  let title-page() = page(header: none, footer: none, columns: 1, margin: if is-book { (x: 1.8cm, y: 2.4cm) } else { (x: 2.5cm, y: 3cm) })[
    #set par(first-line-indent: 0pt, justify: false)
    #if is-book {
      v(18%)
      align(center)[
        #text(font: head-fonts, size: 2.3em, weight: "bold", fill: luma(15), hyphenate: false)[#title]
        #if _nonempty(subtitle) { v(0.6em); text(font: head-fonts, size: 1.3em, fill: luma(80))[#subtitle] }
        #v(1fr)
        #if author-text != "" { text(size: 1.25em)[#authors.join(", ")] }
        #v(2em)
        #if _nonempty(publisher) { text(font: head-fonts, size: 0.95em, fill: luma(90), upper(publisher)) }
      ]
      v(6%)
    } else {
      v(1fr)
      block(width: 100%, stroke: (left: 5pt + accent), inset: (left: 18pt, y: 10pt))[
        #text(font: head-fonts, size: 2.4em, weight: "bold", fill: accent, hyphenate: false)[#title]
        #if _nonempty(subtitle) { v(0.4em); text(font: head-fonts, size: 1.35em, fill: luma(80))[#subtitle] }
      ]
      v(26pt)
      if author-text != "" { text(size: 1.15em)[#authors.join(", ")]; linebreak() }
      if date != none { text(fill: luma(90))[#date] }
      v(2fr)
      if _nonempty(abstract) {
        block(width: 100%, inset: (x: 10pt, y: 11pt), fill: luma(247), radius: 3pt)[
          #set par(justify: true)
          #text(font: head-fonts, weight: "bold", fill: accent)[#if abstract-title != none { abstract-title } else { [Abstract] }] \
          #abstract
        ]
      }
      v(0.4fr)
    }
  ]

  // Cover (book), title, front matter.
  if cover != none {
    page(header: none, footer: none, margin: 0pt, columns: 1, image(cover, width: 100%, height: 100%, fit: "contain"))
  }
  if (is-book or is-report) and title != none {
    title-page()
    if is-book and (_nonempty(rights) or _nonempty(publisher) or date != none) {
      page(header: none, footer: none, columns: 1)[
        #set par(first-line-indent: 0pt, justify: false)
        #v(1fr)
        #set text(size: 0.85em, fill: luma(70))
        #if title != none [#title \ ]
        #if author-text != "" [#authors.join(", ") \ ]
        #if date != none [#date \ ]
        #if _nonempty(publisher) [#publisher \ ]
        #if _nonempty(rights) { v(0.6em); rights }
      ]
    }
    if is-book and _nonempty(abstract) {
      page(header: none, footer: none, columns: 1)[#set par(first-line-indent: 0pt); #v(20%) #align(center, block(width: 85%, align(left, abstract)))]
    }
  } else if title != none {
    if columns > 1 {
      place(top, float: true, scope: "parent", clearance: 1.2em, title-block())
    } else {
      title-block()
    }
  }
  if toc or lof or lot {
    if is-book or is-report {
      front.update(true)
      counter(page).update(1)
      [#metadata("front")<desk-front-start>]
    }
    if toc { outline(depth: toc-depth) }
    if lof { v(1em); outline(title: auto, target: figure.where(kind: image)) }
    if lot { v(1em); outline(title: auto, target: figure.where(kind: table)) }
    if is-book or is-report {
      pagebreak(weak: true)
      if is-book {
        // A book's page 1 is a right-hand page: the blank left-hand page this may add has no head or foot.
        [#metadata("gap")<desk-gap>]
        pagebreak(weak: true, to: "odd")
      }
      front.update(false)
      counter(page).update(1)
      [#metadata("main")<desk-main-start>]
    } else {
      v(1em)
    }
  } else if (is-book or is-report) and title != none {
    if is-book {
      [#metadata("gap")<desk-gap>]
      pagebreak(weak: true, to: "odd")
    }
    counter(page).update(1)
    [#metadata("main")<desk-main-start>]
  }

  doc
}
