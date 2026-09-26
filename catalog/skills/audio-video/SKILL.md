---
name: audio-video
description: Inspect, watch, listen to, convert, edit and transcribe audio and video files (.mp4 .mov .m4v .mkv .webm .avi .wmv .mpg .ts .gif; .mp3 .wav .m4a .aac .flac .ogg .opus .aiff .wma) and subtitles (.srt .vtt .ass .ssa). Use it to read duration, codecs, resolution, streams, chapters and tags; to see a video as timestamped contact sheets or frames and audio as waveforms or spectrograms (then view_image); to convert with presets (web MP4, WebM, HEVC, GIF, email-sized, MP3, M4A, Opus, WAV, FLAC) or to a target size; to trim, cut, join, speed up, crop, scale, rotate, pad, add text, logos or blur, fade, normalise loudness, mix or replace audio, add or burn subtitles, and set tags, chapters and cover art; to measure LUFS, peaks, clipping, silence and speech; to transcribe or translate speech into text or subtitles with Whisper; to find where words are said in long recordings; and to convert, shift, sync, merge, clean, check or extract subtitle files.
license: MIT
---

# Audio and video

Scripts in `scripts/` use PyAV (FFmpeg libraries), a bundled static ffmpeg, NumPy, Pillow and faster-whisper.
Desk sets up their Python environment, so run them with `python3`. Every script has `--help` with examples, prints
Markdown by default and JSON with `--format json`, and never modifies its input.

Work in a loop: **look** at the file, **act** on it, then **check** the result the same way you looked.

## Look

Start with the facts, then see or hear the content.

```bash
python3 scripts/media_info.py talk.mp4                   # container, duration, streams, HDR, rotation, chapters, tags
python3 scripts/media_info.py footage/ --recursive       # one row per file, with totals
python3 scripts/media_info.py talk.mp4 --keyframes       # where a fast (copy) cut can land
python3 scripts/media_info.py lecture.mp4 --map          # long media: sound segments split at pauses, as time addresses
```

**Watch a video** with contact sheets: one PNG of timestamped frames, sized for vision. Look at it with
view_image, then zoom into the part that matters with a narrower range.

```bash
python3 scripts/media_frames.py sheet talk.mp4                                   # 9-25 frames over the whole video
python3 scripts/media_frames.py sheet talk.mp4 --start 12:00 --end 14:00 --count 16
python3 scripts/media_frames.py frames talk.mp4 --at 0:05,1:30,50% --out-dir frames/   # exact frames (--max-edge 0: full size)
python3 scripts/media_frames.py frames talk.mp4 --scenes --sheet                  # one frame per shot
```

A sheet of a 60-minute video takes a few seconds (it uses keyframes; labels show their real times). Use `--exact`
when the precise frame matters. Frames of rotated phone videos come out upright, and HDR is tone-mapped.
Also: `gif in.mp4 preview.gif` (short clips over the video, or `--start 1:00 --end 1:05`), `thumb` (the sharpest,
best-exposed frame, or `--at`) and `cover` (embedded album art or poster).

**Hear audio** by looking at it and measuring it:

```bash
python3 scripts/media_audio.py waveform podcast.mp3 --mark-silence   # peaks and RMS per channel, clipping in red
python3 scripts/media_audio.py spectrogram song.flac                  # hum, hiss, cut-offs, tones
python3 scripts/media_audio.py stats podcast.mp3                      # LUFS, true peak, RMS, DC, clipping, silence share
python3 scripts/media_audio.py silence interview.wav --min 1.5        # silent ranges (--sounds for the rest)
python3 scripts/media_audio.py speech interview.wav                   # where someone talks (offline model)
```

**Know what is said**: transcribe it (next section), or read the subtitles a file already has, with times:

```bash
python3 scripts/subtitles.py read movie.mkv                    # "#n [time] text" rows from the first text track (or a .srt)
python3 scripts/subtitles.py find movie.mkv "quarterly budget"  # the cues that say it, their times and the cues around them
```

## Act

