# Email formats and what the scripts do with them

## Single messages

| Format | How it is read |
|---|---|
| `.eml` (RFC 5322 / MIME) | The standard library parser in its tolerant (compat32) mode, plus our own decoding of headers and payloads. |
| `.emlx` (Apple Mail) | A byte count, the RFC 822 message, then an XML plist. The plist gives flags (read, answered, flagged) and the received date. `.partial.emlx` files keep large attachments in `../Attachments/<id>/<part>/`: they are picked up when present, otherwise the attachment is listed as missing. |
| `.msg` (Outlook, MS-OXMSG) | An OLE compound file read with olefile: MAPI properties, recipients, attachments, embedded messages, named properties. |
| `winmail.dat` (TNEF) | Unpacked wherever it appears as an attachment, and read on its own (`.dat`, `.tnef`): files with their long names, embedded messages, subject (or the conversation topic), sender (attFrom), recipients by type (the recipient table), date, Message-ID, and the body as text, HTML or compressed RTF. |
| mbox entry, Maildir file | Addressed as `MAILBOX --message N` (see below). |

### Decoding rules (robustness)

- **Headers**: RFC 2047 encoded words are decoded even when sloppy: a multi-byte character split across two
  words is joined, whitespace between adjacent words is dropped, unknown charset labels fall back gracefully.
  Raw 8-bit headers (no encoding) are decoded as UTF-8, else detected.
- **Addresses** are split before decoding, so an encoded display name containing a comma stays one address.
- **Bodies**: the declared charset first, then UTF-8, then detection (charset-normalizer), then a lossless
  single-byte fallback. Common mislabels are mapped (`iso-8859-1` → cp1252, `gb2312` → gb18030,
  `ks_c_5601-1987` → cp949, `x-unknown`). A body labelled `us-ascii` that is really UTF-8 comes out right.
- **Transfer encodings**: base64 with bad padding or stray characters, quoted-printable with 8-bit bytes,
  uuencode. RFC 2231 parameters (`filename*=utf-8''…`, continuations) are decoded.
- **Structure**: a missing closing boundary, parts without headers, `message/rfc822` parts, delivery reports.
- **Which body**: plain text unless it is empty, a "view this in your browser" stub, or much shorter than the HTML.
  HTML is cleaned (scripts, styles, comments, 1×1 tracking images) and converted to Markdown; layout tables become
  blocks, data tables stay tables. `cid:` images become `attachment:NAME`.
- **Hidden text**: elements styled `display:none`, `visibility:hidden`, a font size of 1px or less, zero opacity,
  pushed off-screen, or white text with no background colour around it (checked only when no style sheet paints
  backgrounds) are removed from the Markdown and quoted in a closing note: "hidden text removed from the HTML: N
  fragment(s) …". Renders keep them (tiny text renders tiny).
- **Links** (`--links`): each `<a href>` with its text and a note for: text showing another domain than the
  target, a punycode (`xn--`) host (decoded, so `xn--pypal-4ve.com` shows as `pаypal.com` with a Cyrillic a), a
  bare IP address, a `user@host` URL that hides the real host, and `javascript:`, `vbscript:`, `data:` or `file:`
  links.
- **Attachment names** with Unicode direction controls (a right-to-left override, U+202E, placed before
  `gpj.exe` makes `invoice…gpj.exe` display as `invoiceexe.jpg`) are shown in their real order, with a warning naming the control characters.
- **Deep or huge structures**: parts nested deeper than 200 MIME levels are not listed (with a warning); attached
  messages are read to 8 levels and listed below that; more than 100,000 parts are cut with a warning; a
  structure the parser cannot follow at all (thousands of levels) still shows its headers and the start of the
  raw body. Attachment lists over 40 rows print as CSV.
- **Inline vs attachment**: an image referenced by `cid:` from the HTML is inline whatever its disposition says.
- **Quotes** (`--strip-quotes`): Gmail (`gmail_quote`), Outlook (`divRplyFwdMsg`, border-top separators), Apple
  Mail (`blockquote type=cite`), Thunderbird (`moz-cite-prefix`), Yahoo; in text, "On … wrote:", "Le … a écrit :",
  "-----Original Message-----" and trailing `>` blocks.

### Outlook .msg details

- 8-bit strings use the message code page (PR_MESSAGE_CODEPAGE), else the ANSI code page of the message locale
  (PR_MESSAGE_LOCALE_ID: 1049 → cp1251, 1041 → cp932, …). The HTML body uses PR_INTERNET_CPID.
- Body order: PR_BODY (text) and PR_HTML; when there is no HTML, the compressed RTF (PR_RTF_COMPRESSED, LZFu) is
  decompressed. RTF made from HTML (`\fromhtml1`) gives back the original HTML exactly; other RTF becomes text.
