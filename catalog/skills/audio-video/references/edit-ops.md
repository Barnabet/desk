# media_edit operations

Every operation is a subcommand (`python3 scripts/media_edit.py OP INPUT OUTPUT [options]`) and, except where
noted, also a `pipeline` step: `{"op": "OP", ...}` with the option names below (dashes become underscores).
Pipeline steps run in order in one encode; times in a step refer to the timeline after the steps before it.

Times: `83.5`, `1:23.5`, `01:02:03.250`, `1h2m`, `25%`, `end`, `-10` (from the end).
Ranges: `10-15`, `1:20-1:25`, `2:00-end`, `30+5` (start + length), comma-separated.
Sizes: pixels (`48`) or percentages of the picture (`5%`). Positions: `top`, `bottom`, `center`, `left`, `right`,
`top-left`, `top-right`, `bottom-left`, `bottom-right`, or `x,y` in pixels or percentages (`10%,80%`).
Colours: names (`white`), `#RRGGBB`, `#RRGGBBAA`, or `name@opacity` (`black@0.5`).

## Timeline

| op | options | notes |
|---|---|---|
| `trim` | `start`, `end` or `duration` | CLI `--fast` copies streams and cuts at keyframes (not a pipeline step). |
| `cut` | `remove` (ranges) | CLI also `--keep RANGES` (pipeline: `{"op":"keep","ranges":...}`) and `--fast`. |
| `speed` | `factor` (0.05-100), `keep_pitch` (default true; CLI `--pitch` turns it off) | Frame rate is kept; audio uses chained `atempo`. |
| `fps` | `fps` | Drops or repeats frames. |