### Convert

```bash
python3 scripts/media_convert.py talk.mov talk.mp4                    # H.264/AAC; streams that already fit are copied
python3 scripts/media_convert.py talk.mov talk.mp4 --preset web       # plays everywhere: within 1920x1080, ≤60 fps, fast start
python3 scripts/media_convert.py talk.mov small.mp4 --preset email    # within 1280x720, ≤30 fps, about 20 MB at most
python3 scripts/media_convert.py talk.mov talk.mp4 --target-mb 8      # two-pass encode under a size
python3 scripts/media_convert.py clip.mp4 clip.gif --start 5 --end 9 --size 480x --fps 12
python3 scripts/media_convert.py song.flac song.mp3                    # tags and cover art kept
python3 scripts/media_convert.py "recordings/*.wav" --to mp3 --out-dir mp3/   # batch, in parallel
python3 scripts/media_convert.py memos/ -r --to m4a --out-dir m4a/     # a folder tree; sub-folders kept
```

The output extension picks the format. `--presets` lists the presets (web, webm, hevc, av1, email, edit, gif, mp3,
m4a, opus, ogg, wav, flac, aiff, alac, wma, voice). Explicit options: `--vcodec`, `--crf`, `--video-bitrate`,
`--size` (1280x720, 720p, 50%, fit:WxH, fill:WxH), `--fps`, `--acodec`, `--audio-bitrate`, `--channels`,
`--sample-rate`, `--audio-track`, `--no-audio`, `--no-video`, `--copy` (remux only) and `--reencode`.
HDR sources are tone-mapped when the target is 8-bit.

- An MP4 or MOV should play everywhere, so VP9, AV1 or Opus from a WebM are re-encoded to H.264/AAC unless you pass
  `--vcodec copy` (the notes say so). Subtitle tracks are converted to what the container holds (mov_text in MP4,
  SRT in MKV, WebVTT in WebM).
- `--target-mb` counts the audio too. When the bitrate left for the picture cannot carry it, it lowers the frame rate
  (to 30) and then the resolution (a note gives what it chose). It re-encodes up to twice more if the first try is over.
  For example, 1 minute of 1080p in 8 MB comes out at 960x540 and 7.8 MB.
- In batch mode, inputs with the same name (take.m4a and take.wav) become take.mp3 and take-2.mp3. Nothing is
  overwritten silently.

### Edit

Each operation writes a new file. Streams an operation does not touch are copied, so a volume change does not
re-encode the picture. Subtitles and chapters follow trims, cuts and speed changes.

```bash
python3 scripts/media_edit.py trim talk.mp4 clip.mp4 --start 1:05 --end 1:47          # exact
python3 scripts/media_edit.py trim talk.mp4 clip.mp4 --start 1:05 --end 1:47 --fast   # keyframe cut, instant
python3 scripts/media_edit.py cut talk.mp4 short.mp4 --remove 0:00-0:12,14:30-15:10
python3 scripts/media_edit.py concat intro.mp4 talk.mp4 outro.mp4 -o full.mp4 --crossfade 0.5 --chapters  # --transition wipeleft …
python3 scripts/media_edit.py normalize podcast.wav mastered.wav --preset podcast      # EBU R128 two-pass, -16 LUFS, -1.5 dBTP
python3 scripts/media_edit.py crop talk.mp4 vertical.mp4 --aspect 9:16                # also --box x,y,w,h or --auto
python3 scripts/media_edit.py text talk.mp4 titled.mp4 --text "Q3 review" --position top --end 4 --box
python3 scripts/media_edit.py overlay talk.mp4 logo.png branded.mp4 --position top-right --width 12% --opacity 0.8
python3 scripts/media_edit.py blur talk.mp4 private.mp4 --box 60%,5%,35%,20% --start 0:10 --end 0:25
python3 scripts/media_edit.py mix-audio video.mp4 music.mp3 out.mp4 --volume 0.25 --duck --loop --fade-out 3
python3 scripts/media_edit.py subtitles talk.mp4 talk.en.srt captioned.mp4 --language eng   # soft track
python3 scripts/media_edit.py subtitles talk.mp4 talk.en.srt burned.mp4 --burn --size 5%     # in the picture
python3 scripts/media_edit.py metadata song.mp3 tagged.mp3 --set title="Intro" --cover art.jpg --chapters ch.txt
```

