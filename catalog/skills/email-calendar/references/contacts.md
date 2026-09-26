# Contacts with vcf_tool.py

## Reading

- vCard 2.1: bare type parameters (`TEL;CELL;PREF`), `ENCODING=QUOTED-PRINTABLE` with `=` soft line breaks,
  `CHARSET=` per property, `ENCODING=BASE64` photos on folded lines.
- vCard 3.0: `TYPE=` lists, escaped `\,` `\;` `\n`, `ENCODING=b` photos, grouped properties (`item1.EMAIL` with
  Apple's `item1.X-ABLabel`, whose label becomes a type).
- vCard 4.0: `PREF=1`, `VALUE=uri` phones (`tel:+33…`), `data:` URI photos, `KIND`, `GENDER`, `ANNIVERSARY`.
- UTF-8 with or without BOM and UTF-16 (Outlook exports). A last card without `END:VCARD` is kept.
- `.csv` and `.json` inputs are read as contact lists everywhere a `.vcf` is accepted.

Each contact: `n` (its number), `fn`, `name {family, given, additional, prefix, suffix}`, `nickname`, `org` (list:
company, department), `title`, `role`, `emails`/`phones` (`{value, types, pref}`), `addresses` (`{types, po_box,
ext, street, locality, region, postal_code, country, label}`), `urls`, `impp`, `bday`, `anniversary`, `note`,
`categories`, `uid`, `rev`, `tz`, `geo`, `photo` (`{mime, size}` or `{url}`), `x` (X- properties), `version`, `source`.

## Writing

Output is vCard 3.0 by default (read by every client), 4.0 or 2.1 with `--version`. Lines are folded at 75 bytes
without splitting UTF-8 characters; 2.1 writes non-ASCII values as quoted-printable UTF-8. X- properties, UID and
REV are kept; photos are embedded (JPEG, PNG, GIF as they are; other formats converted to JPEG, at most 512 px).

`create --spec` takes a contact or a list: `name` (or `given`/`family`, or a `name` object), `email`/`emails`,
`phone`/`phones` (strings like `"+1 555 0100;cell"` or `{value, types, pref}`), `org`/`company`, `title`, `role`,
`address`/`addresses` (`"street, postcode city, region, country;work"` or `{street, city, region, postal_code,
country, types}`), `birthday`, `anniversary`, `url`, `note`, `nickname`, `categories`, `photo` (a path, URL or
data: URI), `uid`.

## Duplicates

`--by` picks what makes two contacts the same person (default `email,phone`; `--by-name` adds `name`):

- **email**: a shared normalised email (lower-case, no `mailto:`).
- **phone**: a shared phone (digits only, compared on the last 9 digits so `+33 6 12 34 56 78` equals
  `06 12 34 56 78`) **and** names that agree: equal, one of them empty, or one's words all within the other's
  (accents and case ignored: "Ana Lopez" / "Ana M. Lopez" agree, "Greg Dartmouth" / "VCard Test" do not). The
  check is made against every name already in both groups, so a chain of shared numbers cannot join different
  people either. A number shared by thousands of cards is compared with its first 50 holders only.
- **name**: equal normalised full names (accents removed, word order ignored). Two "John Smith" are often two
  people: use it knowingly.

`dedupe` lists each group with what the contacts share and flags `names_differ` (a family sharing one email, for
example). A merged contact starts from the richest record and unites emails, phones, addresses, URLs and
categories without repeats; notes are joined; names that disagree are kept as "Also named: …" in the note;
`merged_from` lists the source numbers. `merge` prints a warning for each group whose names differ.

## CSV

| `--csv-style` | columns |
|---|---|
| `simple` | Full Name, Given Name, Family Name, Nickname, Organization, Title, Email 1-3 (+ Type), Phone 1-3 (+ Type), Address 1-2 (+ Type), Birthday, URL, Note, Categories, UID |
| `google` | Google Contacts import/export: Name, Given Name, Family Name, …, E-mail N - Type/Value, Phone N - Type/Value, Address 1 - …, Organization 1 - Name/Title, Group Membership |
| `outlook` | Outlook import/export: First/Middle/Last Name, E-mail (2, 3) Address, Home/Business/Mobile Phone, Business Fax, Company, Job Title, Home/Business Street, City, State, Postal Code, Country/Region, Birthday, Web Page, Notes, Categories |

Reading CSV recognises all three layouts (and Google's newer "Label" columns). The delimiter (comma, semicolon as
European Excel writes it, or tab) is read from the header row, and the whole file goes through one CSV reader, so
quoted fields that span lines (multi-line notes and addresses in Google and Outlook exports) stay whole.

The `simple` and `google` styles add numbered columns (`Email 4`, `E-mail 4 - Value`, `Address 2 - …`) so up to 10
emails, phones and addresses per contact survive. Outlook's layout is fixed: 3 emails, one home, business, mobile
and fax phone, a home and a business address; `convert` prints a warning for every contact that loses values.
Photos are not carried in CSV. A contact written to CSV and read back gives the same names, emails, phones, notes
and addresses (within those limits).

## Photos

`photos` saves each embedded photo under its contact number and name (JPEG, PNG, GIF and WebP as they are, other
formats as PNG) and draws a labelled sheet with each photo's size. A photo that is truncated or not an image (phone
exports sometimes cut them) is still saved as it is, reported on stderr, and left off the sheet.
