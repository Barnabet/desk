# What file_identify detects, and where it sends each type

## How a file is identified

Each file is read once, at most its first 64 KB (16 KB with `--fast` and in `file_survey`), plus small reads at known
offsets (the tail for ZIP directories and trailers, offset 0x8001 for ISO 9660, 128 for DICOM, 257 for TAR). The
checks run in order, and the first confident answer wins:

1. **Magic numbers** and structural checks: PNG, JPEG, GIF, PDF, RIFF (WAV, AVI, WebP), ISO BMFF (MP4, MOV, M4A,
   HEIC, AVIF, JPEG 2000 and CR3 by their `ftyp` brands), EBML (Matroska, WebM by its DocType), Ogg (Vorbis, Opus,
   FLAC, Theora by their first packet), MPEG audio (two valid frame headers in a row), executables, databases,
   compressed streams and archives, disk images and filesystems, fonts and 3D formats.
2. **Container internals.** ZIP: `[Content_Types].xml` names the Office type (docx, docm, dotx, xlsx, xlsm, xlsb,
   pptx, ppsx, potx, vsdx); `mimetype` names ODF, EPUB, Krita and OpenRaster; then APK (manifest + dex), AAB, IPA
   (`Payload/*.app`), XPI, JAR (manifest + classes), wheels (`*.dist-info/WHEEL`), NuGet, VSIX, XPS, 3MF, KMZ, USDZ,
   iWork (`Index/*.iwa`, with its preview image), NumPy .npz. A package whose `[Content_Types].xml` names a main
   part it does not contain (`/word/document.xml`) is reported as damaged, with a medium confidence. A ZIP whose
   central directory is missing (streamed or truncated) is read from its local headers: when those show a Word,
   Excel, PowerPoint, ODF, EPUB, APK or JAR package, it is reported as that type, "damaged or truncated".
   Every ZIP-based file also goes through the zip-bomb check the other Desk skills apply before opening one
   (`check_zip`): parts that inflate beyond 2 GB, a total beyond 4 GB, a compression ratio above 250 on parts over
   16 MB, more than 100,000 parts, and, in Office packages, XML parts that declare a DOCTYPE or entities
   (billion laughs, XXE). On top of it: parts whose size fields are false (declared smaller than stored) and
   entries that share one member's data (overlapping members). Any of these gives an "unsafe to open" warning. OLE2: the directory names the type (WordDocument → .doc, Workbook
   → .xls, PowerPoint Document → .ppt, `__substg1.0_` → .msg, EncryptionInfo + EncryptedPackage → a
   password-protected docx/xlsx/pptx, the root CLSID → .msi).
3. **Text.** The encoding is found first (BOM, UTF-16 NUL pattern, UTF-8 check, charset-normalizer), then the
   structure: PEM/SSH/PGP keys, JSON (and JSON Lines, GeoJSON, TopoJSON, glTF, HAR, notebooks, JSON Schema,
   package manifests), XML dialects by root element (SVG, XHTML, RSS, Atom, KML, GPX, OSM, DocBook, JATS, plist,
   Office 2003 XML, FictionBook, OPML, sitemaps, COLLADA), HTML, email headers and mbox, calendars, contacts,
   subtitles, RTF, PostScript, LaTeX, BibTeX, diffs, logs, CSV/TSV (with the delimiter and header), YAML, TOML, INI,
   .env files, Markdown and other markup, and source code by language (shebang, extension, keyword statistics).
4. A **stub or padding** before a known signature (self-extracting archives, installers with a payload).
5. **puremagic's** long-tail signature table (hundreds of rarer formats), trusted only on a match of 4+ bytes.
6. **Entropy**: near 8 bits per byte with no signature is "encrypted, compressed or random"; otherwise "unknown
   binary data" with its entropy and share of zero bytes.

## Output fields (`--format json`)

`path`, `name`, `size`, `type` (a stable id such as `docx`, `png`, `pem`), `desc`, `mime`, `group`, `confidence`
(0-1) and `confidence_label` (`high` ≥ 0.85, `medium` ≥ 0.6, `low`), `details` (format-specific: `pages`, `width`,
`height`, `duration_s`, `tracks`, `sheets`, `slides`, `tables`, `entries`, `encoding`, `newlines`, `lines`, `language`,
`arch`, `bits`, `certificates`…), `ext`, `mismatch` and `suggested_ext`, `warnings`, `note`, `skill`, `command`
(the first command, ready to run with that skill), `script` and `args`. An unreadable file has `error` instead.
Multi-file JSON adds `summary` (`by_skill`, `by_type`, counts) and paging (`total`, `offset`, `next_offset`, `next`).

