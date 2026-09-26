-- Desk's pandoc filter for Sphinx reStructuredText (Python's and many projects' docs), used for every .rst input
-- (mk_convert.py, mk_render.py, mk_read.py). pandoc reads Sphinx files as plain reST, so:
--   * roles such as :func:`~pkg.f`, :class:`!Zip`, :ref:`text <target>` keep their raw markup; they become code
--     (f(), Zip) or plain text (text), with Sphinx's ~ and ! conventions and :pep:/:rfc: numbers;
--   * API directives (.. function::, .. method::, .. class::, .. attribute::, .. exception::, …) lose their
--     signatures; they become a definition list: the signature in code, the description under it;
--   * .. versionadded::, .. versionchanged::, .. deprecated:: become "New in version 3.2: …" notes;
--   * .. module:: keeps only its synopsis.
-- A plain reST file without Sphinx roles or directives is left alone.

local API = {
  ["function"] = "", method = "", classmethod = "classmethod ", staticmethod = "staticmethod ", attribute = "",
  data = "", exception = "exception ", decorator = "@", decoratormethod = "@", property = "property ",
  describe = "", object = "", envvar = "", option = "", cmdoption = "", program = "", coroutinefunction = "coroutine ",
  coroutinemethod = "coroutine ", abstractmethod = "abstractmethod ", awaitablefunction = "awaitable ",
  ["py:function"] = "", ["py:method"] = "", ["py:class"] = "class ", ["py:attribute"] = "", ["py:data"] = "",
  ["py:exception"] = "exception ", ["c:function"] = "", ["c:type"] = "", ["c:macro"] = "", ["c:member"] = "",
  ["c:var"] = "", ["cpp:function"] = "", ["cpp:class"] = "class ", ["js:function"] = "",
}
local VERSION = { versionadded = "New in version", versionchanged = "Changed in version", deprecated = "Deprecated since version", versionremoved = "Removed in version" }
local PROSE_ROLES = { ref = true, doc = true, term = true, dfn = true, guilabel = true, menuselection = true, abbr = true, numref = true, keyword = true, token = true, download = true }
local CALLABLE = { func = true, meth = true, ["py:func"] = true, ["py:meth"] = true, ["c:func"] = true, ["cpp:func"] = true, ["js:func"] = true }

local sphinx = false

local function has_api_class(classes)
  for _, c in ipairs(classes) do
    if API[c] or VERSION[c] or c == "module" or c == "currentmodule" then return true end
  end
  return false
end

local function role_text(el)
  local role = el.attributes.role or ""
  local text = el.text
  local title = text:match("^(.-)%s*<[^>]*>$")
  if title and title ~= "" then text = title end
  text = text:gsub("^!", "")
  if text:sub(1, 1) == "~" then text = text:sub(2):match("([^.]+)$") or text:sub(2) end
  if CALLABLE[role] and not text:match("%)$") then text = text .. "()" end
  return role, text
end

local function code(el)
  if not el.classes:includes("interpreted-text") then return nil end
  local role, text = role_text(el)
  if role == "pep" then return pandoc.Str("PEP\u{a0}" .. text) end
  if role == "rfc" then return pandoc.Str("RFC\u{a0}" .. text) end
  if role == "abbr" then text = text:gsub("%s*%(.-%)$", "") end
  if role == "math" then return pandoc.Math("InlineMath", text) end
  if PROSE_ROLES[role] or role == "" then return pandoc.Inlines(text) end
  return pandoc.Code(text)
end

-- The signature of `.. class:: Name(args)`, which pandoc reads as the reST class directive (its words become the
-- div's classes).
local function class_signature(classes)
  if #classes == 0 or has_api_class(classes) then return nil end
  local sig = table.concat(classes, " ")
  if sig:match("^[%a_][%w_.]*%(") or (sig:match("^[%u][%w_.]*$") and #classes == 1) then return sig end
  return nil
end

local function signature_entry(sig, body)
  return pandoc.DefinitionList({ { { pandoc.Code(sig) }, { body } } })
end

local function div(el)
  if not sphinx then return nil end
  for _, c in ipairs(el.classes) do
    if VERSION[c] then
      local first = el.content[1]
      if first and (first.t == "Para" or first.t == "Plain") and first.content[1] and first.content[1].t == "Str" then
        local v = first.content[1].text
        first.content[1] = pandoc.Emph({ pandoc.Str(VERSION[c] .. " " .. v .. ":") })
        el.content[1] = first
      else
        table.insert(el.content, 1, pandoc.Para({ pandoc.Emph({ pandoc.Str(VERSION[c] .. ":") }) }))
      end
      return el.content
    end
    if c == "module" or c == "currentmodule" then
      local syn = el.attributes.synopsis
      if syn and syn ~= "" then return pandoc.Para({ pandoc.Emph(pandoc.Inlines(syn)) }) end
      return {}
    end
    if API[c] ~= nil then
      local first = el.content[1]
      if first and (first.t == "Para" or first.t == "Plain") then
        local sig = API[c] .. pandoc.utils.stringify(first.content)
        local body = {}
        for i = 2, #el.content do body[#body + 1] = el.content[i] end
        return signature_entry(sig, body)
      end
      return el.content
    end
  end
  local sig = class_signature(el.classes)
  if sig then return signature_entry("class " .. sig, el.content) end
  return nil
end

function Pandoc(doc)
  doc:walk({
    Code = function(el)
      if el.classes:includes("interpreted-text") then sphinx = true end
    end,
    Div = function(el)
      if has_api_class(el.classes) then sphinx = true end
    end,
  })
  if not sphinx then return nil end
  -- Innermost directives first, so a method inside a class keeps its own signature.
  doc = doc:walk({ Code = code, Div = div })
  -- One definition list per run of directives (Markdown output would separate single-entry lists with &nbsp;).
  return doc:walk({ Blocks = function(blocks)
    local out = pandoc.Blocks({})
    for _, b in ipairs(blocks) do
      local prev = out[#out]
      if b.t == "DefinitionList" and prev and prev.t == "DefinitionList" then
        for _, item in ipairs(b.content) do prev.content:insert(item) end
      else
        out:insert(b)
      end
    end
    return out
  end })
end
