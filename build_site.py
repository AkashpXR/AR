"""Generate the static web AR site from models.json.

usage: python build_site.py
Writes docs/index.html, docs/<id>/index.html (the AR page for that garment, media baked in),
docs/ar/index.html (the same page driven by ?m=<id> and docs/models.json) and docs/models.json.
docs/ is the GitHub Pages root. All links are relative, so the site works from any host or sub-path.

Media is picked up automatically from docs/Resources/<id>/ (first .mp4 and first .png/.jpg) unless
models.json names "video" / "pattern". A web-optimised docs/models/<id>/intro.mp4
(tools/encode_video.py) is preferred over the raw upload.
"""
import json, os, html, glob
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "docs")

# The AR page. Placeholders are %%NAME%% so CSS/JS braces need no escaping.
AR_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<title>%%TITLE%%</title>
<meta name="description" content="%%DESCRIPTION%%">

<!-- 8th Wall Engine (world tracking), XRExtras (loading / permissions / errors), Landing Page (unsupported devices).
     AR engine: Copyright © 2026 Niantic Spatial, Inc. All rights reserved.
     Licensed under https://github.com/8thwall/engine/blob/main/LICENSE -->
<script src="https://cdn.jsdelivr.net/npm/@8thwall/engine-binary@1/dist/xr.js" async crossorigin="anonymous" data-preload-chunks="slam"></script>
<script src="https://cdn.jsdelivr.net/npm/@8thwall/xrextras@1/dist/xrextras.js" crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/@8thwall/landing-page@1/dist/landing-page.js" crossorigin="anonymous"></script>

