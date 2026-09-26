-- Desk's pandoc filter for HTML output (mk_convert.py): a document without a title gets a <title> from its first
-- heading, else from the file name (the metadata desk-default-title). A title in the document always wins.

function Pandoc(doc)
  local fallback = doc.meta["desk-default-title"]
  doc.meta["desk-default-title"] = nil
  if doc.meta.title ~= nil or doc.meta.pagetitle ~= nil then return doc end
  for _, b in ipairs(doc.blocks) do
    if b.t == "Header" then
      local text = pandoc.utils.stringify(b)
      if text ~= "" then
        doc.meta.pagetitle = text
        return doc
      end
    end
  end
  if fallback ~= nil then doc.meta.pagetitle = fallback end
  return doc
end
