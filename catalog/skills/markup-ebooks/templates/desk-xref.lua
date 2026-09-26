-- Desk's pandoc filter for cross-references written the Quarto way (mk_convert.py, mk_render.py): @sec-id, @fig-id,
-- @tbl-id and [@fig-a; @tbl-b], pointing at a heading {#sec-id}, a figure ![…](x.png){#fig-id} or a table caption
-- {#tbl-id}. It runs before --citeproc, so they are never taken for citations.
--   * Typst (desk-xref-typst=true): Typst's numbered references, "Figure 2", "Table 1", "Section 3.1"; a heading
--     without numbering (no --number-sections) becomes a link titled with the heading's text.
--   * Other outputs (HTML, DOCX, EPUB, ...): links titled "Figure 2", "Table 1" or the heading's text.
-- An @sec-/@fig-/@tbl- reference without a target stays as written, with a warning.

local targets = {}
local typst = false
local numbered = false
local prefix = ""
local warned = {}
local words = { sec = "Section", fig = "Figure", tbl = "Table" }

local function warn(msg)
  if warned[msg] then return end
  warned[msg] = true
  if pandoc.log and pandoc.log.warn then pandoc.log.warn(msg) else io.stderr:write("[WARNING] " .. msg .. "\n") end
end

local function kind_of(id)
  return id:match("^(sec)%-") or id:match("^(fig)%-") or id:match("^(tbl)%-")
end

local function typst_label(id)
  return 'label("' .. id:gsub("\\", "\\\\"):gsub('"', '\\"') .. '")'
end

-- The inlines that replace one reference, or nil when the id has no target here.
local function render(id)
  local t = targets[id]
  local kind = kind_of(id)
  if t == nil then
    if prefix ~= "" and kind then
      -- Typeset in parts: the target may be in another part; desk-typst.lua resolves the link afterwards.
      return { pandoc.Link({ pandoc.Str(words[kind]) }, "#" .. id) }
    end
    return nil
  end
  if typst and (t.kind ~= "sec" or numbered) then
    return { pandoc.RawInline("typst", "#ref(" .. typst_label(prefix .. id) .. ")") }
  end
  if t.kind == "sec" then return { pandoc.Link(t.text:clone(), "#" .. id) } end
  return { pandoc.Link({ pandoc.Str(words[t.kind] .. "\u{a0}" .. t.n) }, "#" .. id) }
end

local function cite(el)
  local out = {}
  for i, c in ipairs(el.citations) do
    if not kind_of(c.id) then return nil end
    local r = render(c.id)
    if r == nil then
      warn("cross-reference @" .. c.id .. " has no target (give the heading, figure or table the id {#" .. c.id .. "})")
      return nil
    end
    if i > 1 then out[#out + 1] = pandoc.Str(i == #el.citations and " and " or ", ") end
    for _, x in ipairs(c.prefix or {}) do out[#out + 1] = x end
    if c.prefix and #c.prefix > 0 then out[#out + 1] = pandoc.Space() end
    for _, x in ipairs(r) do out[#out + 1] = x end
    for _, x in ipairs(c.suffix or {}) do out[#out + 1] = x end
  end
  return out
end

-- Without a bibliography the Markdown reader keeps "@sec-x" as text: find it inside words like "(@fig-a)." too.
local function str(el)
  local s = el.text
  if not s:find("@", 1, true) then return nil end
  local out = {}
  local rest = s
  local changed = false
  while true do
    local pre, id, post = rest:match("^(.-)@([%a][%w_%-%.:]*[%w_])(.*)$")
    if not pre then break end
    local r = kind_of(id) and render(id) or nil
    if r == nil then
      if kind_of(id) then warn("cross-reference @" .. id .. " has no target (give the heading, figure or table the id {#" .. id .. "})") end
      out[#out + 1] = pandoc.Str(pre .. "@" .. id)
    else
      if pre ~= "" then out[#out + 1] = pandoc.Str(pre) end
      for _, x in ipairs(r) do out[#out + 1] = x end
      changed = true
    end
    rest = post
  end
  if not changed then return nil end
  if rest ~= "" then out[#out + 1] = pandoc.Str(rest) end
  return out
end

function Pandoc(doc)
  typst = doc.meta["desk-xref-typst"] ~= nil
  numbered = doc.meta["desk-xref-numbered"] ~= nil
  doc.meta["desk-xref-typst"] = nil
  doc.meta["desk-xref-numbered"] = nil
  local p = doc.meta["desk-id-prefix"]  -- parts mode (kept for desk-typst.lua)
  prefix = p ~= nil and pandoc.utils.stringify(p) or ""
  local fig, tbl = 0, 0
  doc:walk({
    traverse = "topdown",
    Header = function(h)
      if h.identifier ~= "" then targets[h.identifier] = { kind = "sec", text = h.content } end
    end,
    Figure = function(f)
      fig = fig + 1
      if f.identifier ~= "" then targets[f.identifier] = { kind = "fig", n = fig } end
    end,
    Table = function(t)
      tbl = tbl + 1
      if t.identifier ~= "" then targets[t.identifier] = { kind = "tbl", n = tbl } end
    end,
  })
  return doc:walk({ Cite = cite, Str = str })
end