<script type="importmap">
{ "imports": {
  "three": "https://cdn.jsdelivr.net/npm/three@0.183.2/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.183.2/examples/jsm/"
} }
</script>
%%GARMENT%%
<style>
  :root { --accent: #ff4d6d; }
  html, body { margin: 0; height: 100%; overflow: hidden; background: #000; font: 16px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #fff; }
  #camerafeed { position: fixed; inset: 0; width: 100%; height: 100%; touch-action: none; z-index: 0; }
  /* the engine positions the canvas itself; keep our overlay above it but below XRExtras' loading/error screens (z-index 800+) */
  #ui { position: fixed; inset: 0; z-index: 500; pointer-events: none; display: flex; flex-direction: column; justify-content: flex-end; }
  #media { display: flex; gap: 10px; }
  #bottom { padding: 0 16px calc(16px + env(safe-area-inset-bottom)); display: flex; flex-direction: column; gap: 10px; align-items: center; }
  #hint { background: rgba(0,0,0,.55); padding: 10px 16px; border-radius: 12px; font-size: 15px; text-align: center; max-width: 420px; backdrop-filter: blur(6px); transition: opacity .35s; }
  #hint.fade { opacity: 0; }
  #hint.warn { background: rgba(180,40,40,.7); }
  button { -webkit-appearance: none; appearance: none; margin: 0; padding: 0; border: 0; font: inherit; color: inherit; background: none; box-sizing: border-box; }
  .btn { pointer-events: auto; border: 0; border-radius: 12px; padding: 12px 18px; font-size: 16px; font-weight: 600; color: #fff; background: var(--accent); }
  .btn.secondary { background: rgba(255,255,255,.18); }
  .btn.pill { border-radius: 999px; padding: 9px 16px; font-size: 14px; background: rgba(0,0,0,.5); backdrop-filter: blur(6px); }
  #controls { display: none; gap: 10px; align-items: center; }
  #controls.on { display: flex; }
  [hidden] { display: none !important; }
  /* overlays on top of the AR view */
  .overlay { position: fixed; inset: 0; z-index: 600; background: #000; }
  .close { position: absolute; top: calc(12px + env(safe-area-inset-top)); right: 12px; width: 44px; height: 44px; flex: none; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; font-size: 26px; line-height: 1; z-index: 2; }
  #vid { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; background: #000; }
  #vseek { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; gap: 10px; padding: 28px 14px calc(14px + env(safe-area-inset-bottom)); background: linear-gradient(to top, rgba(0,0,0,.7), transparent); }
  #vpp { width: 40px; height: 40px; flex: none; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; background: rgba(255,255,255,.18); color: #fff; font-size: 16px; line-height: 1; }
  #vscrub { flex: 1; height: 28px; margin: 0; -webkit-appearance: none; appearance: none; background: transparent; cursor: pointer; }
  #vscrub::-webkit-slider-runnable-track { height: 4px; border-radius: 2px; background: linear-gradient(to right, var(--accent) 0 var(--p, 0%), rgba(255,255,255,.35) var(--p, 0%) 100%); }
  #vscrub::-webkit-slider-thumb { -webkit-appearance: none; width: 16px; height: 16px; margin-top: -6px; border-radius: 50%; background: #fff; }
  #vscrub::-moz-range-track { height: 4px; border-radius: 2px; background: rgba(255,255,255,.35); }
  #vscrub::-moz-range-progress { height: 4px; border-radius: 2px; background: var(--accent); }
  #vscrub::-moz-range-thumb { width: 16px; height: 16px; border: 0; border-radius: 50%; background: #fff; }
  #vtime { font-size: 12px; color: #ddd; font-variant-numeric: tabular-nums; flex: none; min-width: 74px; text-align: right; }
  #viewer { background: #111; touch-action: none; overflow: hidden; }
  #viewer img { position: absolute; left: 0; top: 0; transform-origin: 0 0; user-select: none; -webkit-user-drag: none; max-width: none; will-change: transform; }
  #zoomhint { position: absolute; left: 50%; transform: translateX(-50%); bottom: calc(16px + env(safe-area-inset-bottom)); color: #ccc; font-size: 13px; background: rgba(0,0,0,.5); padding: 6px 12px; border-radius: 999px; pointer-events: none; transition: opacity .4s; }
  /* hide the "powered by 8th Wall" badge injected by the MIT-licensed xrextras and landing-page helpers */
  .poweredby-img, img[src*="poweredby"], .landing8-attribution-logo, [aria-label*="powered by" i], [class*="powered-by"], [class*="poweredby"] { display: none !important; }
</style>
</head>
<body>
  <canvas id="camerafeed"></canvas>
  <div id="ui">
    <div id="bottom">
      <div id="hint">Loading…</div>
      <div id="controls">
        <button class="btn" id="place" type="button">Place here</button>
        <button class="btn secondary" id="reset" type="button">Reset</button>
      </div>
      <div id="media">
        <button class="btn pill" id="videoBtn" type="button">▶ View Video</button>
        <button class="btn pill" id="patternsBtn" type="button">View Patterns</button>
      </div>
    </div>
  </div>
  <div id="videoBox" class="overlay" hidden>
    <button id="vclose" class="close" type="button" aria-label="Close">×</button>
    <video id="vid" playsinline preload="metadata"></video>
    <div id="vseek">
      <button id="vpp" type="button" aria-label="Play or pause">▶</button>
      <input id="vscrub" type="range" min="0" max="1000" value="0" step="1" aria-label="Seek">
      <span id="vtime">0:00 / 0:00</span>
    </div>
  </div>
  <div id="viewer" class="overlay" hidden>
    <button id="pclose" class="close" type="button" aria-label="Close">×</button>
    <img id="pattern" alt="Sewing patterns" draggable="false">
    <div id="zoomhint">Pinch or double-tap to zoom · drag to move</div>
  </div>
  <script type="module" src="%%APP%%"></script>
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


def url_path(site_relative_path):
    """docs-relative filesystem path -> percent-encoded URL path (still docs-relative, no leading ../)."""
    return quote(site_relative_path.replace(os.sep, "/"))


def discover_media(m):
    """Return (video, pattern) as docs-relative paths, honouring models.json overrides.

    Preferred: docs/models/<id>/intro.mp4 and docs/models/<id>/patterns.* (written by make_garment.py).
    Fallback: the first .mp4 / image in docs/Resources/<id>/ (older layout)."""
    model_dir = os.path.join(SITE, "models", str(m["id"]))
    res_dir = os.path.join(SITE, "Resources", str(m["id"]))
    video = m.get("video")
    pattern = m.get("pattern")
    if not video and os.path.isfile(os.path.join(model_dir, "intro.mp4")):
        video = os.path.join("models", str(m["id"]), "intro.mp4")
    if not pattern:
        found = sorted(f for ext in ("patterns.png", "patterns.jpg", "patterns.webp") for f in glob.glob(os.path.join(model_dir, ext)))
        if found:
            pattern = os.path.relpath(found[0], SITE)
    if not video:
        found = sorted(glob.glob(os.path.join(res_dir, "*.mp4")) + glob.glob(os.path.join(res_dir, "*.MP4")))
        if found:
            video = os.path.relpath(found[0], SITE)
    if not pattern:
        found = sorted(f for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp") for f in glob.glob(os.path.join(res_dir, ext)))
        if found:
            pattern = os.path.relpath(found[0], SITE)
    return video, pattern


def render_ar_page(garment, brand, app_src):
    title = html.escape((garment["name"] + " · " + brand) if garment else brand)
    desc = html.escape(garment.get("description", "Place the garment on the floor at true size using the camera.") if garment else "Place the garment on the floor at true size using the camera.")
    baked = ""
    if garment:
        baked = "<script>window.GARMENT = " + json.dumps(garment).replace("</", "<\\/") + "</script>"
    return (AR_PAGE.replace("%%TITLE%%", title).replace("%%DESCRIPTION%%", desc)
            .replace("%%GARMENT%%", baked).replace("%%APP%%", app_src))


def main():
    with open(os.path.join(ROOT, "models.json"), encoding="utf-8") as f:
        data = json.load(f)
    brand = data["brand"]
    cards, public = [], []
    for m in data["models"]:
        for key in ("glb", "usdz", "poster"):
            if not os.path.isfile(os.path.join(SITE, m[key])):
                print(f"WARNING: {m['id']} missing {m[key]}")
        video, pattern = discover_media(m)
        if not video:
            print(f"WARNING: {m['id']} has no intro video (docs/Resources/{m['id']}/*.mp4)")
        if not pattern:
            print(f"WARNING: {m['id']} has no pattern image (docs/Resources/{m['id']}/*.png)")
        garment = {
            "id": str(m["id"]), "name": m["name"], "description": m.get("description", ""),
            "glb": url_path(m["glb"]), "usdz": url_path(m["usdz"]), "poster": url_path(m["poster"]),
            "video": url_path(video) if video else "", "pattern": url_path(pattern) if pattern else "",
        }
        public.append(garment)
        page_dir = os.path.join(SITE, garment["id"])
        os.makedirs(page_dir, exist_ok=True)
        with open(os.path.join(page_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(render_ar_page(garment, brand, "../ar/app.js"))
        cards.append(f'  <a class="card" href="{garment["id"]}/"><img src="{garment["poster"]}" alt=""><span>{html.escape(garment["name"])}</span></a>')
        print(f"wrote {garment['id']}/index.html  ({garment['name']})  video={video}  pattern={pattern}")
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX.format(brand=html.escape(brand), cards="\n".join(cards)))
    print("wrote index.html")
    os.makedirs(os.path.join(SITE, "ar"), exist_ok=True)
    with open(os.path.join(SITE, "ar", "index.html"), "w", encoding="utf-8") as f:
        f.write(render_ar_page(None, brand, "app.js"))
    print("wrote ar/index.html (shared page, ?m=<id>)")
    with open(os.path.join(SITE, "models.json"), "w", encoding="utf-8") as f:
        json.dump({"brand": brand, "models": public}, f, indent=2)
    print("wrote models.json")


if __name__ == "__main__":
    main()