Subtitle tracks (text) and chapters are re-timed to follow `trim`, `cut`, `keep` and `speed`; ASS styling is dropped
when a track is re-timed, but cue positions (`{\an8}` …) are kept. Every re-encoded video comes out at a constant
frame rate (the source's), so a cut 25 fps clip still reports 25 fps.

## Sound

| op | options | notes |
|---|---|---|
| `volume` | `gain` (`6dB`, `-3dB`, `0.5`), `ranges` | Only the ranges when given. |
| `mute` | `ranges`, `beep` (bool), `beep_freq` | Without ranges the audio track is removed. |
| `normalize` | `preset` (`podcast`/`web` -16, `streaming` -14, `broadcast` -23, `voice` -19), `target` (LUFS), `true_peak` (dBTP), `lra` (LU) | EBU R128 two-pass loudnorm: linear when the target is reachable within the peak limit, else dynamic (the note says so). The sample rate is restored. Into MP3/AAC/Opus the limit is 1-1.5 dB lower and the result's true peak is measured (one corrective encode for audio under 30 min). |
| `denoise` | `strength` (dB, default 12), `highpass` (Hz, default 80) | Steady noise (fans, hiss) and rumble; not for music. |
| `fade` | `in`, `out` (seconds), `color`, `only` (`audio`/`video`) | Picture and sound together unless `only`. |

## Picture

| op | options | notes |
|---|---|---|
| `crop` | `box` (`x,y,w,h`), or `aspect` (`9:16`) with `anchor`, or `auto` (bool) | `auto` finds black borders on five samples; put it before other size changes. |
| `scale` | `size`: `1280x720`, `1280x`, `x720`, `720p`, `50%`, `fit:WxH`, `fill:WxH` | `fill` crops to cover exactly; `fit` stays inside. |
| `rotate` | `angle` (clockwise degrees), `color` | 90/180/270 are lossless transposes of the pixels; CLI `--metadata-only` sets the rotation flag without re-encoding. |
| `flip` | `horizontal`, `vertical` | |
| `pad` | `aspect` or `size`, `color`, `blur` (bool) | `blur` fills the bars with a blurred copy (vertical video on a 16:9 canvas). |
| `adjust` | `brightness` (-1..1), `contrast` (0..3), `saturation` (0..3), `gamma`, `grayscale`, `sharpen`, `denoise`, `start`, `end` | |
| `blur` | `box`, `strength` (default 20), `start`, `end` | Hides faces, plates or screens; check the result frame by frame. |

## Overlays

| op | options | notes |
|---|---|---|
| `text` | `text` (`\n` for new lines), `position`, `size` (default 6%), `color`, `font` (file or name), `regular`, `box`, `box_color`, `no_outline`, `shadow`, `align`, `margin`, `start`, `end`, `fade` | Drawn with Pillow (the same result on every platform) in a system font that has the text's letters (CJK too); Arabic, Hebrew and Indic text goes through libass, which shapes it. Emoji are not drawn. Long text wraps at 92% of the width. |
| `image` (CLI `overlay`) | `image`, `position` (default bottom-right), `width` (default 15%), `opacity`, `margin`, `start`, `end` | PNG transparency is kept. |
| `subtitles` (CLI `subtitles --burn`) | `subtitles` (file), `font`, `size`, `color`, `box`, `position` (`bottom`/`top`/`center`), `margin`, `encoding` | libass when ffmpeg has it (ASS files keep their styling unless you pass style options); otherwise the skill draws each cue. Either way a cue's own position (`{\an8}` in SRT/ASS, `line:0` in WebVTT) wins over `position`. `box` puts a dark box behind the text. |

## Other subcommands (not pipeline steps)

| subcommand | what it does |
|---|---|
| `concat A B … -o OUT` | Same formats are joined without re-encoding; otherwise every clip is fitted to the first one's size and frame rate (letterboxed), audio to 48 kHz stereo, and silent audio or black video fills gaps. `--crossfade S` with `--transition`: `fade` (the default; also `dissolve` or `crossfade`, a smooth cross-dissolve), `fadeblack`/`black`, `fadewhite`, `wipeleft`, `slideup`, `circleopen`, `radial`, `pixelize` or any other ffmpeg xfade name. ffmpeg's own grainy `dissolve` is `pixel-dissolve`. An unknown name is an error that lists the choices. `--chapters` adds one chapter per clip. |
| `extract-audio IN OUT.m4a` | Copies the audio when the output format can hold it (AAC → .m4a, MP3 → .mp3, Opus → .opus); else encodes. |
| `replace-audio IN AUDIO OUT` | `--offset` (negative skips the new audio's start), `--loop`. Pads with silence or cuts at the video's end. |
| `mix-audio IN AUDIO OUT` | `--volume`, `--duck` (sidechain compression under speech), `--loop`, `--offset`, `--fade-out`. |
| `subtitles IN SUBS OUT` | Soft track: MP4/MOV get mov_text, WebM gets WebVTT, MKV keeps SRT or ASS. `--language`, `--title`, `--default`, `--forced`. Any readable format (srt, vtt, ass, sbv, lrc, json, tsv) is accepted. |
| `remux IN OUT` | New container, no re-encoding of the picture; subtitles converted per track (mov_text → SRT in MKV); audio re-encoded only if the container cannot hold it (the note names the codecs). WebM → MKV keeps VP9 and Opus. |
| `metadata IN OUT` | `--set KEY=VALUE` (repeat; `KEY=` deletes), `--clear`, `--chapters FILE`, `--clear-chapters`, `--cover IMAGE`, `--remove-cover`. Same container as the input. |

Chapters files: lines like `0:00 Intro`, `12:30 - Q&A`, `1:02:03 Wrap-up`; or JSON `[{"start": "0:00", "title": "Intro"}]`;
or an ffmpeg `;FFMETADATA1` file.

## Encoding options (all subcommands that re-encode)

`--crf` (default 20 for H.264, 24 for HEVC, 30 for VP9), `--speed fastest|fast|medium|slow|slowest`, `--vcodec`,
`--acodec`, `--video-bitrate`, `--audio-bitrate`, `--track N` (which audio track to edit), `--reencode`.
Edited streams keep the source codec when the output container allows it (HEVC stays HEVC, ProRes stays ProRes);
otherwise the container's default is used (H.264/AAC for MP4, VP9/Opus for WebM).
