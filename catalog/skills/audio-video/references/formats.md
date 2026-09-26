# Formats, codecs and targets

## Containers the scripts write

| extension | video | audio | subtitles | notes |
|---|---|---|---|---|
| .mp4 .m4v | H.264 (default), HEVC, AV1, VP9, MPEG-4 | AAC (default), MP3, ALAC, AC-3, Opus, FLAC | mov_text | Fast start for the web. The safest choice for sharing. |
| .mov | H.264, HEVC, ProRes, MJPEG | AAC, ALAC, PCM | mov_text | Editors (ProRes with the `edit` preset). |
| .mkv | anything | anything | SRT, ASS, WebVTT, PGS | Keeps every stream; best for archiving. |
| .webm | VP9 (default), VP8, AV1 | Opus (default), Vorbis | WebVTT | Browsers. |
| .avi .wmv .mpg .ts .flv .ogv .3gp | legacy codecs | | none (3gp: mov_text) | Only when a device needs them. |
| .gif | GIF (palette optimised) | | | Short loops; large for long clips. |
| .mp3 .m4a .aac .opus .ogg .flac .wav .aiff .wma .ac3 .caf .mka | | the matching codec | | Cover art kept in mp3, m4a and flac. |

`media_convert.py` copies a stream when the target container can hold it, players can play it there, and no
quality, size or rate option asks for a change (mkv → mp4 with H.264/AAC is instant and lossless). `--reencode`
forces encoding, and `--copy` forbids it.

- **Plays everywhere.** In .mp4 and .mov, only H.264, HEVC and MPEG-4 video, and AAC, MP3, ALAC and AC-3 audio, are
  copied by default. VP9, AV1, Opus or FLAC from a WebM or MKV are re-encoded to H.264/AAC, and a note says why.
  `--vcodec copy` / `--acodec copy` keeps them; the note then warns that QuickTime and many TVs cannot play them.
  `remux` works the same way. Opus stays Opus from WebM to MKV.
- **Subtitles** are converted per track to what the container holds: mov_text in MP4/MOV, SRT (or ASS, WebVTT) in
  MKV, and WebVTT in WebM. Image tracks (PGS, VobSub) go only to MKV; elsewhere they are dropped, with a note.
- **Tags.** MP4 brand tags (major_brand, compatible_brands) are not copied into other containers.

## Presets (`media_convert.py --preset`)

| preset | output |
|---|---|
| web (mp4) | H.264 CRF 23 + AAC 128k, within 1920x1080 (1080x1920 portrait), ≤60 fps, fast start; already-compatible H.264 is copied |
| webm | VP9 CRF 32 + Opus 96k, within 1920x1080, ≤60 fps |
| hevc | H.265 CRF 26 + AAC 128k (10-bit kept) |
| av1 | AV1 (SVT) CRF 35 + Opus 96k |
| email | H.264 CRF 28 capped so the file stays under about 20 MB, within 1280x720, ≤30 fps, AAC 96k |
| edit | ProRes 422 + PCM in .mov |
| gif | 480 px wide, 12 fps, palette per clip |
| mp3 | VBR ~190 kb/s (`-q:a 2`), or `--audio-bitrate` for CBR |
| m4a / aac | AAC 192k |
| opus | Opus 96k (48 kHz) |
| ogg | Vorbis q5 |
| wav / aiff | PCM, 16-bit (24-bit when the source has more) |
| flac / alac | lossless |
| wma | WMA v2 192k |
| voice | AAC 64k mono 24 kHz, for speech |

Quality guide: H.264 CRF 18 looks transparent, 23 is the default, 28 is small; each +6 roughly halves the size.
VP9 and AV1 CRF values are higher for the same look (30-36).

`--target-mb` works out the audio bitrate first (lower for small targets), then the video bitrate from the duration,
and runs a two-pass encode. When that bitrate is too low for the picture (below about 0.045 bits per pixel for H.264,
0.03 for HEVC and VP9, 0.024 for AV1), it lowers the frame rate to 30 (25 for 50 fps sources) and then the size, down
a ladder of heights (1080, 900, 720, 540, 480, 360, 270, 240), then to 24 fps. The report gives the size chosen, and
`--size` or `--fps` fixes one of them. If the result is over, it re-encodes up to twice, each time smaller, and says
so if it is still over. For a long video in a few MB the picture can become very small (a warning says when the
quality will suffer): shorten it (`--start/--end`) or accept a bigger file.