- Sender: the SMTP address (PR_SENDER_SMTP_ADDRESS …); an Exchange X.500 address (`/O=…/CN=…`) is replaced by the
  SMTP address of a recipient with the same name when there is one, otherwise shown as an Exchange address.
- Recipients by type (To, Cc, Bcc) from the recipient storages; transport headers (PR_TRANSPORT_MESSAGE_HEADERS)
  give Message-ID, References, Received and Authentication-Results when the message was received.
- Attachments: by value (files), embedded messages (shown nested, saved as .eml), OLE objects (reported, not
  extracted), links to external files (reported).
- Non-mail items: meetings and appointments (rebuilt as an iCalendar, see `calendar.md`), contacts (names,
  company, emails, phones, addresses, birthday) and tasks (start, due, status, % complete).
- `--to-eml` writes a standard message: headers (including the original Received and auth headers), text and HTML
  alternatives, inline images in `multipart/related`, attachments and embedded messages (`message/rfc822`) in
  their original order. A meeting or appointment gets its rebuilt calendar as a `text/calendar` alternative (see
  `calendar.md`). A recipient Outlook never resolved to an address (a name only) is written as an empty group,
  `"New Outlook User":;`, valid RFC 5322 that no reader mistakes for an address. An S/MIME clear-signed .msg
  (`IPM.Note.SMIME.MultipartSigned`) keeps the signed MIME entity as its body, so the content and signature
  stay together (the signature is not verified).

## Mailboxes

| Kind | Detected by | Order of #N |
|---|---|---|
| mbox (mboxo, mboxrd, Gmail Takeout, Thunderbird, Apple export) | a file whose messages start with `From ` lines | file order |
| Maildir, Maildir++ | a folder with `cur/` and `new/` (subfolders `.Sent/` … are included, recorded as `folder`) | sorted file names |
| Apple Mail `.mbox` bundle | a folder holding `Messages/*.emlx` | sorted paths |
| folder of messages | `.eml`, `.emlx`, `.msg` files anywhere below | sorted paths |

- `From ` separator lines must look like separators (a time `hh:mm`, or a blank line before and a header after),
  so an unquoted "From " line inside a body does not split a message. `>From ` quoting is undone on reading and
  applied (mboxrd) when writing an mbox.
- CRLF mailboxes (written on Windows) work. The separator scan reads the file in 8 MB blocks, so memory stays flat
  for any size; index workers each map only their own chunk of messages.
- Dates in listings are each sender's own time with its UTC offset (`2024-01-31 12:20 -0800`); `--tz ZONE`
  converts them all to one zone. CSV and JSON give the full ISO date.
- The **index** (SQLite, cached): per message `n, off, len, path, ts, date, from_name, from_addr, to_addrs,
  cc_addrs, subject, msgid, in_reply_to, refs, n_att, n_inline, att (name, type, size, inline), ctype, labels
  (X-Gmail-Labels, X-Keywords), list_id, flags (Status, X-Status), folder, size`. Attachment names come from a
  light MIME walk that skips attachment payloads, so it stays fast on attachment-heavy mailboxes.
- The **text store** (cached, built by the first `--body`/`--any` search): the decoded readable text of each
  message (plain text, else HTML stripped), compressed.
- Threads: connected components over Message-ID links (References and In-Reply-To), plus replies ("Re:", "Fwd:",
  "AW:", "TR:" …) that share a subject with an earlier message (`--no-subject` to turn that off). `--sort` orders
  them by last activity (default), first message, message count, number of people or total bytes.
- Body search reads the text store in two threads; a run of 3+ literal characters every match must contain (like
  `invoice` in `invoice\s+#?\d+`) is first looked for with a plain substring search, so the regular expression runs
  only on the messages that contain it.

## mail_read JSON fields

`format, subject, from, sender, to, cc, bcc, reply_to` (lists of `{name, email}`), `date` (ISO with offset),
`date_raw, message_id, in_reply_to, references, list_id, labels, mailer, importance, auth` (`spf`, `dkim`, `dmarc`,
`arc`: lists of `{result, header.d, smtp.mailfrom, …}`, `dkim_signed_by`, `notes`), `security` (`signed`,
`encrypted`), `attachments` (`index, name, content_type, size, disposition, content_id, mime_path, kind`
(file, message, calendar, tnef, report, ole-object, reference), `message` (nested model), `calendar`, `tnef`),
`calendar` (invite summaries), `warnings`, `body`, `body_source`; with `--both` also `text` and `html`; with
`--headers` all `headers`; `.msg` adds `message_class`, `categories`, `outlook_item`, `unsent`.

## mail_create JSON spec

Every option can be given as a key of `--spec` (inline JSON, a file, or `-`): `from, to, cc, bcc, reply_to,
subject, body` (Markdown), `body_file, html, plain, attach` (list of paths), `inline` (list of image paths),
`header` (list of "Name: value" or an object), `reply, reply_all, forward, as_attachment, message, no_quote,
calendar, date, priority, no_unsent, out`.
