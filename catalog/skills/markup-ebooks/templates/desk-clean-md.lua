-- Desk's pandoc filter for Markdown written from e-books, web pages and office files (mk_convert.py): readable
-- Markdown without the reader's scaffolding. Section and wrapper divs and anchor spans are unwrapped (their content
-- stays), and raw HTML that holds no text (an EPUB cover's inline SVG, empty wrappers) is dropped. The document's
-- own text, headings, links, images, tables and notes are untouched.

local function textless(html)
  if html:match("<[Ii][Mm][Gg]") or html:match("<[Tt][Aa][Bb][Ll][Ee]") or html:match("<[Ii][Ff][Rr][Aa][Mm][Ee]") or html:match("<[Vv][Ii][Dd][Ee][Oo]") then
    return false
  end
  local t = html:gsub("<[Ss][Vv][Gg].-</[Ss][Vv][Gg]>", ""):gsub("<[^>]*>", ""):gsub("&[%w#]+;", "x")
  return not t:match("%S")
end

local SVG_TAGS = { svg = true, image = true, g = true, path = true, rect = true, use = true, defs = true, desc = true }

local function raw_inline(el)
  if not el.format:match("html") then return nil end
  local tag = el.text:match("^</?%s*([%a]+)")
  if tag and SVG_TAGS[tag:lower()] then return {} end
end

-- Sizes, ids and classes that plain Markdown cannot hold would turn pictures, figures and links into raw HTML.
-- Only elements that have some are rebuilt (returning every link would double the time on a big book).
local EMPTY = pandoc.Attr()
local function plain_attr(el)
  if el.attr ~= EMPTY then
    el.attr = pandoc.Attr()
    return el
  end
end

-- An EPUB's cover comes twice (from the metadata and from its cover page): keep the first of two identical pictures
-- in a row near the start.
local function lone_image(b)
  if (b.t == "Para" or b.t == "Plain") and #b.content == 1 and b.content[1].t == "Image" then
    return b.content[1].src:match("([^/]+)$") or b.content[1].src
  end
  if b.t == "Figure" and #b.content == 1 then return lone_image(b.content[1]) end
  return nil
end

function Pandoc(doc)
  doc = doc:walk({
    Div = function(el) return el.content end,
    Span = function(el) return el.content end,
    RawBlock = function(el) if el.format:match("html") and textless(el.text) then return {} end end,
    RawInline = raw_inline,
    Image = plain_attr,
    Figure = plain_attr,
    Link = plain_attr,
  })
  local blocks = doc.blocks
  local last, i = nil, 1
  while i <= #blocks and i <= 40 do
    local b = blocks[i]
    if (b.t == "Para" or b.t == "Plain") and #b.content == 0 then  -- a paragraph that held only an anchor
      blocks:remove(i)
    else
      local src = lone_image(b)
      if src and src == last then
        blocks:remove(i)
      else
        last = src
        i = i + 1
      end
    end
  end
  doc.blocks = blocks
  return doc
end