Other operations: `speed`, `fade`, `volume`, `mute` (ranges, `--beep`), `denoise`, `scale`, `rotate`
(`--metadata-only` for a flag change), `flip`, `pad` (`--blur` fills bars with the picture), `adjust` (brightness,
contrast, saturation, gamma, grayscale, sharpen), `fps`, `extract-audio`, `replace-audio` and `remux`.

Chain several operations in **one encode** (no quality lost between steps) with `pipeline`:

```bash
python3 scripts/media_edit.py pipeline in.mp4 reel.mp4 --ops '[{"op":"trim","start":"0:05","end":"0:35"},
  {"op":"crop","aspect":"9:16"},{"op":"text","text":"Launch day","position":"top","end":3,"box":true},
  {"op":"fade","in":0.5,"out":0.5},{"op":"normalize","preset":"streaming"}]'
```

Times inside a pipeline refer to the timeline at that step (after earlier trims). All operations and their options
are in `references/edit-ops.md`.

`normalize` into MP3, AAC or Opus limits the peaks 1 to 1.5 dB lower, because encoders overshoot them. It then
measures the true peak of the result. For an audio file under 30 minutes, it encodes once more if the result is over
the target; otherwise it says so. Subtitle positions (`{\an8}` at the top, WebVTT `line:0`) survive conversions and
burning.

### Transcribe

```bash
python3 scripts/media_transcribe.py interview.mp3                              # transcript on stdout
python3 scripts/media_transcribe.py talk.mp4 --out talk.srt --out talk.txt --words
python3 scripts/media_transcribe.py lecture.m4a --model small --language fr --out lecture.vtt
python3 scripts/media_transcribe.py clip.mp4 --task translate --out clip.en.srt  # any language → English
python3 scripts/media_transcribe.py talk.mp4 --find "budget"                    # where it is said, with times
```

The default model is `base` (145 MB). The first use of a model downloads it to the Hugging Face cache and says so;
later runs are offline. `--list-models` shows sizes and what is downloaded, `--model-dir` uses a local model folder,
and `--prompt` helps with names and jargon. Word timings (`--words`) give better subtitle timing. Long recordings
are processed in chunks, so memory stays bounded, and the report gives the speed achieved. On a slow machine try
`tiny` or `--beam-size 1`; for hard audio (accents, noise) try `small` or `turbo`.

Transcripts are cached by the file's content, the model, the language and the options. Asking again for another
page (`--offset`), a search (`--find`) or another `--out` format is instant and needs no model. The printed
transcript stops at `--max-chars` and ends with the command for the next part.

### Subtitles

```bash
python3 scripts/subtitles.py convert movie.srt movie.vtt               # srt vtt ass sbv lrc json tsv txt md
python3 scripts/subtitles.py convert talk.json talk.srt --reflow --line-chars 42  # transcript JSON → subtitles
python3 scripts/subtitles.py shift movie.srt fixed.srt --by -1.5
python3 scripts/subtitles.py sync movie.srt fixed.srt --map 0:01:02.5=0:01:04 --map 1:40:00=1:40:03.2
python3 scripts/subtitles.py clean raw.srt clean.srt --remove-sdh --line-chars 42 --min-duration 1
python3 scripts/subtitles.py check movie.srt --video movie.mp4          # overlaps, reading speed, long lines …
python3 scripts/subtitles.py extract movie.mkv movie.en.srt --language eng
```

`merge` (interleave, or `--mode stack` for bilingual) and `split --at` complete the set. Malformed blocks (bad
times, stray lines) are skipped and counted by `info` and `check`, never glued onto a neighbouring cue.

## Big files

