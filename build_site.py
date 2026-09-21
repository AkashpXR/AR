"""Generate the static web AR site from models.json.

usage: python build_site.py
Writes docs/index.html and docs/<id>/index.html. Model files live in docs/models/<id>/ (docs = GitHub Pages root).
All links are relative, so the site works from any host or sub-path.
"""
import json, os, html

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "docs")
MODEL_VIEWER = "https://cdn.jsdelivr.net/npm/@google/model-viewer@4.3.1/dist/model-viewer.min.js"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{name} · {brand}</title>
<meta name="description" content="{description}">
<link rel="preload" as="fetch" href="../{glb}" crossorigin>
<script type="module" src="{mv}"></script>
<style>
  :root {{ --bg: #f4f2ef; --fg: #1d1b19; --muted: #6b665f; --accent: #b3243f; --card: #ffffff; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #141311; --fg: #f1ede7; --muted: #a39d94; --accent: #ff4d6d; --card: #201e1b; }} }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; background: var(--bg); color: var(--fg); font: 16px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ display: flex; flex-direction: column; min-height: 100dvh; }}
  model-viewer {{ flex: 1; width: 100%; min-height: 60dvh; background: var(--bg); --poster-color: transparent; }}
  .bar {{ padding: 16px 20px calc(16px + env(safe-area-inset-bottom)); background: var(--card); box-shadow: 0 -8px 24px rgba(0,0,0,.08); }}
  .bar h1 {{ margin: 0 0 4px; font-size: 20px; font-weight: 600; }}
  .bar p {{ margin: 0 0 14px; color: var(--muted); font-size: 14px; }}
  .ar {{ display: none; width: 100%; padding: 14px 18px; border: 0; border-radius: 12px; background: var(--accent); color: #fff; font-size: 17px; font-weight: 600; cursor: pointer; }}
  .ar:active {{ filter: brightness(.9); }}
  .noar {{ display: none; margin: 0; padding: 12px 14px; border-radius: 10px; background: rgba(127,127,127,.12); color: var(--muted); font-size: 14px; }}
  .has-ar .ar {{ display: block; }}
  .no-ar .noar {{ display: block; }}
  .progress {{ position: absolute; left: 0; right: 0; top: 0; height: 3px; background: rgba(127,127,127,.2); }}
  .progress .fill {{ height: 100%; width: 0; background: var(--accent); transition: width .2s; }}
  .hint {{ position: absolute; left: 0; right: 0; top: 12px; text-align: center; font-size: 13px; color: var(--muted); pointer-events: none; }}
</style>
</head>
<body>
<main>
  <model-viewer id="mv"
    src="../{glb}"
    ios-src="../{usdz}"
    poster="../{poster}"
    alt="{name} shown on a mannequin"
    ar ar-modes="webxr scene-viewer quick-look" ar-placement="floor" ar-scale="fixed" xr-environment
    camera-controls touch-action="pan-y" auto-rotate rotation-per-second="20deg"
    shadow-intensity="1" shadow-softness="0.8" environment-image="neutral" exposure="1"
    loading="eager" reveal="auto" interaction-prompt="none">
    <div class="progress" slot="progress-bar"><div class="fill" id="fill"></div></div>
    <div class="hint">Drag to rotate · pinch to zoom</div>
    <button slot="ar-button" style="display:none"></button>
  </model-viewer>
  <div class="bar">
    <h1>{name}</h1>
    <p>{description}</p>
    <button class="ar" id="arbtn" type="button">View in your space</button>
    <p class="noar">AR is not available on this device or browser. On iPhone open this page in Safari; on Android use Chrome. You can still rotate the model above.</p>
  </div>
</main>
<script type="module">
  const mv = document.getElementById('mv');
  const fill = document.getElementById('fill');
  const btn = document.getElementById('arbtn');
  mv.addEventListener('progress', e => {{ fill.style.width = (e.detail.totalProgress * 100) + '%'; }});
  mv.addEventListener('load', () => {{ fill.parentElement.style.display = 'none'; }});
  btn.addEventListener('click', () => mv.activateAR());
  const check = () => {{
    const ok = !!mv.canActivateAR;
    document.body.classList.toggle('has-ar', ok);
    document.body.classList.toggle('no-ar', !ok);
  }};
  mv.addEventListener('load', check);
  customElements.whenDefined('model-viewer').then(() => setTimeout(check, 0));
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


def main():
    with open(os.path.join(ROOT, "models.json"), encoding="utf-8") as f:
        data = json.load(f)
    brand = data["brand"]
    cards = []
    for m in data["models"]:
        for key in ("glb", "usdz", "poster"):
            if not os.path.isfile(os.path.join(SITE, m[key])):
                print(f"WARNING: {m['id']} missing {m[key]}")
        page_dir = os.path.join(SITE, m["id"])
        os.makedirs(page_dir, exist_ok=True)
        ctx = {k: html.escape(str(v)) for k, v in m.items()}
        ctx.update(brand=html.escape(brand), mv=MODEL_VIEWER)
        with open(os.path.join(page_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(PAGE.format(**ctx))
        cards.append(f'  <a class="card" href="{ctx["id"]}/"><img src="{ctx["poster"]}" alt=""><span>{ctx["name"]}</span></a>')
        print(f"wrote {m['id']}/index.html  ({m['name']})")
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX.format(brand=html.escape(brand), cards="\n".join(cards)))
    print("wrote index.html")


if __name__ == "__main__":
    main()
