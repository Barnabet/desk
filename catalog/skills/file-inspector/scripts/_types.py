"""The file-type table: what each detected type is called, its usual extensions, its group and the Desk skill for it.

Detection lives in _sniff.py; this module is data plus the routing rules (which skill, which first command).
Standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FT:
    id: str
    desc: str
    mime: str
    exts: tuple[str, ...]
    group: str


def _t(id: str, desc: str, mime: str, exts: str, group: str) -> FT:
    return FT(id, desc, mime, tuple(f".{e}" for e in exts.split()) if exts else (), group)


_ALL = [
    # ── Word processing ──
    _t("docx", "Word document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx", "word"),
    _t("docm", "Word macro-enabled document", "application/vnd.ms-word.document.macroEnabled.12", "docm", "word"),
    _t("dotx", "Word template", "application/vnd.openxmlformats-officedocument.wordprocessingml.template", "dotx", "word"),
    _t("dotm", "Word macro-enabled template", "application/vnd.ms-word.template.macroEnabled.12", "dotm", "word"),
    _t("doc", "Word 97-2003 document", "application/msword", "doc dot", "word"),
    _t("odt", "OpenDocument text", "application/vnd.oasis.opendocument.text", "odt ott", "word"),
    _t("fodt", "Flat OpenDocument text (XML)", "application/vnd.oasis.opendocument.text-flat-xml", "fodt", "word"),
    _t("rtf", "Rich Text Format document", "application/rtf", "rtf", "word"),
    _t("wordml", "Word 2003 XML document", "application/xml", "xml", "word"),
    _t("wpd", "WordPerfect document", "application/vnd.wordperfect", "wpd", "word"),
    _t("hwp", "Hangul (HWP) document", "application/x-hwp", "hwp", "unsupported"),
    # ── PDF and print ──
    _t("pdf", "PDF document", "application/pdf", "pdf", "pdf"),
    _t("xps", "XPS document", "application/oxps", "xps oxps", "unsupported"),
    _t("postscript", "PostScript document", "application/postscript", "ps", "unsupported"),
    _t("eps", "Encapsulated PostScript", "application/postscript", "eps epsf", "unsupported"),
    _t("djvu", "DjVu document", "image/vnd.djvu", "djvu djv", "unsupported"),
    # ── Spreadsheets ──
    _t("xlsx", "Excel workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx", "spreadsheet"),
    _t("xlsm", "Excel macro-enabled workbook", "application/vnd.ms-excel.sheet.macroEnabled.12", "xlsm", "spreadsheet"),
    _t("xltx", "Excel template", "application/vnd.openxmlformats-officedocument.spreadsheetml.template", "xltx xltm", "spreadsheet"),
    _t("xlsb", "Excel binary workbook", "application/vnd.ms-excel.sheet.binary.macroEnabled.12", "xlsb", "spreadsheet"),
    _t("xls", "Excel 97-2003 workbook", "application/vnd.ms-excel", "xls xlt", "spreadsheet"),
    _t("ods", "OpenDocument spreadsheet", "application/vnd.oasis.opendocument.spreadsheet", "ods ots", "spreadsheet"),
    _t("fods", "Flat OpenDocument spreadsheet (XML)", "application/vnd.oasis.opendocument.spreadsheet-flat-xml", "fods", "spreadsheet"),
    _t("spreadsheetml", "Excel 2003 XML spreadsheet", "application/xml", "xml", "spreadsheet"),
    # ── Presentations ──
    _t("pptx", "PowerPoint presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx", "presentation"),
    _t("pptm", "PowerPoint macro-enabled presentation", "application/vnd.ms-powerpoint.presentation.macroEnabled.12", "pptm", "presentation"),
    _t("potx", "PowerPoint template", "application/vnd.openxmlformats-officedocument.presentationml.template", "potx potm", "presentation"),
    _t("ppsx", "PowerPoint slide show", "application/vnd.openxmlformats-officedocument.presentationml.slideshow", "ppsx ppsm", "presentation"),
    _t("ppt", "PowerPoint 97-2003 presentation", "application/vnd.ms-powerpoint", "ppt pps pot", "presentation"),
    _t("odp", "OpenDocument presentation", "application/vnd.oasis.opendocument.presentation", "odp otp", "presentation"),
    _t("key", "Apple Keynote presentation", "application/vnd.apple.keynote", "key", "unsupported"),
    _t("pages", "Apple Pages document", "application/vnd.apple.pages", "pages", "unsupported"),
    _t("numbers", "Apple Numbers spreadsheet", "application/vnd.apple.numbers", "numbers", "unsupported"),
    _t("vsdx", "Visio drawing", "application/vnd.ms-visio.drawing", "vsdx vsdm", "unsupported"),
    _t("vsd", "Visio 2003-2010 drawing", "application/vnd.visio", "vsd", "unsupported"),
    _t("pub", "Publisher document", "application/x-mspublisher", "pub", "unsupported"),
    _t("odg", "OpenDocument drawing", "application/vnd.oasis.opendocument.graphics", "odg", "unsupported"),
    _t("ooxml-encrypted", "Password-protected Office document (docx, xlsx or pptx)", "application/x-ooxml-encrypted", "docx xlsx pptx docm xlsm pptm", "encrypted"),
    # ── Images ──
    _t("png", "PNG image", "image/png", "png", "image"),
    _t("apng", "Animated PNG", "image/apng", "png apng", "image"),
    _t("jpeg", "JPEG image", "image/jpeg", "jpg jpeg jpe jfif", "image"),
    _t("gif", "GIF image", "image/gif", "gif", "image"),
    _t("webp", "WebP image", "image/webp", "webp", "image"),
    _t("tiff", "TIFF image", "image/tiff", "tif tiff", "image"),
    _t("bmp", "BMP image", "image/bmp", "bmp dib", "image"),
    _t("ico", "Windows icon", "image/vnd.microsoft.icon", "ico", "image"),
    _t("cur", "Windows cursor", "image/x-win-bitmap", "cur", "image"),
    _t("icns", "macOS icon", "image/icns", "icns", "image"),
    _t("heic", "HEIF/HEIC image", "image/heic", "heic heif hif", "image"),
    _t("avif", "AVIF image", "image/avif", "avif", "image"),
    _t("jxl", "JPEG XL image", "image/jxl", "jxl", "image"),
    _t("jp2", "JPEG 2000 image", "image/jp2", "jp2 j2k jpf jpx", "image"),
    _t("psd", "Photoshop document", "image/vnd.adobe.photoshop", "psd psb", "image"),
    _t("svg", "SVG vector image", "image/svg+xml", "svg", "image"),
    _t("svgz", "Compressed SVG", "image/svg+xml", "svgz", "image"),
    _t("raw", "Camera RAW image", "image/x-raw", "cr2 cr3 nef nrw arw srf sr2 dng raf orf rw2 pef srw x3f 3fr erf kdc mrw", "image"),
    _t("qoi", "QOI image", "image/qoi", "qoi", "image"),
    _t("exr", "OpenEXR image", "image/x-exr", "exr", "image"),
    _t("hdr", "Radiance HDR image", "image/vnd.radiance", "hdr pic", "image"),
    _t("dds", "DirectDraw surface texture", "image/vnd.ms-dds", "dds", "image"),
    _t("tga", "Targa image", "image/x-tga", "tga", "image"),
    _t("pcx", "PCX image", "image/x-pcx", "pcx", "image"),
    _t("pnm", "Netpbm image", "image/x-portable-anymap", "pbm pgm ppm pnm pam", "image"),
    _t("xcf", "GIMP image", "image/x-xcf", "xcf", "image"),
    _t("kra", "Krita image", "application/x-krita", "kra", "image"),
    _t("ora", "OpenRaster image", "image/openraster", "ora", "image"),
    _t("emf", "Windows enhanced metafile", "image/emf", "emf", "image"),
    _t("wmf", "Windows metafile", "image/wmf", "wmf", "image"),
    _t("xpm", "X PixMap image", "image/x-xpixmap", "xpm", "image"),
    _t("sgi", "SGI image", "image/sgi", "sgi rgb rgba bw", "image"),
    _t("sun-raster", "Sun raster image", "image/x-sun-raster", "ras sun", "image"),
    _t("fits", "FITS astronomical image/data", "image/fits", "fits fit fts", "image"),
    _t("msp", "Microsoft Paint image", "image/x-msp", "msp", "image"),
    _t("fli", "FLI/FLC animation", "video/x-fli", "fli flc", "image"),
    _t("pixar", "Pixar image", "image/x-pixar", "pxr", "image"),
    _t("pil-im", "IFUNC/PIL image (.im)", "image/x-im", "im", "image"),
    _t("mpo", "Multi-picture JPEG (MPO, stereo)", "image/mpo", "mpo", "image"),
    _t("xbm", "X BitMap image (C source)", "image/x-xbitmap", "xbm", "image"),
    _t("mng", "MNG animation (Multiple-image Network Graphics)", "video/x-mng", "mng jng", "unsupported"),
    _t("bpg", "BPG image (Better Portable Graphics)", "image/bpg", "bpg", "unsupported"),
    _t("dicom", "DICOM medical image", "application/dicom", "dcm dicom", "unsupported"),
    # ── Fonts ──
    _t("ttf", "TrueType font", "font/ttf", "ttf", "font"),
    _t("otf", "OpenType font (CFF)", "font/otf", "otf", "font"),
    _t("ttc", "TrueType font collection", "font/collection", "ttc otc", "font"),
    _t("woff", "WOFF web font", "font/woff", "woff", "font"),
    _t("woff2", "WOFF2 web font", "font/woff2", "woff2", "font"),
    _t("eot", "Embedded OpenType font", "application/vnd.ms-fontobject", "eot", "font"),
    _t("pfb", "PostScript Type 1 font", "application/x-font-type1", "pfb pfa", "font"),
    # ── Audio ──
    _t("mp3", "MP3 audio", "audio/mpeg", "mp3", "audio"),
    _t("wav", "WAV audio", "audio/wav", "wav wave", "audio"),
    _t("flac", "FLAC audio", "audio/flac", "flac", "audio"),
    _t("ogg", "Ogg Vorbis audio", "audio/ogg", "ogg oga", "audio"),
    _t("opus", "Opus audio (Ogg)", "audio/ogg; codecs=opus", "opus ogg", "audio"),
    _t("m4a", "MPEG-4 audio", "audio/mp4", "m4a m4b m4p m4r", "audio"),
    _t("aac", "AAC audio (ADTS)", "audio/aac", "aac", "audio"),
    _t("aiff", "AIFF audio", "audio/aiff", "aif aiff aifc", "audio"),
    _t("amr", "AMR audio", "audio/amr", "amr", "audio"),
    _t("midi", "MIDI music", "audio/midi", "mid midi", "audio"),
    _t("wma", "Windows Media audio", "audio/x-ms-wma", "wma", "audio"),
    _t("ape", "Monkey's Audio", "audio/ape", "ape", "audio"),
    _t("wavpack", "WavPack audio", "audio/x-wavpack", "wv", "audio"),
    _t("au", "Sun/NeXT audio", "audio/basic", "au snd", "audio"),
    _t("caf", "Core Audio file", "audio/x-caf", "caf", "audio"),
    _t("dsf", "DSD audio", "audio/dsd", "dsf dff", "audio"),
    _t("mka", "Matroska audio", "audio/x-matroska", "mka", "audio"),
    _t("ac3", "Dolby AC-3 audio", "audio/ac3", "ac3", "audio"),
    # ── Video ──
    _t("mp4", "MPEG-4 video", "video/mp4", "mp4 m4v", "video"),
    _t("mov", "QuickTime video", "video/quicktime", "mov qt", "video"),
    _t("3gp", "3GPP video", "video/3gpp", "3gp 3g2", "video"),
    _t("mkv", "Matroska video", "video/x-matroska", "mkv", "video"),
    _t("webm", "WebM video", "video/webm", "webm", "video"),
    _t("avi", "AVI video", "video/x-msvideo", "avi", "video"),
    _t("wmv", "Windows Media video (ASF)", "video/x-ms-wmv", "wmv asf", "video"),
    _t("flv", "Flash video", "video/x-flv", "flv", "video"),
    _t("mpeg-ps", "MPEG program stream video", "video/mpeg", "mpg mpeg vob m2p", "video"),
    _t("mpeg-ts", "MPEG transport stream video", "video/mp2t", "ts mts m2ts", "video"),
    _t("ogv", "Ogg video (Theora)", "video/ogg", "ogv ogg", "video"),
    _t("rm", "RealMedia", "application/vnd.rn-realmedia", "rm rmvb ra", "video"),
    _t("swf", "Flash movie (SWF)", "application/x-shockwave-flash", "swf", "unsupported"),
    _t("y4m", "YUV4MPEG video", "video/x-yuv4mpeg", "y4m", "video"),
    # ── Subtitles ──
    _t("srt", "SubRip subtitles", "application/x-subrip", "srt", "subtitles"),
    _t("vtt", "WebVTT subtitles", "text/vtt", "vtt", "subtitles"),
    _t("ass", "ASS/SSA subtitles", "text/x-ssa", "ass ssa", "subtitles"),
    _t("sub-microdvd", "MicroDVD subtitles", "text/x-microdvd", "sub", "subtitles"),
    # ── Data files ──
    _t("csv", "CSV table", "text/csv", "csv", "data"),
    _t("tsv", "Tab-separated table", "text/tab-separated-values", "tsv tab", "data"),
    _t("psv", "Delimited text table", "text/plain", "psv txt dat", "data"),
    _t("json", "JSON", "application/json", "json", "data"),
    _t("jsonl", "JSON Lines", "application/jsonl", "jsonl ndjson ldjson", "data"),
    _t("geojson", "GeoJSON", "application/geo+json", "geojson json", "gis"),
    _t("topojson", "TopoJSON", "application/json", "topojson json", "gis"),
    _t("har", "HTTP archive (HAR)", "application/json", "har", "data"),
    _t("json-schema", "JSON Schema", "application/schema+json", "json", "data"),
    _t("xml", "XML document", "application/xml", "xml", "data"),
    _t("yaml", "YAML", "application/yaml", "yaml yml", "data"),
    _t("toml", "TOML", "application/toml", "toml", "data"),
    _t("ini", "INI configuration", "text/plain", "ini cfg conf cnf inf", "data"),
    _t("properties", "Key=value settings (.env / .properties)", "text/plain", "env properties", "data"),
    _t("parquet", "Apache Parquet table", "application/vnd.apache.parquet", "parquet pq", "data"),
    _t("arrow", "Apache Arrow IPC file", "application/vnd.apache.arrow.file", "arrow feather ipc", "data"),
    _t("feather-v1", "Feather v1 table", "application/octet-stream", "feather", "data"),
    _t("avro", "Apache Avro data", "application/avro", "avro", "data"),
    _t("orc", "Apache ORC table", "application/octet-stream", "orc", "data"),
    _t("sav", "SPSS data file", "application/x-spss-sav", "sav zsav", "data"),
    _t("dta", "Stata data file", "application/x-stata-dta", "dta", "data"),
    _t("sas7bdat", "SAS data set", "application/x-sas-data", "sas7bdat", "data"),
    _t("xpt", "SAS transport file", "application/x-sas-xport", "xpt", "data"),
    _t("hdf5", "HDF5 data", "application/x-hdf5", "h5 hdf5 hdf he5 nc", "data"),
    _t("netcdf", "NetCDF data", "application/x-netcdf", "nc cdf", "data"),
    _t("npy", "NumPy array", "application/octet-stream", "npy", "data"),
    _t("npz", "NumPy array archive", "application/zip", "npz", "data"),
    _t("mat", "MATLAB data file", "application/x-matlab-data", "mat", "data"),
    _t("pickle", "Python pickle (runs code when loaded)", "application/octet-stream", "pkl pickle p", "data"),
    _t("sql", "SQL script or dump", "application/sql", "sql", "data"),
    _t("bibtex", "BibTeX bibliography", "application/x-bibtex", "bib", "markup"),
    # ── Databases ──
    _t("sqlite", "SQLite database", "application/vnd.sqlite3", "sqlite sqlite3 db db3 s3db sl3", "database"),
    _t("geopackage", "GeoPackage (SQLite)", "application/geopackage+sqlite3", "gpkg", "gis"),
    _t("mbtiles", "MBTiles map tiles (SQLite)", "application/vnd.sqlite3", "mbtiles", "gis"),
    _t("duckdb", "DuckDB database", "application/octet-stream", "duckdb ddb db", "database"),
    _t("access", "Microsoft Access database", "application/x-msaccess", "mdb accdb", "unsupported"),
    _t("dbf", "dBASE table", "application/dbf", "dbf", "database"),
    # ── Archives and compression ──
    _t("zip", "ZIP archive", "application/zip", "zip zipx", "archive"),
    _t("jar", "Java archive", "application/java-archive", "jar war ear", "archive"),
    _t("apk", "Android package", "application/vnd.android.package-archive", "apk", "archive"),
    _t("aab", "Android app bundle", "application/octet-stream", "aab", "archive"),
    _t("ipa", "iOS app package", "application/octet-stream", "ipa", "archive"),
    _t("xpi", "Firefox extension", "application/x-xpinstall", "xpi", "archive"),
    _t("crx", "Chrome extension", "application/x-chrome-extension", "crx", "archive"),
    _t("whl", "Python wheel", "application/zip", "whl", "archive"),
    _t("nupkg", "NuGet package", "application/zip", "nupkg snupkg", "archive"),
    _t("vsix", "VSIX extension package", "application/zip", "vsix", "archive"),
    _t("cbz", "Comic book archive (ZIP)", "application/vnd.comicbook+zip", "cbz", "archive"),
    _t("tar", "TAR archive", "application/x-tar", "tar", "archive"),
    _t("tar.gz", "Gzip-compressed TAR archive", "application/gzip", "tgz gz", "archive"),
    _t("tar.bz2", "Bzip2-compressed TAR archive", "application/x-bzip2", "tbz2 tbz bz2", "archive"),
    _t("tar.xz", "XZ-compressed TAR archive", "application/x-xz", "txz xz", "archive"),
    _t("tar.zst", "Zstandard-compressed TAR archive", "application/zstd", "tzst zst", "archive"),
    _t("gzip", "Gzip-compressed file", "application/gzip", "gz gzip", "archive"),
    _t("bzip2", "Bzip2-compressed file", "application/x-bzip2", "bz2", "archive"),
    _t("xz", "XZ-compressed file", "application/x-xz", "xz", "archive"),
    _t("lzma", "LZMA-compressed file", "application/x-lzma", "lzma", "archive"),
    _t("zstd", "Zstandard-compressed file", "application/zstd", "zst zstd", "archive"),
    _t("lz4", "LZ4-compressed file", "application/x-lz4", "lz4", "archive"),
    _t("lzip", "Lzip-compressed file", "application/x-lzip", "lz", "archive"),
    _t("compress-z", "Unix compress (.Z) file", "application/x-compress", "z", "archive"),
    _t("7z", "7-Zip archive", "application/x-7z-compressed", "7z", "archive"),
    _t("rar", "RAR archive", "application/vnd.rar", "rar", "archive"),
    _t("cab", "Microsoft Cabinet archive", "application/vnd.ms-cab-compressed", "cab", "archive"),
    _t("cpio", "cpio archive", "application/x-cpio", "cpio", "archive"),
    _t("ar", "ar archive (static library)", "application/x-archive", "a ar lib", "archive"),
    _t("deb", "Debian package", "application/vnd.debian.binary-package", "deb udeb", "archive"),
    _t("rpm", "RPM package", "application/x-rpm", "rpm", "archive"),
    _t("lzh", "LHA archive", "application/x-lzh-compressed", "lzh lha", "archive"),
    _t("arj", "ARJ archive", "application/x-arj", "arj", "archive"),
    _t("xar", "XAR archive (macOS .pkg)", "application/x-xar", "xar pkg", "archive"),
    _t("wim", "Windows imaging archive", "application/x-ms-wim", "wim esd", "archive"),
    _t("zlib", "zlib-compressed data", "application/zlib", "zz zlib", "archive"),
    # ── Disk images ──
    _t("iso", "ISO 9660 disc image", "application/x-iso9660-image", "iso", "disk-image"),
    _t("udf", "UDF disc image", "application/x-iso9660-image", "iso udf", "disk-image"),
    _t("dmg", "macOS disk image", "application/x-apple-diskimage", "dmg", "disk-image"),
    _t("vhd", "Virtual hard disk (VHD)", "application/x-vhd", "vhd", "disk-image"),
    _t("vhdx", "Virtual hard disk (VHDX)", "application/x-vhdx", "vhdx", "disk-image"),
    _t("vmdk", "VMware disk", "application/x-vmdk", "vmdk", "disk-image"),
    _t("qcow2", "QEMU disk image", "application/x-qemu-disk", "qcow2 qcow", "disk-image"),
    _t("squashfs", "SquashFS filesystem", "application/octet-stream", "squashfs sqfs snap", "disk-image"),
    _t("fs-image", "Disk or filesystem image", "application/octet-stream", "img ima dsk raw bin iso vfd hdd", "disk-image"),
    _t("uf2", "UF2 firmware image", "application/octet-stream", "uf2", "disk-image"),
    # ── Markup and ebooks ──
    _t("markdown", "Markdown text", "text/markdown", "md markdown mdown mkd mdx", "markup"),
    _t("html", "HTML page", "text/html", "html htm xhtml shtml mhtml", "markup"),
    _t("rst", "reStructuredText", "text/x-rst", "rst rest", "markup"),
    _t("latex", "LaTeX source", "application/x-tex", "tex latex ltx sty cls", "markup"),
    _t("org", "Org-mode text", "text/org", "org", "markup"),
    _t("asciidoc", "AsciiDoc text", "text/asciidoc", "adoc asciidoc asc", "markup"),
    _t("typst", "Typst source", "text/x-typst", "typ", "markup"),
    _t("mediawiki", "MediaWiki markup", "text/x-wiki", "wiki mediawiki", "markup"),
    _t("textile", "Textile markup", "text/x-textile", "textile", "markup"),
    _t("creole", "Creole wiki markup", "text/x-creole", "creole", "markup"),
    _t("man", "Unix manual page (troff)", "text/troff", "man 1 2 3 4 5 6 7 8 9 roff", "markup"),
    _t("pod", "Perl POD documentation", "text/x-pod", "pod", "markup"),
    _t("docbook", "DocBook XML", "application/docbook+xml", "dbk docbook xml", "markup"),
    _t("jats", "JATS article XML", "application/jats+xml", "jats xml", "markup"),
    _t("epub", "EPUB ebook", "application/epub+zip", "epub", "ebook"),
    _t("fb2", "FictionBook ebook", "application/x-fictionbook+xml", "fb2", "ebook"),
    _t("mobi", "Mobipocket/Kindle ebook", "application/x-mobipocket-ebook", "mobi azw azw3 prc", "unsupported"),
    _t("ipynb", "Jupyter notebook", "application/x-ipynb+json", "ipynb", "notebook"),
    _t("rss", "RSS feed", "application/rss+xml", "rss xml", "markup"),
    _t("atom", "Atom feed", "application/atom+xml", "atom xml", "markup"),
    _t("opml", "OPML outline", "text/x-opml", "opml", "data"),
    _t("sitemap", "XML sitemap", "application/xml", "xml", "data"),
    _t("chm", "Compiled HTML help", "application/vnd.ms-htmlhelp", "chm", "unsupported"),
    # ── Email, calendar, contacts ──
    _t("eml", "Email message (RFC 822)", "message/rfc822", "eml msg mht", "email"),
    _t("emlx", "Apple Mail message", "message/rfc822", "emlx", "email"),
    _t("msg", "Outlook message", "application/vnd.ms-outlook", "msg", "email"),
    _t("mbox", "Mailbox (mbox)", "application/mbox", "mbox mbx mbs", "email"),
    _t("pst", "Outlook data file (PST/OST)", "application/vnd.ms-outlook-pst", "pst ost", "unsupported"),
    _t("ics", "iCalendar", "text/calendar", "ics ical ifb icalendar", "calendar"),
    _t("vcf", "vCard contacts", "text/vcard", "vcf vcard", "contacts"),
    # ── Text, code, logs ──
    _t("text", "Plain text", "text/plain", "txt text me 1st asc", "text"),
    _t("log", "Log file", "text/plain", "log out err", "log"),
    _t("diff", "Diff / patch", "text/x-diff", "diff patch", "code"),
    _t("code", "Source code", "text/plain", "", "code"),
    # ── Executables and code containers ──
    _t("pe", "Windows executable (PE)", "application/vnd.microsoft.portable-executable", "exe dll sys scr cpl ocx efi drv mui", "executable"),
    _t("elf", "ELF executable", "application/x-executable", "so o elf bin axf ko", "executable"),
    _t("macho", "Mach-O executable", "application/x-mach-binary", "dylib bundle o", "executable"),
    _t("macho-fat", "Universal (fat) Mach-O binary", "application/x-mach-binary", "dylib", "executable"),
    _t("java-class", "Java class file", "application/java-vm", "class", "executable"),
    _t("wasm", "WebAssembly module", "application/wasm", "wasm", "executable"),
    _t("dex", "Android DEX bytecode", "application/octet-stream", "dex odex", "executable"),
    _t("pyc", "Python bytecode", "application/x-python-code", "pyc pyo", "executable"),
    _t("luac", "Lua bytecode", "application/x-lua-bytecode", "luac", "executable"),
    _t("msi", "Windows Installer package", "application/x-msi", "msi msp msm", "executable"),
    _t("lnk", "Windows shortcut", "application/x-ms-shortcut", "lnk", "system"),
    _t("icc", "ICC color profile", "application/vnd.iccprofile", "icc icm", "system"),
    _t("coff", "COFF object file", "application/octet-stream", "obj o", "executable"),
    _t("minidump", "Windows minidump (crash dump)", "application/x-dmp", "dmp mdmp", "system"),
    # ── Certificates, keys, secrets ──
    _t("pem", "PEM certificates or keys", "application/x-pem-file", "pem crt cer key pub csr crl p7b der", "keys"),
    _t("der-cert", "X.509 certificate (DER)", "application/pkix-cert", "cer crt der", "keys"),
    _t("der-key", "Private key (DER, PKCS#8)", "application/pkcs8", "key der p8 pk8", "keys"),
    _t("pkcs12", "PKCS#12 key store (.p12/.pfx)", "application/x-pkcs12", "p12 pfx", "keys"),
    _t("ssh-public-key", "SSH public key(s)", "text/plain", "pub", "keys"),
    _t("ssh-private-key", "SSH private key", "text/plain", "", "keys"),
    _t("putty-key", "PuTTY private key", "text/plain", "ppk", "keys"),
    _t("pgp", "OpenPGP data", "application/pgp-keys", "asc gpg pgp sig kbx", "keys"),
    _t("jks", "Java key store", "application/x-java-keystore", "jks keystore", "keys"),
    _t("jceks", "Java JCEKS key store", "application/x-java-jce-keystore", "jceks", "keys"),
    _t("keepass", "KeePass password database", "application/x-keepass", "kdbx kdb", "encrypted"),
    _t("age", "age-encrypted file", "application/octet-stream", "age", "encrypted"),
    _t("openssl-enc", "OpenSSL-encrypted file (Salted__)", "application/octet-stream", "enc bin", "encrypted"),
    _t("ansible-vault", "Ansible Vault encrypted file", "text/plain", "yml yaml vault", "encrypted"),
    _t("luks", "LUKS encrypted volume", "application/octet-stream", "img luks", "encrypted"),
    _t("bitlocker", "BitLocker encrypted volume", "application/octet-stream", "img bek", "encrypted"),
    # ── 3D, CAD, GIS ──
    _t("stl", "STL 3D model", "model/stl", "stl", "3d"),
    _t("obj3d", "Wavefront OBJ 3D model", "model/obj", "obj", "3d"),
    _t("ply", "PLY 3D model", "model/ply", "ply", "3d"),
    _t("glb", "glTF binary 3D model", "model/gltf-binary", "glb", "3d"),
    _t("gltf", "glTF 3D model (JSON)", "model/gltf+json", "gltf", "3d"),
    _t("fbx", "FBX 3D model", "application/octet-stream", "fbx", "3d"),
    _t("3mf", "3MF 3D print model", "model/3mf", "3mf", "3d"),
    _t("usdz", "USDZ 3D model", "model/vnd.usdz+zip", "usdz", "3d"),
    _t("usd", "Pixar USD scene", "model/vnd.usd", "usd usdc usda", "3d"),
    _t("blend", "Blender project", "application/x-blender", "blend", "3d"),
    _t("collada", "COLLADA 3D model", "model/vnd.collada+xml", "dae", "3d"),
    _t("dwg", "AutoCAD drawing (DWG)", "image/vnd.dwg", "dwg", "3d"),
    _t("dxf", "AutoCAD DXF drawing", "image/vnd.dxf", "dxf", "3d"),
    _t("step", "STEP CAD model", "model/step", "step stp", "3d"),
    _t("shapefile", "ESRI shapefile", "application/x-esri-shape", "shp shx", "gis"),
    _t("kml", "KML (Google Earth)", "application/vnd.google-earth.kml+xml", "kml", "gis"),
    _t("kmz", "KMZ (zipped KML)", "application/vnd.google-earth.kmz", "kmz", "gis"),
    _t("gpx", "GPX GPS track", "application/gpx+xml", "gpx", "gis"),
    _t("osm", "OpenStreetMap XML data", "application/xml", "osm xml", "gis"),
    _t("flatgeobuf", "FlatGeobuf", "application/octet-stream", "fgb", "gis"),
    # ── System and misc ──
    _t("plist", "Property list (binary)", "application/x-plist", "plist", "data"),
    _t("xml-plist", "Property list (XML)", "application/x-plist", "plist xml", "data"),
    _t("ds-store", "macOS Finder metadata (.DS_Store)", "application/octet-stream", "ds_store", "system"),
    _t("registry-hive", "Windows registry hive", "application/octet-stream", "dat hiv", "system"),
    _t("reg", "Windows registry export", "text/plain", "reg", "system"),
    _t("evtx", "Windows event log", "application/octet-stream", "evtx", "system"),
    _t("pcap", "Network capture (pcap)", "application/vnd.tcpdump.pcap", "pcap cap dmp", "system"),
    _t("pcapng", "Network capture (pcapng)", "application/x-pcapng", "pcapng", "system"),
    _t("torrent", "BitTorrent metainfo", "application/x-bittorrent", "torrent", "data"),
    _t("fit", "Garmin FIT activity data", "application/vnd.ant.fit", "fit", "unsupported"),
    _t("gedcom", "GEDCOM genealogy data", "text/plain", "ged gedcom", "unsupported"),
    _t("registry-pol", "Windows Group Policy registry file (Registry.pol)", "application/octet-stream", "pol", "system"),
    _t("git-pack", "Git pack file", "application/octet-stream", "pack", "system"),
    _t("git-index", "Git index", "application/octet-stream", "", "system"),
    _t("thumbs-db", "Windows thumbnail cache", "application/octet-stream", "db", "system"),
    _t("url-shortcut", "Internet shortcut", "text/plain", "url website", "system"),
    _t("desktop-entry", "Linux desktop entry", "text/plain", "desktop", "system"),
    _t("webloc", "macOS web location", "application/x-plist", "webloc", "system"),
    # ── Fallbacks ──
    _t("empty", "Empty file", "application/x-empty", "", "empty"),
    _t("encrypted-or-compressed", "High-entropy data (encrypted, compressed or random)", "application/octet-stream", "", "unknown"),
    _t("binary", "Unknown binary data", "application/octet-stream", "bin dat", "unknown"),
]

TYPES: dict[str, FT] = {t.id: t for t in _ALL}

#: Extensions that say nothing about the content (never a mismatch).
GENERIC_EXTS = {
    ".bin", ".dat", ".data", ".tmp", ".temp", ".bak", ".old", ".orig", ".backup", ".copy", ".sav~", ".swp", ".part",
    ".crdownload", ".download", ".partial", ".1", ".2", ".3", ".out", ".raw", ".dump", ".file", ".unknown", ".sample", ".blob",
}

#: Types a text heuristic lands on; their extensions are interchangeable enough that we don't warn between them.
TEXTUAL_GROUPS = {"text", "code", "log", "data", "markup", "subtitles", "calendar", "contacts", "email", "keys", "system"}

# Common source-code extensions → language name (also used to accept a code file named by its extension).
CODE_EXTS: dict[str, str] = {
    ".py": "Python", ".pyw": "Python", ".pyi": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript (JSX)", ".ts": "TypeScript", ".tsx": "TypeScript (TSX)", ".mts": "TypeScript", ".cts": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy", ".gradle": "Groovy",
    ".c": "C", ".h": "C/C++ header", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++", ".hxx": "C++",
    ".m": "Objective-C or MATLAB", ".mm": "Objective-C++", ".cs": "C#", ".fs": "F#", ".vb": "Visual Basic", ".go": "Go",
    ".rs": "Rust", ".swift": "Swift", ".rb": "Ruby", ".php": "PHP", ".pl": "Perl", ".pm": "Perl", ".lua": "Lua",
    ".r": "R", ".jl": "Julia", ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hs": "Haskell",
    ".clj": "Clojure", ".elm": "Elm", ".ml": "OCaml", ".nim": "Nim", ".zig": "Zig", ".v": "V or Verilog", ".sv": "SystemVerilog",
    ".vhd": "VHDL", ".asm": "Assembly", ".s": "Assembly", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".fish": "Fish shell",
    ".ps1": "PowerShell", ".psm1": "PowerShell", ".bat": "Batch", ".cmd": "Batch", ".css": "CSS", ".scss": "SCSS",
    ".sass": "Sass", ".less": "Less", ".vue": "Vue", ".svelte": "Svelte", ".astro": "Astro", ".sql": "SQL",
    ".graphql": "GraphQL", ".gql": "GraphQL", ".proto": "Protocol Buffers", ".tf": "Terraform (HCL)", ".hcl": "HCL",
    ".nix": "Nix", ".cmake": "CMake", ".mk": "Makefile", ".dockerfile": "Dockerfile", ".vim": "Vim script",
    ".el": "Emacs Lisp", ".lisp": "Lisp", ".scm": "Scheme", ".f90": "Fortran", ".f": "Fortran", ".pas": "Pascal",
    ".d": "D", ".cr": "Crystal", ".coffee": "CoffeeScript", ".sol": "Solidity", ".tcl": "Tcl", ".awk": "AWK",
    ".ipynb": "Jupyter", ".gd": "GDScript", ".glsl": "GLSL", ".hlsl": "HLSL", ".wgsl": "WGSL", ".cu": "CUDA",
    ".jsonp": "JavaScript (JSONP)", ".adb": "Ada", ".ads": "Ada", ".cob": "COBOL", ".cbl": "COBOL",
    ".purs": "PureScript", ".rkt": "Racket", ".fsx": "F#", ".vbs": "VBScript", ".au3": "AutoIt", ".ahk": "AutoHotkey",
}

CODE_NAMES: dict[str, str] = {
    "dockerfile": "Dockerfile", "containerfile": "Dockerfile", "makefile": "Makefile", "gnumakefile": "Makefile",
    "cmakelists.txt": "CMake", "rakefile": "Ruby", "gemfile": "Ruby", "vagrantfile": "Ruby", "jenkinsfile": "Groovy",
    "build": "Starlark", "workspace": "Starlark", "justfile": "Just", "procfile": "Procfile", "podfile": "Ruby",
}


def ext_of(name: str) -> str:
    """The lowercase extension, with the compound .tar.* forms kept together."""
    low = name.lower()
    for comp in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz4", ".tar.z"):
        if low.endswith(comp):
            return comp
    i = low.rfind(".")
    if i <= 0 or i == len(low) - 1:
        return ""
    return low[i:]


_EXT_INDEX: dict[str, list[str]] = {}
for _ft in _ALL:
    for _e in _ft.exts:
        _EXT_INDEX.setdefault(_e, []).append(_ft.id)
for _e in CODE_EXTS:
    _EXT_INDEX.setdefault(_e, []).append("code")
_EXT_INDEX.update({".tar.gz": ["tar.gz"], ".tar.bz2": ["tar.bz2"], ".tar.xz": ["tar.xz"], ".tar.zst": ["tar.zst"]})


def types_for_ext(ext: str) -> list[str]:
    return _EXT_INDEX.get(ext.lower(), [])


def group_for_ext(ext: str) -> str | None:
    """The group an extension alone suggests (for survey's fast mode)."""
    ids = types_for_ext(ext)
    return TYPES[ids[0]].group if ids else None


# ── routing: which skill, which first command ─────────────────────────────

# group → (skill, script, subcommand or None)
_GROUP_ROUTES: dict[str, tuple[str, str, str | None]] = {
    "word": ("word-documents", "docx_info.py", None),
    "pdf": ("pdf-toolkit", "pdf_info.py", None),
    "spreadsheet": ("spreadsheets", "sheet_info.py", None),
    "presentation": ("presentations", "pptx_info.py", None),
    "image": ("images", "img_info.py", None),
    "font": ("images", "font_tool.py", "info"),
    "audio": ("audio-video", "media_info.py", None),
    "video": ("audio-video", "media_info.py", None),
    "subtitles": ("audio-video", "subtitles.py", "info"),
    "data": ("data-files", "data_info.py", None),
    "database": ("data-files", "data_info.py", None),
    "archive": ("archives", "arc_list.py", None),
    "markup": ("markup-ebooks", "mk_read.py", None),
    "ebook": ("markup-ebooks", "epub_tool.py", "info"),
    "notebook": ("markup-ebooks", "nb_tool.py", "outline"),
    "email": ("email-calendar", "mail_read.py", None),
    "calendar": ("email-calendar", "ics_tool.py", "read"),
    "contacts": ("email-calendar", "vcf_tool.py", "read"),
    "text": ("file-inspector", "text_tool.py", "info"),
    "log": ("file-inspector", "text_tool.py", "log"),
    "code": ("file-inspector", "text_tool.py", "info"),
    "keys": ("", "", None),
    "executable": ("file-inspector", "bin_tool.py", "strings"),
    "system": ("file-inspector", "bin_tool.py", "hex"),
    "disk-image": ("file-inspector", "bin_tool.py", "carve"),
    "encrypted": ("", "", None),
    "unknown": ("file-inspector", "bin_tool.py", "map"),
}

# type id → (skill, script, subcommand) overriding the group route.
_TYPE_ROUTES: dict[str, tuple[str, str, str | None]] = {
    "html": ("markup-ebooks", "html_extract.py", None),
    "mbox": ("email-calendar", "mbox_tool.py", "index"),
    "csv": ("data-files", "data_info.py", None),
    "tsv": ("data-files", "data_info.py", None),
    "xml-plist": ("data-files", "data_tree.py", "outline"),
    "plist": ("", "", None),
    "hdf5": ("", "", None),
    "netcdf": ("", "", None),
    "npy": ("", "", None),
    "mat": ("", "", None),
    "dbf": ("", "", None),
    "orc": ("", "", None),
    "feather-v1": ("", "", None),
    "torrent": ("", "", None),
    "properties": ("file-inspector", "text_tool.py", "info"),
    "svg": ("images", "img_view.py", None),
    "svgz": ("images", "img_view.py", None),
    "fb2": ("markup-ebooks", "mk_read.py", None),
    "rss": ("data-files", "data_tree.py", "outline"),
    "atom": ("data-files", "data_tree.py", "outline"),
    "bibtex": ("file-inspector", "text_tool.py", "info"),
    "sql": ("file-inspector", "text_tool.py", "map"),
    "geojson": ("data-files", "data_info.py", None),
    "topojson": ("data-files", "data_tree.py", "outline"),
    "gpx": ("data-files", "data_tree.py", "outline"),
    "osm": ("data-files", "data_tree.py", "outline"),
    "kml": ("data-files", "data_tree.py", "outline"),
    "geopackage": ("data-files", "data_info.py", None),
    "mbtiles": ("data-files", "data_info.py", None),
    "gltf": ("data-files", "data_tree.py", "outline"),
    "collada": ("data-files", "data_tree.py", "outline"),
    "dxf": ("file-inspector", "text_tool.py", "info"),
    "obj3d": ("file-inspector", "text_tool.py", "head"),
    "npz": ("archives", "arc_list.py", None),
    "kmz": ("archives", "arc_list.py", None),
    "3mf": ("archives", "arc_list.py", None),
    "usdz": ("archives", "arc_list.py", None),
    "pages": ("archives", "arc_list.py", None),
    "numbers": ("archives", "arc_list.py", None),
    "key": ("archives", "arc_list.py", None),
    "vsdx": ("archives", "arc_list.py", None),
    "odg": ("archives", "arc_list.py", None),
    "xps": ("archives", "arc_list.py", None),
    "kra": ("archives", "arc_list.py", None),
    "ora": ("archives", "arc_list.py", None),
    "iso": ("archives", "arc_list.py", None),
    "wim": ("", "", None),
    "dmg": ("", "", None),
    "ssh-public-key": ("file-inspector", "text_tool.py", "head"),
    "reg": ("file-inspector", "text_tool.py", "info"),
    "url-shortcut": ("file-inspector", "text_tool.py", "head"),
    "desktop-entry": ("file-inspector", "text_tool.py", "head"),
    "ansible-vault": ("file-inspector", "text_tool.py", "head"),
    "pickle": ("file-inspector", "bin_tool.py", "strings"),
    "minidump": ("file-inspector", "bin_tool.py", "strings"),
    "lnk": ("file-inspector", "bin_tool.py", "strings"),
    "empty": ("", "", None),
}

#: What each skill is for, in a few words (used in reports).
SKILL_BLURBS = {
    "word-documents": "Word and rich text",
    "pdf-toolkit": "PDF",
    "spreadsheets": "spreadsheets",
    "presentations": "slide decks",
    "images": "images and fonts",
    "audio-video": "audio, video, subtitles",
    "data-files": "data files and databases",
    "archives": "archives and packages",
    "markup-ebooks": "markup, ebooks, notebooks",
    "email-calendar": "email, calendars, contacts",
    "file-inspector": "text, binaries, unknown files",
}

#: Notes for single types no Desk skill opens (they win over the group's note).
TYPE_NOTES = {
    "plist": "no Desk skill reads binary property lists; Python's plistlib does in a few lines (references/recipes.md)",
    "hdf5": "no Desk skill reads HDF5; bin_tool.py strings shows its dataset names",
    "netcdf": "no Desk skill reads NetCDF; bin_tool.py strings shows its variable names",
    "npy": "no Desk skill script reads NumPy arrays; numpy.load does (numpy is in the data-files runtime)",
    "mat": "no Desk skill reads MATLAB files; bin_tool.py strings shows its variable names",
    "dbf": "no Desk skill reads dBASE tables; LibreOffice opens them, or ask for a CSV export",
    "orc": "no Desk skill script reads ORC; pyarrow.orc does (pyarrow is in the data-files runtime)",
    "feather-v1": "no Desk skill script reads Feather v1; pyarrow.feather does (pyarrow is in the data-files runtime)",
    "torrent": "a BitTorrent metainfo file (bencoded): bin_tool.py strings shows the tracker and file names",
    "dmg": "no Desk skill opens macOS disk images: on a Mac the user can mount it (double-click) and share the contents; bin_tool.py carve may still find files inside",
    "wim": "no Desk skill opens Windows imaging archives: 7-Zip or DISM on Windows can extract it; bin_tool.py carve may still find files inside",
}

#: Notes for groups no Desk skill reads.
UNSUPPORTED_NOTES = {
    "keys": "certificate and key details are above; no other skill is needed (never print or upload a private key)",
    "empty": "the file is empty",
    "3d": "no Desk skill reads 3D/CAD models; identify only (text formats like OBJ, DXF and glTF can be read as text)",
    "gis": "no Desk skill renders GIS layers; GeoJSON, GPX, KML and GeoPackage can be read as data",
    "unsupported": "no Desk skill reads this format",
    "encrypted": "encrypted: nothing inside can be read without the password or key (Desk cannot decrypt it; ask the user)",
}


def route(type_id: str) -> tuple[str, str, str | None]:
    """(skill, script, subcommand) for a type; skill is '' when none applies."""
    if type_id in _TYPE_ROUTES:
        return _TYPE_ROUTES[type_id]
    ft = TYPES.get(type_id)
    if ft is None:
        return ("file-inspector", "bin_tool.py", "map")
    return _GROUP_ROUTES.get(ft.group, ("", "", None))


_SAFE_ARG = re.compile(r"^[\w@%+=:,./\\-]+$")


def quote_arg(s: str) -> str:
    """Quotes an argument for display in a command line (POSIX shells; also readable on Windows). A name holding a
    newline or another control character uses $'...' quoting, so the command stays on one line and still works."""
    if s and _SAFE_ARG.match(s):
        return s
    if any(ord(c) < 32 or ord(c) == 127 for c in s):
        esc = {"\\": "\\\\", "'": "\\'", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
        return "$'" + "".join(esc.get(c) or (f"\\x{ord(c):02x}" if ord(c) < 32 or ord(c) == 127 else c) for c in s) + "'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def show_path(s: str) -> str:
    """A path for display in a table: control characters (a newline in a file name) shown as escapes, not line breaks."""
    if not any(ord(c) < 32 or ord(c) == 127 for c in s):
        return s
    return "".join({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(c) or (f"\\x{ord(c):02x}" if ord(c) < 32 or ord(c) == 127 else c) for c in s)


def command_for(type_id: str, path: str, extra: list[str] | None = None) -> dict | None:
    """The first command to run on this file: skill, script, args and a display string."""
    skill, script, sub = route(type_id)
    if not skill:
        return None
    args = ([sub] if sub else []) + [path] + (extra or [])
    display = "python3 scripts/" + script + " " + " ".join(quote_arg(a) for a in args)
    return {"skill": skill, "script": script, "args": args, "display": display}
