# Garments AR

Static web AR site: one page per garment, opened from a printed QR code. camera AR with the 8th Wall engine, plus the intro video and zoomable sewing patterns as overlays (works on iPhone Safari and Android Chrome without ARCore/ARKit).

- `docs/` is the published site (GitHub Pages, branch `main`, folder `/docs`). Each garment lives at `docs/<id>/` with its files in `docs/models/<id>/`.
- Each garment page **is** the AR page (`docs/<id>/index.html`, media baked in): the camera opens first, the garment is placed on the floor, and the top buttons **View Video** and **View Patterns** open overlays (seekable video / pinch-zoomable pattern image) whose close buttons return to the same AR session. Drop the raw video (`.mp4`) and pattern image (`.png`/`.jpg`) into `docs/Resources/<id>/`; the generator picks them up. Optional: `blender -b --python tools/encode_video.py -- "docs/Resources/<id>/<video>.mp4" docs/models/<id>/intro.mp4 1280 MEDIUM` writes a streaming-friendly 720p copy that the page prefers (needs `pip install qtfaststart` for the index move).
- `docs/ar/app.js` is the shared script behind every garment page (8th Wall's free open-source engine, its own SLAM, no ARCore/ARKit needed, + three.js): tap to place, − / + to resize, swipe on the garment to rotate. `docs/ar/index.html` is the same page driven by `?m=<id>` and the generated `docs/models.json`. The engine binary requires the copyright notice and licence link that the generator keeps in every page's HTML.
- `models.json` lists the garments. `python build_site.py` regenerates the pages.
- `tools/glb_to_web.py` turns a CLO `.glb` into `model.glb` (Draco), `model.usdz` and `poster.webp`:
  `blender -b --python tools/glb_to_web.py -- "<garment>.glb" docs/models/<id> 0.35 0.5 0.9`
  (arguments: cloth decimate ratio, skin decimate ratio, uniform scale; hair becomes a matte double-sided alpha cutout at 0.3)
- `tools/usd_fix.py` is called by the export script: it patches the USD (hair alpha cutout, matte, double-sided meshes) and packages the `.usdz` with Pixar's packager. It runs on Blender's bundled `pxr`; on a machine without it, `pip install usd-core`.
- `python serve.py` previews `docs/` locally on port 8765.
- `python make_qr.py https://<user>.github.io/<repo>/` writes one printable QR PNG per garment into `qr/`.
