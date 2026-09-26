-- Desk's pandoc filter for the Typst path (mk_convert.py, mk_render.py): makes documents from any reader compile.
--   * links with an empty target keep only their text (Typst refuses empty URLs)
--   * links to #ids that do not exist keep only their text (Typst refuses unknown labels)
--   * a duplicated identifier keeps its first element (Typst refuses ambiguous labels)
--   * without --citeproc, citations stay as written ([@key]) instead of Typst #cite calls that need a bibliography
--   * line breaks inside headings become spaces (a Typst heading ends with its line)
--   * span and image ids become anchors of their own (a Typst label right after another label steals it)
-- With the metadata desk-safe=true (the retry after a failed compile) math and raw Typst become literal text too.
-- With desk-promote=true (the report and book styles) the top heading level becomes level 1, so chapters open as
-- chapters: a document whose headings start at level 2 moves up, and a lone level-1 heading that opens the document
-- (a README or Gutenberg title) becomes the title when there is none.

local ids = {}
local seen = {}
local warned = {}
local safe = false
local citeproc = false

local function warn(msg)
  if warned[msg] then return end
  warned[msg] = true
  if pandoc.log and pandoc.log.warn then
    pandoc.log.warn(msg)
  else
    io.stderr:write("[WARNING] " .. msg .. "\n")
  end
end

