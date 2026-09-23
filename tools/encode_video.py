"""Blender headless: re-encode an intro video for the web (H.264 + AAC, moov at the front).

usage: blender -b --python encode_video.py -- <in.mp4> <out.mp4> [long_edge_px] [quality]
quality: one of LOWEST, VERYLOW, LOW, MEDIUM, HIGH, PERC_LOSSLESS, LOSSLESS (Blender CRF presets; MEDIUM ~ crf 23)
"""
import bpy, sys, os, glob, shutil, tempfile

argv = sys.argv[sys.argv.index("--") + 1:]
src = os.path.abspath(argv[0])
dst = os.path.abspath(argv[1])
long_edge = int(argv[2]) if len(argv) > 2 else 1280
quality = argv[3] if len(argv) > 3 else "MEDIUM"

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

clip = bpy.data.movieclips.load(src)
w, h = clip.size
fps = clip.fps
frames = clip.frame_duration
print(f"source: {w}x{h} @ {fps:.3f} fps, {frames} frames")

scale = min(1.0, long_edge / max(w, h))
scene.render.resolution_x = int(w * scale) // 2 * 2
scene.render.resolution_y = int(h * scale) // 2 * 2
scene.render.resolution_percentage = 100
# keep fractional frame rates (29.97 -> 30 / 1.001)
if abs(fps - round(fps)) > 1e-3:
    scene.render.fps = int(round(fps * 1.001))
    scene.render.fps_base = 1.001
else:
    scene.render.fps = int(round(fps))
    scene.render.fps_base = 1.0
scene.frame_start = 1
scene.frame_end = frames

# colour: pass pixels through unchanged
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'
try:
    scene.sequencer_colorspace_settings.name = 'sRGB'
except Exception:
    pass

se = scene.sequence_editor_create()
strips = se.strips if hasattr(se, "strips") else se.sequences   # Blender 4.4+ renamed sequences -> strips
strips.new_movie(name="video", filepath=src, channel=2, frame_start=1)
try:
    strips.new_sound(name="audio", filepath=src, channel=1, frame_start=1)
    has_audio = True
except Exception as e:
    has_audio = False
    print("no audio track:", e)

r = scene.render
if hasattr(r.image_settings, 'media_type'):   # Blender 5.0+: video formats appear only after selecting the video media type
    r.image_settings.media_type = 'VIDEO'
r.image_settings.file_format = 'FFMPEG'
r.ffmpeg.format = 'MPEG4'
r.ffmpeg.codec = 'H264'
r.ffmpeg.constant_rate_factor = quality
r.ffmpeg.ffmpeg_preset = 'GOOD'
r.ffmpeg.gopsize = 30
r.ffmpeg.audio_codec = 'AAC' if has_audio else 'NONE'
if has_audio:
    r.ffmpeg.audio_bitrate = 128
    r.ffmpeg.audio_mixrate = 48000
tmp = tempfile.mkdtemp(prefix="encode_")
r.filepath = os.path.join(tmp, "out")
bpy.ops.render.render(animation=True)

produced = sorted(glob.glob(os.path.join(tmp, "*.mp4")), key=os.path.getsize)
if not produced:
    sys.exit("encode failed: no mp4 produced in " + tmp)
raw = produced[-1]

# move the moov atom to the front so browsers can start playback before the download finishes
try:
    from qtfaststart import processor
    processor.process(raw, dst)
    print("faststart applied")
except Exception as e:
    print("qtfaststart unavailable, copying as-is:", e)
    shutil.copyfile(raw, dst)
shutil.rmtree(tmp, ignore_errors=True)
print(f"wrote {dst} {os.path.getsize(dst)/1e6:.1f} MB ({r.resolution_x}x{r.resolution_y}, {quality})")