## Loudness targets (`media_edit.py normalize`, `media_audio.py stats`)

| use | integrated | true peak |
|---|---|---|
| podcasts, web video | -16 LUFS | -1.5 dBTP |
| Spotify, YouTube, Apple Music | -14 LUFS | -1 dBTP |
| broadcast (EBU R128) | -23 LUFS | -1 dBTP |
| voice notes, audiobooks | -19 LUFS | -1.5 dBTP |

LRA (loudness range) says how much the level varies: under 8 LU is dense (music, ads), 10-20 is natural speech with
pauses. Clipping means runs of samples at full scale; a true peak above -1 dBTP risks distortion after MP3/AAC encoding.

`normalize` uses loudnorm in linear mode (one gain for the whole file) when the target is reachable without the peaks
going over the limit. Otherwise it uses dynamic mode, which evens the levels and can land 1-3 LU under the target on
very peaky audio; the note says which mode it used. Into MP3/AAC/Opus the limit is set 1 dB lower (1.5 dB for sources
under 96 kb/s) because encoders overshoot. The true peak of the written file is then measured: an audio file under
30 minutes is encoded once more with a lower limit if needed; otherwise a note gives the value. `stats` measures true
peak with 4x oversampling (matches ffmpeg's ebur128 within 0.05 dB).
A low-bitrate lossy source (a 48 kb/s podcast) is re-encoded at about its own bitrate, not the preset's 190 kb/s.

## Subtitle formats (`subtitles.py`)

| format | read | write | notes |
|---|---|---|---|
| SRT | yes | yes | Italic/bold/underline tags kept; `{\an8}` (top) and other positions kept. Malformed blocks are skipped and counted. |
| WebVTT | yes | yes | Cue settings and `<v Speaker>` kept; `line:0` (top) ↔ `{\an8}`; STYLE/NOTE blocks skipped. |
| ASS/SSA | yes | yes | Styles kept when converting ASS → ASS; other outputs keep italics, line breaks and `{\anN}` positions. |
| SBV (YouTube) | yes | yes | |
| LRC (lyrics) | yes | yes | End times come from the next line. |
| JSON | yes | yes | A list of `{start, end, text}`, or `{"segments": [...]}` from media_transcribe (with `words`). |
| TSV | yes | yes | `start\tend\ttext` in milliseconds (Whisper style). |
| TXT, MD | | yes | Readable transcript; paragraphs break at pauses of 2 s. |

Text encodings: UTF-8, UTF-16 and UTF-32 are detected; older files in a Windows or ISO code page (Western, Central
European, Turkish, Baltic, Cyrillic including KOI8-R, Greek, Hebrew, Arabic) are guessed from their letters, and
`subtitles.py info` shows the guess. Pass `--encoding cp1251` (or any Python codec name) when letters look wrong.
Outputs are always UTF-8.

Readability checks (`subtitles.py check`): at most 42 characters per line (`--line-chars`) and 2 lines, 20
characters per second, 0.7-7 s on screen, no overlaps, a few frames between cues. These follow common broadcast
guidelines; pass other limits with the options. `--max-chars` is the output budget, as in every listing: a long
report stops at a whole row and ends with the command for the next part.

Reading subtitles: `subtitles.py read FILE` prints `#n [start] text` rows (`--start/--end` for a window), and
`find FILE "words"` prints matching cues with their neighbours. Matching ignores case, accents and punctuation, and
a phrase split across two cues is still found. `--regex` and `--exact` are stricter. Both commands accept a
subtitle file or a video with a text track (`--track`, `--language`); the extracted track is cached.

## HDR, rotation and colour

- `media_info.py` reports HDR10 (PQ), HLG and Dolby Vision, colour primaries, transfer, matrix and range, and the
  rotation flag of phone videos.
- Frames and sheets are rotated upright and HDR frames are tone-mapped to SDR, so what you view matches a player.
- Converting HDR to H.264, VP9 or GIF tone-maps to SDR BT.709 (zscale + Hable). Converting to HEVC or AV1 keeps
  10-bit and the BT.2020 tags; mastering-display metadata and Dolby Vision layers are not carried over.
- Re-encoding applies the rotation to the pixels; stream copies keep the flag.