local order = {}
local function collect(el)
  if el.identifier and el.identifier ~= "" and not ids[el.identifier] then
    ids[el.identifier] = true
    order[#order + 1] = el.identifier
  end
end

local function dedupe(el)
  if el.identifier and el.identifier ~= "" then
    if seen[el.identifier] then
      el.identifier = ""
      return el
    end
    seen[el.identifier] = true
  end
end

local prefix = ""
local parts = false

local function link(el)
  local t = el.target or ""
  if t == "" then return el.content end
  if t:sub(1, 1) == "#" then
    local id = t:sub(2)
    if id ~= "" and ids[prefix .. id] then
      el.target = "#" .. prefix .. id
      return el
    end
    if parts and id ~= "" then
      -- Maybe an anchor in another part: mk_render resolves the marker once every part is converted.
      local out = { pandoc.RawInline("typst", '#link(label("desk-xref:' .. id:gsub("\\", "\\\\"):gsub('"', '\\"') .. '"))[') }
      for _, x in ipairs(el.content) do out[#out + 1] = x end
      out[#out + 1] = pandoc.RawInline("typst", "]")
      return out
    end
    warn("link to a missing anchor #" .. id .. " kept as text")
    return el.content
  end
end

local function cite(el)
  if citeproc then return nil end
  if #el.content > 0 then return pandoc.Span(el.content) end
  local keys = {}
  for _, c in ipairs(el.citations) do keys[#keys + 1] = "@" .. c.id end
  return pandoc.Str("[" .. table.concat(keys, "; ") .. "]")
end

local function math(el)
  if not safe then return nil end
  if el.mathtype == "DisplayMath" then return pandoc.Code("$$" .. el.text .. "$$") end
  return pandoc.Code("$" .. el.text .. "$")
end

local function label_code(id)
  if id:match("^[%w_%-%.:]+$") then return "<" .. id .. ">" end
  return '#label("' .. id:gsub("\\", "\\\\"):gsub('"', '\\"') .. '")'
end

-- Typst attaches a label to the element just before it, so a label right after another label steals it: an empty
-- anchor (<a id=…></a>) inside another span, next to an image's id or at the start of a heading labels nothing.
-- Every span id therefore becomes an anchor element of its own, placed before the span's content.
local function span(el)
  if el.identifier == "" then return nil end
  local anchor = pandoc.RawInline("typst", "#metadata(none)" .. label_code(el.identifier))
  el.identifier = ""
  if #el.content == 0 then return anchor end
  return { anchor, el }
end

-- An image's id becomes a label right after it, and pandoc turns an image it cannot load into an empty span that
-- keeps the id: next to another label that steals it. Keep an image id only when a link points at it, as an anchor
-- of its own before the image.
local linked = {}
local function image(el)
  if el.identifier == "" then return nil end
  local id = el.identifier
  el.identifier = ""
  if linked[id] and ids[id] then
    return { pandoc.RawInline("typst", "#metadata(none)" .. label_code(id)), el }
  end
  return el
end

-- A Typst heading ends at the end of its line: line breaks inside a heading (HTML <br>, wrapped source) become spaces.
local function header(el)
  el.content = el.content:walk({
    SoftBreak = function() return pandoc.Space() end,
    LineBreak = function() return pandoc.Space() end,
  })
  return el
end

-- A picture alone in a paragraph that is not a figure (a linked thumbnail, an image with a title) is centred like a
-- figure, instead of hanging at the left of the column.
local function lone_picture(el)
  local c = el.content
  if #c ~= 1 then return nil end
  local x = c[1]
  if x.t == "Link" and #x.content == 1 then x = x.content[1] end
  if x.t ~= "Image" then return nil end
  return { pandoc.RawBlock("typst", "#align(center)["), el, pandoc.RawBlock("typst", "]") }
end

local function raw_inline(el)
  if safe and el.format:match("typst") then return pandoc.Code(el.text) end
end

local function raw_block(el)
  if safe and el.format:match("typst") then return pandoc.CodeBlock(el.text) end
end

-- A big document is typeset in parts (one pandoc run each): desk-id-prefix keeps each part's ids apart, and
-- desk-ids-out names a file that receives this part's ids (so links between parts can be resolved afterwards).
local function add_prefix(el)
  if el.identifier and el.identifier ~= "" then
    el.identifier = prefix .. el.identifier
    return el
  end
end

local function write_ids(path)
  local f = io.open(path, "w")
  if not f then return end
  for _, id in ipairs(order) do f:write(id:sub(#prefix + 1), "\n") end
  f:close()
end

local function promote_headings(doc)
  local min, h1, h2, first = 99, 0, 0, nil
  doc:walk({ Header = function(h)
    if first == nil then first = h end
    if h.level < min then min = h.level end
    if h.level == 1 then h1 = h1 + 1 elseif h.level == 2 then h2 = h2 + 1 end
  end })
  local shift = 0
  if min == 99 then return doc end
  if min > 1 then
    shift = min - 1
  elseif h1 == 1 and first.level == 1 and h2 > 0 then
    local text = pandoc.utils.stringify(first.content)
    local title = doc.meta.title ~= nil and pandoc.utils.stringify(doc.meta.title) or nil
    if title == nil or title:lower() == text:lower() then
      if title == nil then doc.meta.title = pandoc.MetaInlines(first.content) end
      local id = first.identifier
      local dropped = false
      doc = doc:walk({ Header = function(h)
        if not dropped and h.level == 1 then
          dropped = true
          if id ~= "" then return pandoc.Plain({ pandoc.Span({}, { id = id }) }) end
          return {}
        end
      end })
      shift = 1
    end
  end
  if shift == 0 then return doc end
  return doc:walk({ Header = function(h) h.level = (h.level - shift < 1) and 1 or (h.level - shift); return h end })
end

function Pandoc(doc)
  local function meta(name)
    local v = doc.meta[name]
    doc.meta[name] = nil
    return v ~= nil and pandoc.utils.stringify(v) or ""
  end
  if meta("desk-promote") == "true" then doc = promote_headings(doc) end
  safe = meta("desk-safe") == "true"
  citeproc = meta("desk-citeproc") == "true"
  prefix = meta("desk-id-prefix")
  local ids_out = meta("desk-ids-out")
  parts = ids_out ~= ""
  if prefix ~= "" then
    doc = doc:walk({ Header = add_prefix, Div = add_prefix, Span = add_prefix, Figure = add_prefix, Table = add_prefix, Image = add_prefix })
  end
  -- The elements pandoc's Typst writer labels (code, images and links get no Typst label), in document order.
  doc = doc:walk({ traverse = "topdown", Header = dedupe, Div = dedupe, Span = dedupe, Figure = dedupe, Table = dedupe, Image = dedupe })
  doc:walk({ Header = collect, Div = collect, Span = collect, Figure = collect, Table = collect, Image = collect })
  if parts then write_ids(ids_out) end
  doc:walk({ Link = function(l) if l.target:sub(1, 1) == "#" then linked[prefix .. l.target:sub(2)] = true end end })
  doc = doc:walk({ Link = link, Cite = cite, Math = math, RawInline = raw_inline, RawBlock = raw_block, Header = header, Span = span, Image = image })
  return doc:walk({ Para = lone_picture, Plain = lone_picture })
end
