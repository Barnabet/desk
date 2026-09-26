# Recipes

Each recipe is a short sequence of the skill's scripts. Check every result as SKILL.md describes (media_info,
frames or a sheet with view_image, media_audio stats).

## Understand a long video quickly

1. `python3 scripts/media_info.py talk.mp4 --map`: chapters, and sound segments split at the longest pauses, each
   with its start and end.
2. `python3 scripts/media_frames.py sheet talk.mp4`: view the sheet and note the parts that matter.
3. `python3 scripts/media_frames.py sheet talk.mp4 --start 23:00 --end 26:00 --count 12` for a closer look.
4. Know what is said: `python3 scripts/subtitles.py read talk.mkv` if it has subtitles, else
   `python3 scripts/media_transcribe.py talk.mp4 --out talk.json --words`. Both stop at `--max-chars` and end with
   the command for the next part.
5. Find a phrase: `python3 scripts/media_transcribe.py talk.mp4 --find "pricing"` (the cached transcript, so this is
   instant) or `subtitles.py find talk.mkv "pricing"`. Use the hit's time to grab exact frames:
   `python3 scripts/media_frames.py frames talk.mp4 --at 24:13.5`.

`frames --scenes --sheet` gives one frame per shot, which suits edited videos (ads, trailers) better than even spacing.

## Clip for social media (vertical, captioned)

```bash
python3 scripts/media_transcribe.py talk.mp4 --start 12:00 --end 12:45 --words --out clip.json
python3 scripts/subtitles.py convert clip.json clip.srt --reflow --line-chars 28
python3 scripts/subtitles.py shift clip.srt clip0.srt --by=-12:00      # the clip starts at 0
python3 scripts/media_edit.py pipeline talk.mp4 reel.mp4 --ops '[
  {"op":"trim","start":"12:00","end":"12:45"},
  {"op":"crop","aspect":"9:16"},
  {"op":"subtitles","subtitles":"clip0.srt","size":"4%","position":"center","box":true},
  {"op":"normalize","preset":"streaming"}]'
```

Transcript times are already relative to the file, so shift them by the trim start before burning. If the speaker is
off-centre, crop with `--box` instead of `--aspect`, or pad a wide picture with `{"op":"pad","aspect":"9:16","blur":true}`.

## Podcast or voice-over mastering

```bash
python3 scripts/media_audio.py stats raw.wav                      # level, peaks, clipping, noise floor
python3 scripts/media_edit.py denoise raw.wav clean.wav            # only if there is steady background noise
python3 scripts/media_edit.py normalize clean.wav master.wav --preset podcast
python3 scripts/media_convert.py master.wav episode.mp3 --audio-bitrate 128k
python3 scripts/media_edit.py metadata episode.mp3 tagged.mp3 --set title="Episode 12" --set artist="Show" --cover art.jpg --chapters chapters.txt
```

Long silences: `media_audio.py silence raw.wav --min 2` lists them; remove them with
`media_edit.py cut raw.wav tight.wav --remove <ranges>` (keep a little silence at each edge for natural pauses).

## Remove the boring parts

`media_audio.py silence talk.mp4 --min 3 --format json` gives silent ranges. Shrink each by 0.3 s on both sides,
then `media_edit.py cut talk.mp4 tight.mp4 --remove "a1-b1,a2-b2,…"`. Subtitles and chapters follow the cut.

## Small enough to send

- `media_convert.py big.mov small.mp4 --preset email` (about 20 MB at most, 720p).
- A hard limit: `--target-mb 9.5` (two-pass). It lowers the size and frame rate itself when the bitrate cannot carry
  the picture; pass `--size`/`--fps` to choose them instead. For talks add `--audio-bitrate 64k --channels 1`, which
  leaves more for the picture.
- Audio only: `media_convert.py talk.wav talk.m4a --preset voice`.

## Join clips from different sources

`media_edit.py concat a.mov b.mp4 c.mkv -o all.mp4 --chapters` fits every clip to the first one's size and frame
rate; pass `--size 1920x1080 --fps 30` to choose. `--crossfade 0.5` adds a cross-dissolve, and `--transition wipeleft`
(or `fadeblack`, `slideup` …) another transition.

## Captions: soft or burned?

- Soft (`media_edit.py subtitles in.mp4 subs.srt out.mp4 --language eng`) can be switched off and edited later,
  but many web players and social networks ignore them.
- Burned (`--burn`) always shows. Use `--box` for readability on busy pictures and check a frame with view_image.
- Before either, `subtitles.py check subs.srt --video in.mp4` and `subtitles.py clean` fix most timing problems.

## Hide private information

`media_edit.py blur in.mp4 out.mp4 --box 62%,8%,30%,12% --start 0:14 --end 0:31` blurs a region for a time
range. Find the box on a frame first (view_image shows the picture; percentages of width and height are easiest),
then check frames at the start, middle and end of the range. Moving objects need several blur steps in a pipeline,
each with its own box and time range.

## Batch work

- `media_info.py folder/ --recursive` inventories a folder: totals per sub-folder first, then one row per file (paths
  relative to the folder), paged by `--max-chars`. `--format json` pages the same way.
- `media_convert.py "in/*.flac" --to mp3 --out-dir out/` converts in parallel (audio) or one by one (video);
  `media_convert.py in/ -r --to mp3 --out-dir out/` does a whole tree and keeps its sub-folders. Same-named inputs get
  `-2`, `-3` suffixes, and failures are listed first.
- For other batches, loop in the shell over files and call the script per file; each call is independent.

## Writing your own script

The skill's Python has PyAV, NumPy, Pillow, faster-whisper and a static ffmpeg. Run custom code with the same
`python3`, and import the skill's helpers by adding the skill's `scripts/` folder to `sys.path`: `SKILL_DIR` is set
when a script runs as a skill script; otherwise use the skill's directory shown with its instructions.

```python
import os, sys
skill = os.environ.get("SKILL_DIR") or "<the skill's directory>"
sys.path.insert(0, os.path.join(skill, "scripts"))
from _media import ffmpeg, probe, read_audio, run_ffmpeg   # ffmpeg() is the bundled binary's path

info = probe("talk.mp4")                                    # the dict media_info prints as JSON
run_ffmpeg(["-i", "talk.mp4", "-vf", "hflip", "-c:a", "copy", "mirror.mp4"], duration=info["duration"])
for block in read_audio("talk.mp4", sr=16000):              # float32 blocks shaped (n, channels)
    ...
```

Decode frames with PyAV (fast seeking, NumPy arrays):

```python
import av
with av.open("talk.mp4") as c:
    s = c.streams.video[0]
    s.thread_type = "AUTO"
    c.seek(int(90 / s.time_base), stream=s)                  # jump to the keyframe before 1:30
    for frame in c.decode(s):
        if frame.time >= 90:
            frame.to_image().save("f.png")                   # or frame.to_ndarray(format="rgb24")
            break
```

Tips:
- Pass arguments to ffmpeg as a list (no shell), with `-nostdin`, and write to a new file.
- `ffmpeg -hide_banner -filters` / `-encoders` show what this build has; `_media.has_filter("zscale")` checks in code.
- Keep images for view_image under 1568 px on the long side (`_render.fit_edge`).
