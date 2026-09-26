// Pandoc template used by pdf_create.py: pandoc's metadata and desk-* variables become a call to desk-doc.
#import "desk.typ": desk-doc

#let horizontalrule = line(start: (25%, 0%), end: (75%, 0%), stroke: 0.5pt + luma(160))

#show terms.item: it => block(breakable: false)[
  #text(weight: "bold")[#it.term]
  #block(inset: (left: 1.5em, top: -0.4em))[#it.description]
]

#show figure.where(kind: table): set figure.caption(position: top)
#show figure.where(kind: image): set figure.caption(position: bottom)

$if(highlighting-definitions)$
$highlighting-definitions$

$endif$
$for(header-includes)$
$header-includes$

$endfor$
#show: doc => desk-doc(
  style: "$desk-style$",
$if(title)$
  title: [$title$],
$endif$
$if(desk-title-text)$
  title-text: $desk-title-text$,
$endif$
$if(subtitle)$
  subtitle: [$subtitle$],
$endif$
$if(author)$
  authors: ($for(author)$[$author$],$endfor$),
$endif$
$if(date)$
  date: [$date$],
$endif$
$if(abstract)$
  abstract: [$abstract$],
$endif$
$if(to)$
  to: [$for(to)$$to$$sep$ \ $endfor$],
$endif$
$if(from)$
  from: [$for(from)$$from$$sep$ \ $endfor$],
$endif$
$if(cc)$
  cc: [$for(cc)$$cc$$sep$ \ $endfor$],
$endif$
$if(subject)$
  subject: [$subject$],
$endif$
$if(sender)$
  sender: [$for(sender)$$sender$$sep$ \ $endfor$],
$endif$
$if(recipient)$
  recipient: [$for(recipient)$$recipient$$sep$ \ $endfor$],
$endif$
$if(place)$
  place: [$place$],
$endif$
$if(closing)$
  closing: [$closing$],
$endif$
$if(signature)$
  signature: [$for(signature)$$signature$$sep$ \ $endfor$],
$endif$
  toc: $if(desk-toc)$true$else$false$endif$,
  toc-depth: $if(desk-toc-depth)$$desk-toc-depth$$else$3$endif$,
  number-sections: $if(desk-number)$true$else$false$endif$,
$if(desk-paper)$
  paper: "$desk-paper$",
$endif$
$if(desk-page-width)$
  page-width: $desk-page-width$,
  page-height: $desk-page-height$,
$endif$
$if(desk-margin)$
  margin: $desk-margin$,
$endif$
$if(desk-font)$
  font: $desk-font$,
$endif$
$if(desk-fontsize)$
  fontsize: $desk-fontsize$,
$endif$
$if(desk-lang)$
  lang: "$desk-lang$",
$endif$
$if(desk-header)$
  header: $desk-header$,
$endif$
$if(desk-footer)$
  footer: $desk-footer$,
$endif$
$if(desk-accent)$
  accent: rgb("$desk-accent$"),
$endif$
$if(desk-nojustify)$
  justify: false,
$endif$
  doc,
)

$for(include-before)$
$include-before$

$endfor$
$body$
$for(include-after)$

$include-after$
$endfor$