## Warnings

| warning | meaning | what to do |
|---|---|---|
| named .X but the content is Y | extension mismatch | open a copy named with `suggested_ext` (keep the original), then use Y's skill |
| named .pdf (or .zip, .docx…) but is an HTML page | a login, error or download page was saved instead | ask for the real file |
| contains N PRIVATE KEY block(s) / PGP SECRET KEY | a secret | never print or upload it; tell the user where it is |
| password-protected / encrypted | Office encryption, KeePass, age, OpenSSL `Salted__`, Ansible Vault, LUKS, BitLocker, encrypted ZIP entries | ask for the password or an unprotected copy |
| contains VBA macros | .docm/.xlsm/.pptm or a macro part | read only; never run |
| no IEND chunk / no end-of-image marker | truncated PNG or JPEG | re-download; view_image refuses broken PNGs |
| no %%EOF marker near the end | truncated PDF, or data appended | open with pdf-toolkit; it may still work |
| a ZIP archive (or other data) is appended after the PDF's last %%EOF | a polyglot file or a hidden payload | `bin_tool.py carve FILE` lists it; do not open the payload |
| unsafe to open: … inflates …x / declares a DOCTYPE / false size fields / overlapping members | a zip bomb, an XML bomb or a crafted archive | do not extract or open it; other Desk skills refuse it (the limits' environment variables are named for a trusted file) |
| the ZIP central directory at the end is missing | a truncated download or damaged Office/ODF/EPUB file | ask for a complete copy; the archives skill may salvage members |
| declares the main part …, but the package does not contain it | a damaged package, or a ZIP pretending to be one | ask for the real file |
| the first 64 KB are text … but samples further in are binary | a disk image, memory dump or container with a text header | `bin_tool.py map` and `carve` |
| the file is empty (0 bytes) | nothing to read | say so |
| encoded as cp1252 (…), not UTF-8 | legacy text encoding | `text_tool.py convert FILE OUT --to utf-8` |
| bidirectional control characters | text may display differently from its logical order (Trojan Source) | `text_tool.py invisible FILE` |
| certificate expired on … | an X.509 certificate past its validity | report it |
| Python pickle | loading it runs code | never unpickle untrusted files |

## Groups and routing

| group | skill | first command | types |
|---|---|---|---|
| word | word-documents | `docx_info.py` | docx, docm, dotx, dotm, doc, odt, fodt, rtf, Word 2003 XML, WordPerfect |
| pdf | pdf-toolkit | `pdf_info.py` | PDF |
| spreadsheet | spreadsheets | `sheet_info.py` | xlsx, xlsm, xltx, xlsb, xls, ods, fods, Excel 2003 XML |
| presentation | presentations | `pptx_info.py` | pptx, pptm, potx, ppsx, ppt, odp |
| image | images | `img_info.py` (SVG: `img_view.py`) | PNG, APNG, JPEG, GIF, WebP, TIFF, BMP, ICO, CUR, ICNS, HEIC, AVIF, JPEG XL, JPEG 2000, PSD, SVG, camera RAW, QOI, EXR, HDR, DDS, TGA, PCX, Netpbm, XCF, Krita, OpenRaster, EMF, WMF, XPM, XBM, SGI, Sun raster, FITS, MPO and more |
| font | images | `font_tool.py info` | TTF, OTF, TTC, WOFF, WOFF2, EOT, Type 1 |
| audio, video | audio-video | `media_info.py` | MP3, WAV, FLAC, Ogg, Opus, M4A, AAC, AIFF, AMR, MIDI, WMA, APE, WavPack, AU, CAF, DSD, MKA, AC-3; MP4, MOV, 3GP, MKV, WebM, AVI, WMV, FLV, MPEG-PS/TS, OGV, RealMedia, Y4M |
| subtitles | audio-video | `subtitles.py info` | SRT, VTT, ASS/SSA, MicroDVD |
| data | data-files | `data_info.py` (plists, feeds, glTF: `data_tree.py outline`) | CSV, TSV, JSON, JSON Lines, HAR, JSON Schema, XML, YAML, TOML, INI, Parquet, Arrow, Avro, SPSS, Stata, SAS, OPML, sitemaps, XML plists |
| database | data-files | `data_info.py` | SQLite, DuckDB (GeoPackage and MBTiles too) |
| archive | archives | `arc_list.py` | ZIP and zip-based packages (JAR, APK, AAB, IPA, XPI, CRX, wheel, NuGet, VSIX, CBZ), TAR and tar.gz/bz2/xz/zst, gzip, bzip2, xz, lzma, zstd, lz4, lzip, .Z, 7z, RAR (1.x to 5), CAB, cpio, ar, deb, RPM, LHA, ARJ, XAR, zlib; ISO images |
| markup, ebook, notebook | markup-ebooks | `mk_read.py`, `html_extract.py`, `epub_tool.py info`, `nb_tool.py outline` | Markdown, HTML, reStructuredText, LaTeX, Org, AsciiDoc, Typst, MediaWiki, Textile, Creole, man pages, POD, DocBook, JATS, RSS/Atom, BibTeX; EPUB, FB2; Jupyter |
| email, calendar, contacts | email-calendar | `mail_read.py`, `mbox_tool.py index`, `ics_tool.py read`, `vcf_tool.py read` | .eml, .emlx, .msg, mbox; .ics; .vcf |
| text, code, log | file-inspector | `text_tool.py info` / `log`; SQL dumps: `text_tool.py map` | plain text, source code (100+ languages), diffs, logs |
| executable, system | file-inspector | `bin_tool.py strings` / `hex` | PE (and NE/LE/LX, MS-DOS), ELF, Mach-O (and universal), Java class, WebAssembly, DEX, .pyc, Lua bytecode, MSI, COFF; shortcuts, ICC profiles, minidumps, .DS_Store, registry hives and exports, event logs, pcap/pcapng, Git packs and indexes, Thumbs.db |
| disk-image | file-inspector | `bin_tool.py carve` (ISO: archives) | ISO 9660, UDF, VHD, VHDX, VMDK, QCOW2, SquashFS, raw disks with GPT/MBR partitions and ext/NTFS/FAT/exFAT/HFS+/APFS, UF2 |
| unknown | file-inspector | `bin_tool.py map` | high-entropy data, unknown binary data |
| keys | none (details are in the result) | | PEM (certificates, chains, keys, CSRs, CRLs), DER certificates and keys, PKCS#12, SSH keys and known_hosts, PuTTY keys, OpenPGP, Java key stores |
| encrypted | none | | see Warnings |
| 3d, gis | none, except text and JSON/XML variants | | STL, OBJ, PLY, glTF/GLB, FBX, 3MF, USD/USDZ, Blender, COLLADA, DWG, DXF, STEP; shapefiles, KML/KMZ, GPX, OSM, GeoJSON, TopoJSON, GeoPackage, MBTiles, FlatGeobuf |
| unsupported | none; zip-based ones go to archives (`arc_list.py`) | | Keynote/Pages/Numbers (they hold a preview image to extract), Visio .vsdx, ODG, XPS, Krita/OpenRaster layers; and with no skill: Visio .vsd, Publisher, PostScript/EPS, DjVu, HWP, MNG/JNG, BPG, DICOM, SWF, Access, MOBI/AZW, CHM, PST/OST, Garmin FIT, GEDCOM |

A type with no skill carries a `note` saying so and what can help: DMG and WIM (mount or extract them on their
own system), binary plists (Python's plistlib), HDF5, NetCDF and MATLAB files (`bin_tool.py strings` shows their
names), NumPy arrays, ORC and Feather v1 (numpy and pyarrow in the data-files runtime), dBASE tables, torrents.

## Confidence

- **high** (≥ 0.85): a signature and a structural check agree (a PNG with a valid IHDR, a ZIP whose content
  types name a workbook), or text that parses completely (JSON, TOML, a certificate).
- **medium** (0.6-0.85): one strong hint (a single MP3 frame, a text heuristic such as a code language named by its
  extension, a ZIP whose central directory is damaged).
- **low** (< 0.6): a guess (puremagic's long tail, an OLE2 file of unknown kind, entropy alone).

Extension mismatches are reported only when the content is certain enough and the families differ: .jpg for a PNG
is a mismatch, .zip for a JAR is not, and neither is .txt for any text format.
