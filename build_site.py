"""Generate the static web AR site from models.json.

usage: python build_site.py
Writes docs/index.html, docs/<id>/index.html and docs/models.json. docs/ is the GitHub Pages root.

Per garment the page plays an intro video first, then offers "Show Patterns" (zoomable pattern image)
and "Show in AR" (docs/ar/, the 8th Wall page). Media is picked up automatically from
docs/Resources/<id>/ (first .mp4 and first .png/.jpg) unless models.json names "video" / "pattern".
A web-optimised docs/models/<id>/intro.mp4 (tools/encode_video.py) is preferred over the raw upload.
All links are relative, so the site works from any host or sub-path.
"""
import json, os, html, glob
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "docs")

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{name} · {brand}</title>
<meta name="description" content="{description}">
<link rel="prefetch" href="../ar/">
<style>
  :root {{ --bg: #0f0e0d; --fg: #f4f2ef; --muted: #a39d94; --accent: #ff4d6d; --card: rgba(20,18,17,.92); }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; background: #000; color: var(--fg); font: 16px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ position: fixed; inset: 0; background: var(--bg); }}
  #intro {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; background: #000; }}
  .pill {{ position: absolute; left: 50%; transform: translateX(-50%); bottom: calc(24px + env(safe-area-inset-bottom)); border: 0; border-radius: 999px; padding: 10px 16px; font-size: 15px; font-weight: 600; color: #fff; background: rgba(0,0,0,.6); backdrop-filter: blur(6px); }}
  #tap {{ inset: 0; transform: none; left: 0; bottom: 0; width: 100%; height: 100%; border-radius: 0; background: rgba(0,0,0,.35); font-size: 20px; }}
  #after {{ position: absolute; left: 0; right: 0; bottom: 0; padding: 18px 20px calc(18px + env(safe-area-inset-bottom)); background: linear-gradient(to top, var(--card) 70%, transparent); }}
  #after h1 {{ margin: 0 0 12px; font-size: 20px; font-weight: 600; text-align: center; }}
  .row {{ display: flex; gap: 10px; }}
  .btn {{ flex: 1; display: block; padding: 14px 12px; border: 0; border-radius: 12px; font-size: 16px; font-weight: 600; text-align: center; text-decoration: none; color: #fff; cursor: pointer; }}
  .btn.primary {{ background: var(--accent); }}
  .btn.secondary {{ background: rgba(255,255,255,.14); }}
  .btn:active {{ filter: brightness(.9); }}
  #replay {{ display: block; margin: 12px auto 0; background: none; border: 0; color: var(--muted); font-size: 14px; }}
  [hidden] {{ display: none !important; }}
  /* pattern viewer */
  #viewer {{ position: fixed; inset: 0; background: #111; touch-action: none; overflow: hidden; z-index: 50; }}
  #viewer img {{ position: absolute; left: 0; top: 0; transform-origin: 0 0; user-select: none; -webkit-user-drag: none; max-width: none; will-change: transform; }}
  #close {{ position: absolute; top: calc(12px + env(safe-area-inset-top)); right: 12px; width: 44px; height: 44px; border: 0; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; font-size: 26px; line-height: 44px; z-index: 2; }}
  #zoomhint {{ position: absolute; left: 50%; transform: translateX(-50%); bottom: calc(16px + env(safe-area-inset-bottom)); color: #ccc; font-size: 13px; background: rgba(0,0,0,.5); padding: 6px 12px; border-radius: 999px; pointer-events: none; transition: opacity .4s; }}
</style>
</head>
<body>
<main>
  <video id="intro" src="{video}" poster="../{poster}" playsinline preload="auto"></video>
  <button id="tap" class="pill" type="button" hidden>▶ Tap to play</button>
  <button id="sound" class="pill" type="button" hidden>🔇 Tap for sound</button>
  <div id="after" hidden>
    <h1>{name}</h1>
    <div class="row">
      <button id="patterns" class="btn secondary" type="button">Show Patterns</button>
      <a id="ar" class="btn primary" href="../ar/?m={id}">Show in AR</a>
    </div>
    <button id="replay" type="button">Replay video</button>
  </div>
  <div id="viewer" hidden>
    <button id="close" type="button" aria-label="Close">×</button>
    <img id="pattern" src="{pattern}" alt="{name} sewing patterns" draggable="false">
    <div id="zoomhint">Pinch or double-tap to zoom · drag to move</div>
  </div>
</main>
<script>
(() => {{
  const video = document.getElementById('intro');
  const tap = document.getElementById('tap');
  const sound = document.getElementById('sound');
  const after = document.getElementById('after');

  const finished = () => {{ after.hidden = false; sound.hidden = true; tap.hidden = true; }};
  video.addEventListener('ended', finished);
  video.addEventListener('error', finished);
  document.getElementById('replay').addEventListener('click', () => {{ after.hidden = true; video.currentTime = 0; video.muted = false; video.play().catch(() => {{}}); }});

  // Autoplay with sound is blocked without a gesture on most phones: fall back to muted autoplay
  // with a "tap for sound" pill, and if even that is refused, to a tap-to-play overlay.
  const start = async () => {{
    try {{ video.muted = false; await video.play(); return; }} catch (e) {{}}
    try {{ video.muted = true; await video.play(); sound.hidden = false; return; }} catch (e) {{}}
    tap.hidden = false;
  }};
  sound.addEventListener('click', () => {{ video.muted = false; sound.hidden = true; }});
  tap.addEventListener('click', () => {{ tap.hidden = true; video.muted = false; video.play().catch(() => {{ video.muted = true; video.play(); sound.hidden = false; }}); }});
  start();

  // ---- pattern viewer: pinch / wheel / double-tap zoom, drag to pan
  const viewer = document.getElementById('viewer');
  const img = document.getElementById('pattern');
  const hint = document.getElementById('zoomhint');
  let s = 1, tx = 0, ty = 0, fit = 1;
  const apply = () => {{ img.style.transform = `translate(${{tx}}px, ${{ty}}px) scale(${{s}})`; }};
  const fitToScreen = () => {{
    const vw = viewer.clientWidth, vh = viewer.clientHeight;
    fit = Math.min(vw / img.naturalWidth, vh / img.naturalHeight);
    s = fit; tx = (vw - img.naturalWidth * s) / 2; ty = (vh - img.naturalHeight * s) / 2; apply();
  }};
  const zoomAt = (factor, cx, cy) => {{
    const ns = Math.min(Math.max(s * factor, fit), fit * 8);
    const k = ns / s;
    tx = cx - (cx - tx) * k; ty = cy - (cy - ty) * k; s = ns; apply();
  }};
  const open = () => {{
    viewer.hidden = false; document.body.style.overflow = 'hidden';
    hint.style.opacity = 1; setTimeout(() => hint.style.opacity = 0, 2500);
    if (img.complete && img.naturalWidth) fitToScreen(); else img.onload = fitToScreen;
  }};
  const close = () => {{ viewer.hidden = true; document.body.style.overflow = ''; }};
  document.getElementById('patterns').addEventListener('click', open);
  document.getElementById('close').addEventListener('click', close);
  window.addEventListener('resize', () => {{ if (!viewer.hidden) fitToScreen(); }});

  const pts = new Map(); let lastDist = 0, lastMid = null, lastTapT = 0, moved = false;
  viewer.addEventListener('pointerdown', (e) => {{
    if (e.target.id === 'close') return;
    viewer.setPointerCapture(e.pointerId); pts.set(e.pointerId, {{x: e.clientX, y: e.clientY}}); moved = false;
    if (pts.size === 2) {{ const [a, b] = [...pts.values()]; lastDist = Math.hypot(a.x - b.x, a.y - b.y); lastMid = {{x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}}; }}
  }});
  viewer.addEventListener('pointermove', (e) => {{
    if (!pts.has(e.pointerId)) return;
    const prev = pts.get(e.pointerId); pts.set(e.pointerId, {{x: e.clientX, y: e.clientY}});
    if (pts.size === 1) {{ tx += e.clientX - prev.x; ty += e.clientY - prev.y; if (Math.hypot(e.clientX - prev.x, e.clientY - prev.y) > 2) moved = true; apply(); }}
    else if (pts.size === 2) {{
      const [a, b] = [...pts.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y); const mid = {{x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}};
      if (lastDist > 0) zoomAt(d / lastDist, mid.x, mid.y);
      tx += mid.x - lastMid.x; ty += mid.y - lastMid.y; apply();
      lastDist = d; lastMid = mid; moved = true;
    }}
  }});
  const up = (e) => {{
    pts.delete(e.pointerId); lastDist = 0;
    if (pts.size === 0 && !moved && e.target.id !== 'close') {{
      const now = Date.now();
      if (now - lastTapT < 320) {{ s > fit * 1.05 ? fitToScreen() : zoomAt(2.5, e.clientX, e.clientY); lastTapT = 0; }} else lastTapT = now;
    }}
  }};
  viewer.addEventListener('pointerup', up); viewer.addEventListener('pointercancel', up);
  viewer.addEventListener('wheel', (e) => {{ e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY); }}, {{passive: false}});
  viewer.addEventListener('touchmove', (e) => e.preventDefault(), {{passive: false}});   // keep Safari from zooming the page
  document.addEventListener('gesturestart', (e) => {{ if (!viewer.hidden) e.preventDefault(); }});
  document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape' && !viewer.hidden) close(); }});
}})();
</script>
</body>
</html>
"""

INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{brand}</title>
<style>
  :root {{ --bg: #f4f2ef; --fg: #1d1b19; --muted: #6b665f; --card: #ffffff; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #141311; --fg: #f1ede7; --muted: #a39d94; --card: #201e1b; }} }}
  body {{ margin: 0; padding: 24px 16px; background: var(--bg); color: var(--fg); font: 16px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  h1 {{ font-size: 22px; margin: 0 0 16px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }}
  a.card {{ display: block; text-decoration: none; color: inherit; background: var(--card); border-radius: 12px; overflow: hidden; }}
  a.card img {{ display: block; width: 100%; aspect-ratio: 1; object-fit: cover; background: #ddd; }}
  a.card span {{ display: block; padding: 10px 12px; font-weight: 600; }}
  p {{ color: var(--muted); }}
</style>
</head>
<body>
<h1>{brand}</h1>
<p>Scan a garment's QR code, or pick one below.</p>
<div class="grid">
{cards}
</div>
</body>
</html>
"""


def rel_url(site_relative_path):
    """Path relative to docs/ -> URL usable from docs/<id>/ (one level down), percent-encoded."""
    return "../" + quote(site_relative_path.replace(os.sep, "/"))


def discover_media(m):
    """Return (video, pattern) as paths relative to docs/, honouring models.json overrides."""
    res_dir = os.path.join(SITE, "Resources", str(m["id"]))
    video = m.get("video")
    pattern = m.get("pattern")
    optimised = os.path.join("models", str(m["id"]), "intro.mp4")
    if not video and os.path.isfile(os.path.join(SITE, optimised)):
        video = optimised
    if not video:
        found = sorted(glob.glob(os.path.join(res_dir, "*.mp4")) + glob.glob(os.path.join(res_dir, "*.MP4")))
        if found:
            video = os.path.relpath(found[0], SITE)
    if not pattern:
        found = sorted(f for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp") for f in glob.glob(os.path.join(res_dir, ext)))
        if found:
            pattern = os.path.relpath(found[0], SITE)
    return video, pattern


def main():
    with open(os.path.join(ROOT, "models.json"), encoding="utf-8") as f:
        data = json.load(f)
    brand = data["brand"]
    cards = []
    for m in data["models"]:
        for key in ("glb", "usdz", "poster"):
            if not os.path.isfile(os.path.join(SITE, m[key])):
                print(f"WARNING: {m['id']} missing {m[key]}")
        video, pattern = discover_media(m)
        if not video:
            print(f"WARNING: {m['id']} has no intro video (docs/Resources/{m['id']}/*.mp4)")
        if not pattern:
            print(f"WARNING: {m['id']} has no pattern image (docs/Resources/{m['id']}/*.png)")
        page_dir = os.path.join(SITE, str(m["id"]))
        os.makedirs(page_dir, exist_ok=True)
        ctx = {k: html.escape(str(v)) for k, v in m.items()}
        ctx.update(brand=html.escape(brand),
                   video=rel_url(video) if video else "",
                   pattern=rel_url(pattern) if pattern else "")
        with open(os.path.join(page_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(PAGE.format(**ctx))
        cards.append(f'  <a class="card" href="{ctx["id"]}/"><img src="{ctx["poster"]}" alt=""><span>{ctx["name"]}</span></a>')
        print(f"wrote {m['id']}/index.html  ({m['name']})  video={video}  pattern={pattern}")
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX.format(brand=html.escape(brand), cards="\n".join(cards)))
    print("wrote index.html")
    # public manifest read by the shared AR page (docs/ar/) to find a garment by id
    public = {"brand": brand, "models": [{k: m[k] for k in ("id", "name", "glb", "usdz", "poster") if k in m} for m in data["models"]]}
    with open(os.path.join(SITE, "models.json"), "w", encoding="utf-8") as f:
        json.dump(public, f, indent=2)
    print("wrote models.json")


if __name__ == "__main__":
    main()