Never try to take in a 2-hour recording at once. Work **map → find → drill down**; times are the addresses:

```bash
python3 scripts/media_info.py lecture.mp4 --map                  # chapters + sound segments split at the longest pauses
python3 scripts/subtitles.py find lecture.mkv "action items"      # or media_transcribe.py lecture.mp4 --find "action items"
python3 scripts/media_frames.py sheet lecture.mp4 --start 40:10 --end 45:00   # look at one segment
python3 scripts/media_frames.py frames lecture.mp4 --at 40:44 --max-edge 0    # one exact frame, full size
python3 scripts/media_info.py archive/ -r                         # a big folder: totals per sub-folder, then the files in pages
```

- Listings (folders, keyframes, cues, hits, transcripts, silences, speech) stop at `--max-chars` (default 60000) on a
  whole row and end with the exact command for the next part (`--offset N`); run it rather than raising the cap.
  JSON pages the same way: the command is on stderr, or in a `next` field for transcripts.
- Expensive results are cached by the file's content (`--no-cache` skips the cache). This covers probes, keyframe
  scans, audio levels, stats, speech, scenes, sheets, frames, GIFs, thumbnails, waveforms, spectrograms, extracted
  subtitle tracks and transcripts. On a 60-minute video, a sheet takes 0.3 s, stats 2.4 s and scenes 6 s the first
  time, and any of them about 0.07 s after that (`references/performance.md`).
- Sheets use keyframes and audio is streamed in blocks, so a long file needs little memory. Transcription is the
  slow step: it runs in chunks and reports its speed. For a quick first look, transcribe one segment from the map
  with `--start/--end`.

## Check your work

- Run `media_info.py` on the output: duration, size, codecs, resolution and streams should be what was asked.
  Every script also reports its own notes; relay them (for example a keyframe cut that starts early).
- Look at visual edits: `media_frames.py frames out.mp4 --at …` inside and outside the edited range, or a sheet,
  then view_image. Check text placement, crops and burned subtitles this way.
- Measure audio edits: `media_audio.py stats` for loudness targets and clipping, `waveform` to see fades and cuts.
- Run `subtitles.py check` on subtitles you wrote.

## Rules

- Never overwrite the user's file. Write a new file, say where it is, and use `--force` only for your own outputs.
- Say what you could not do and why (a stream that could not be kept, a font that was missing, a model that was
  not downloaded).
- Prefer copying streams to re-encoding: `--fast` cuts, `remux`, `metadata`, and conversions without quality
  options keep the original quality and take seconds.
- Times: `83.5`, `1:23.5`, `01:02:03.250`, `1h2m`, `25%`, `end`, `-10` (10 s before the end). Ranges: `10-15`,
  `1:20-1:25`, `2:00-end`, `30+5`.

## Limits

- No OCR. Image-based subtitle tracks (PGS, VobSub) cannot become text; look at frames instead.
- Transcription has no speaker labels and needs a model download (tiny 75 MB to large-v3 3.1 GB) on first use.
- Burned subtitles use libass when this ffmpeg has it; otherwise the skill draws them (no italics, ASS styling or
  Arabic/Hebrew/Indic shaping). Emoji cannot be drawn in text or subtitles.
- A damaged video decodes as far as it can. Undecodable frames are skipped, and ffmpeg's error concealment fills in
  for them (the sheet says so). A file cut off before its index (an MP4 without "moov") cannot be read, and the error
  says that.
- Re-encoding HDR to HEVC keeps 10-bit and BT.2020 tags but not mastering metadata; Dolby Vision is not kept.
- DRM-protected media cannot be read. Live streams and URLs are out of scope: download the file first.

For recipes (vertical clips, podcast mastering, captions, highlights from a transcript, audio cleanup) and for
writing your own script with the same `python3`, PyAV and ffmpeg, see `references/recipes.md`. Codecs,
containers, presets and loudness targets are in `references/formats.md`. For still images use the `images` skill,
for unknown files `file-inspector`, and to put a transcript in a document `word-documents` or `markup-ebooks`.
