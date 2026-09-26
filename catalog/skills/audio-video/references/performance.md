# Performance and caching

## What is cached

Scripts that only look at media cache their results by the file's content. Asking again is instant, and so is asking
for another page or a search of the same result:

| Script | Cached | Key |
|---|---|---|
| every script | the probe (`media_info` facts: streams, chapters, tags, HDR) | file content |
| `media_info --keyframes`, `--count-frames` | the packet and keyframe scan | file content |
| `media_info --map`, `media_audio silence`, waveform silence shading | audio levels every 20 ms (a small array) | file content + range + track |
| `media_audio stats` | the whole report (LUFS, true peak, clipping, silence) | file content + range + track + threshold |
| `media_audio speech` | the speech ranges | file content + options |
| `media_audio waveform`, `spectrogram` | the PNG | file content + every drawing option |
| `media_frames sheet`, `frames`, `gif`, `thumb` | the PNGs / GIF | file content + every option that changes the pictures |
| `media_frames frames --scenes` | the scene-change times | file content + threshold |
| `subtitles read/find/extract` on a video | the extracted text track | file content + track |
| `media_transcribe` | the transcript (segments and words) | file content + model (name, or folder + size + date) + language + task + every decoding option + range + track |

- Keys use the file's content, not its name or date: an edited file is read again, and a copy shares the entry. Files
  over 8 MB are fingerprinted from their size, date and samples of the head, middle and tail, so a 5 GB video is
  keyed in milliseconds.
- Each kind of result carries a version. It changes when the code changes what it produces, so an updated skill
  never reuses stale results.
- Failures are never cached.
- The cache lives in the temp folder (`desk-files`). It holds at most 1 GB by default, and nothing is stored when the
  disk is nearly full. `DESK_FILE_CACHE` moves it, `DESK_FILE_CACHE_MB` resizes it, and `DESK_NO_CACHE=1` (or
  `--no-cache`) bypasses it.
- A cached picture is copied to the output you name, so outputs are always new files.

Scripts that write deliverables (`media_convert`, `media_edit`, `subtitles` convert/shift/…) always do the work.

## Measured timings

Measured on an Apple M2 (8 cores, 8 GB) with the scripts run as Desk runs them (sandboxed, Python 3.12,
`DESK_MAX_WORKERS=2`). Other agents were using the machine at the time, so an idle machine is somewhat faster.
"Cached" is the second run of the same command. The fixtures were made with ffmpeg: `testsrc2` pictures, tones with
regular pauses, pink noise for the podcast, 30 chapters, and a 900-cue subtitle track.

**60-minute video** (640x360 25 fps H.264, 16 kHz mono AAC, SRT track, 262 MB):

| Operation | First run | Cached |
|---|---|---|
| `media_info` | 0.17 s | 0.06 s |
| `media_info --map` (decodes the audio once) | 0.60 s | 0.12 s |
| `media_info --keyframes` | 0.36 s | 0.06 s |
| `media_frames sheet` (25 frames from keyframes) | 0.32 s | 0.07 s |
| `media_frames sheet --exact` | 0.42 s | 0.07 s |
| `media_frames sheet --start 12:00 --end 14:00 --count 16` | 0.36 s | 0.07 s |
| `media_frames frames --scenes --sheet` | 6.0 s | 0.07 s |
| `media_audio waveform --mark-silence` | 2.0 s | 0.07 s |
| `media_audio spectrogram` | 0.66 s | 0.07 s |
| `media_audio stats` | 2.4 s | 0.06 s |
| `media_audio silence` | 0.68 s | 0.12 s |
| `media_audio speech` (Silero VAD) | 8.5 s | 0.07 s |
| `subtitles find` / `read` (extracts the track) | 0.16 s | 0.08 s |

**2-hour podcast** (48 kb/s mono MP3, 43 MB):

| Operation | First run | Cached |
|---|---|---|
| `media_info --map` | 3.2 s | 0.12 s |
| `media_audio stats` | 11.7 s (18.9 s with `DESK_MAX_WORKERS=1`) | 0.05 s |
| `media_audio waveform` | 3.2 s | 0.05 s |
| `media_audio silence` (levels already cached by `--map`) | 0.13 s | 0.13 s |
| `media_edit normalize --preset podcast` (never cached; two loudnorm passes, then a true-peak check) | 220 s | |

**20-minute 1080p video** (1920x1080 30 fps H.264 at 1.1 Mb/s, keyframes every 250 frames):

| Operation | First run | Cached |
|---|---|---|
| `media_frames sheet` (16 frames) | 0.66 s | 0.07 s |
| `media_frames sheet --exact` | 0.70 s | 0.07 s |
| `media_frames sheet --count 36` | 0.64 s | 0.07 s |
| `media_frames frames --at 5:00,10:00.5,15:00 --max-edge 0` (full-size PNGs) | 0.25 s | 0.07 s |
| `media_frames thumb` (picks the sharpest of several candidates) | 0.76 s | 0.07 s |
| `media_frames gif` (preview clips over the video) | 1.1 s | 0.07 s |
| `media_convert --end 1:00 --target-mb 8` (two-pass; picture reduced to 960x540, 7.78 MB) | 7.1 s | |

The normalized podcast measured -2.0 dBTP (limit -1.5) at 56 kb/s, about the source's own bitrate. The source was
pink noise with deep level swings, so loudnorm used dynamic mode and landed at -18.3 LUFS. Audio whose peaks leave
room gets linear mode, which lands within a few tenths (a 20 s tonal test file: -16.4 LUFS). The note says which mode
was used.

`stats` gets its speed from running ffmpeg's loudness meter and the NumPy peak and true-peak scan side by side
(one after the other when `DESK_MAX_WORKERS=1`). The true peak is 4x oversampled, and only near the loud samples.
Sheets seek to keyframes, so their cost depends on the number of frames asked for, not on the length of the video.
`--exact` decodes forward from each keyframe to the exact frame.

Transcription could not be timed here (no Whisper model was downloaded on the test machine). faster-whisper reports
its own speed at the end of each run. A cached transcript is read back in about 0.1 s, and so is every `--find` and
`--offset` page after that.
