// Pandoc template for mk_convert.py and mk_render.py: pandoc's metadata and desk-* variables become a call
// to desk-markup (templates/markup.typ). Pass your own with --template my.typ (see references/templates.md).
#import "markup.typ": desk-markup

#let horizontalrule = align(center, line(length: 40%, stroke: 0.5pt + luma(170)))

#show terms.item: it => block(breakable: false, above: 0.8em)[
  #text(weight: "bold")[#it.term]
  #block(inset: (left: 1.5em, top: -0.35em))[#it.description]
]

$if(highlighting-definitions)$
$highlighting-definitions$

$endif$
$if(smart)$
$else$
#set smartquote(enabled: false)

$endif$
$for(header-includes)$
$header-includes$

$endfor$
#show: doc => desk-markup(
  style: "$if(desk-style)$$desk-style$$else$article$endif$",
$if(title)$
  title: [$title$],
$endif$
$if(subtitle)$
  subtitle: [$subtitle$],
$endif$
$if(author)$
  authors: ($for(author)$$if(author.name)$[$author.name$]$else$[$author$]$endif$,$endfor$),
$endif$
$if(date)$
  date: [$date$],
$endif$
$if(abstract)$
  abstract: [$abstract$],
$endif$
$if(abstract-title)$
  abstract-title: [$abstract-title$],
$endif$
$if(keywords)$
  keywords: ($for(keywords)$"$keywords$",$endfor$),
$endif$
$if(publisher)$
  publisher: [$publisher$],
$endif$
$if(rights)$
  rights: [$rights$],
$endif$
$if(desk-cover)$
  cover: "$desk-cover$",
$endif$
  toc: $if(desk-toc)$true$else$false$endif$,
  toc-depth: $if(desk-toc-depth)$$desk-toc-depth$$else$3$endif$,
  lof: $if(desk-lof)$true$else$false$endif$,
  lot: $if(desk-lot)$true$else$false$endif$,
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
$if(desk-columns)$
  columns: $desk-columns$,
$endif$
$if(desk-font)$
  font: $desk-font$,
$endif$
$if(desk-heading-font)$
  heading-font: $desk-heading-font$,
$endif$
$if(desk-code-font)$
  code-font: $desk-code-font$,
$endif$
$if(desk-fontsize)$
  fontsize: $desk-fontsize$,
$endif$
$if(desk-linestretch)$
  linestretch: $desk-linestretch$,
$endif$
$if(desk-lang)$
  lang: "$desk-lang$",
$endif$
$if(desk-region)$
  region: "$desk-region$",
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
