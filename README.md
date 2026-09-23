# Garments AR

Static web AR site: one page per garment, opened from a printed QR code. intro video, zoomable sewing patterns, and camera AR with the 8th Wall engine (works on iPhone Safari and Android Chrome without ARCore/ARKit).

- `docs/` is the published site (GitHub Pages, branch `main`, folder `/docs`). Each garment lives at `docs/<id>/` with its files in `docs/models/<id>/`.
- Each garment page plays an intro video first, then shows **Show Patterns** (full-screen, pinch/double-tap zoomable pattern image with a close button) and **Show in AR**. Drop the raw video (`.mp4`) and pattern image (`.png`/`.jpg`) into `docs/Resources/<id>/`; the generator picks them up. Optional: `blender -b --python tools/encode_video.py -- "docs/Resources/<id>/<video>.mp4" docs/models/<id>/intro.mp4 1280 MEDIUM` writes a streaming-friendly 720p copy that the page prefers (the raw upload is not used by the page once it exists; it needs `pip install qtfaststart` for the index move).
- `docs/ar/` is the shared camera AR page (`../ar/?m=<id>`): 8th Wall's free open-source engine (its own SLAM, no ARCore/ARKit needed) + three.js. Tap to place the garment on the floor at true size; drag to rotate. `docs/models.json` is generated for it. The engine binary requires the copyright notice and licence link kept in `docs/ar/index.html`.
- `models.json` lists the garments. `python build_site.py` regenerates the pages.
- `tools/glb_to_web.py` turns a CLO `.glb` into `model.glb` (Draco), `model.usdz` and `poster.webp`:
  `blender -b --python tools/glb_to_web.py -- "<garment>.glb" docs/models/<id> 0.35 0.5 0.9`
  (arguments: cloth decimate ratio, skin decimate ratio, uniform scale; hair becomes a matte double-sided alpha cutout at 0.3)
- `tools/usd_fix.py` is called by the export script: it patches the USD (hair alpha cutout, matte, double-sided meshes) and packages the `.usdz` with Pixar's packager. It runs on Blender's bundled `pxr`; on a machine without it, `pip install usd-core`.
- `python serve.py` previews `docs/` locally on port 8765.
- `python make_qr.py https://<user>.github.io/<repo>/` writes one printable QR PNG per garment into `qr/`.
