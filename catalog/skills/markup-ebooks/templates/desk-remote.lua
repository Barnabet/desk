-- Desk's pandoc filter that keeps pandoc off the network (mk_convert.py, mk_render.py, epub_tool.py build).
-- pandoc downloads remote images for PDF, DOCX, ODT, EPUB and self-contained HTML without any timeout. Desk downloads
-- them first (scripts/_remote.py) and passes a JSON map url -> local file as the metadata desk-remote-map; this
-- filter points images at those copies and turns the rest into links, in raw HTML as well.

local map = {}
local warned = {}

local function warn(msg)
  if warned[msg] then return end
  warned[msg] = true
  if pandoc.log and pandoc.log.warn then pandoc.log.warn(msg) else io.stderr:write("[WARNING] " .. msg .. "\n") end
end

local function remote(src)
  return src:match("^[Hh][Tt][Tt][Pp][Ss]?://") ~= nil or src:match("^//") ~= nil
end

local function lookup(src)
  return map[src] or map[(src:gsub("&amp;", "&"))]
end

local function image(el)
  if not remote(el.src) then return nil end
  local loc = lookup(el.src)
  if loc then
    el.src = loc
    return el
  end
  warn("remote image kept as a link: " .. el.src)
  local label = el.caption
  if #label == 0 then label = { pandoc.Str("image") } end
  return pandoc.Link(label, el.src)
end

local function rewrite_html(text)
  return (text:gsub("<[Ii][Mm][Gg]%s[^>]*>", function(tag)
    local q, src = tag:match("%s[Ss][Rr][Cc]%s*=%s*([\"'])(.-)%1")
    if not src or not remote(src) then return tag end
    local loc = lookup(src)
    if loc then return (tag:gsub("([Ss][Rr][Cc]%s*=%s*)([\"']).-%2", "%1%2" .. loc:gsub("%%", "%%%%") .. "%2", 1)) end
    warn("remote image kept as a link: " .. src)
    local alt = tag:match("%s[Aa][Ll][Tt]%s*=%s*[\"'](.-)[\"']") or "image"
    return '<a href="' .. src .. '">' .. alt .. "</a>"
  end))
end

local function raw(el)
  if el.format:match("html") and el.text:match("<[Ii][Mm][Gg]") then
    el.text = rewrite_html(el.text)
    return el
  end
end

function Pandoc(doc)
  local v = doc.meta["desk-remote-map"]
  if v ~= nil then
    local f = io.open(pandoc.utils.stringify(v), "r")
    if f then
      local ok, decoded = pcall(pandoc.json.decode, f:read("a"), false)
      f:close()
      if ok and type(decoded) == "table" then map = decoded end
    end
    doc.meta["desk-remote-map"] = nil -- never let the temp path reach the output's metadata
  end
  return doc:walk({ Image = image, RawInline = raw, RawBlock = raw })
end
