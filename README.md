# Garments AR

Static web AR site: one page per garment, opened from a printed QR code. camera AR with the 8th Wall engine, plus the intro video and zoomable sewing patterns as overlays (works on iPhone Safari and Android Chrome without ARCore/ARKit).

- `docs/` is the published site (GitHub Pages, branch `main`, folder `/docs`). Each garment lives at `docs/<id>/` with its files in `docs/models/<id>/`.
- Each garment page **is** the AR page (`docs/<id>/index.html`, media baked in): the camera opens first, the garment is placed on the floor (tap), pinch to resize, swipe on it to rotate, and the bottom buttons **View Video** and **View Patterns** open overlays whose close buttons return to the same AR session.

## Adding a garment

1. Create `source/<id>/` (git-ignored, never published) and drop in three files, any names:
   the CLO export `*.glb`, the intro video `*.mp4`, the pattern image `*.png` / `*.jpg`.
2. Run `python make_garment.py <id> "<Name>" "<one-line description>"`.
   It exports the model (Draco GLB + USDZ + poster, hair detected and fixed automatically, 0.9 scale),
   encodes the video to a 2 MB 720p streaming copy, copies the pattern, adds the row to `models.json`,
   rebuilds the site and writes `qr/<id>-<name>.png`. Options: `--scale`, `--cloth`, `--skin`,
   `--hair Name1,Name2` / `--skin-mats ...` to override the printed material detection, `--skip-model`, `--skip-video`.
3. Check the printed `HAIR:` / `SKIN:` lines and `docs/models/<id>/poster.webp`, then `git add -A && git commit -m "Add garment <id>" && git push`.
   The page is live at `<base_url>/<id>/` about a minute later; print the QR from `qr/`.

Requirements on the machine: Blender 5.x (path in `make_garment.py` or the `BLENDER` env var), Python 3 with `pip install qtfaststart qrcode[pil]`.

- `docs/ar/app.js` is the shared script behind every garment page (8th Wall's free open-source engine, its own SLAM, no ARCore/ARKit needed, + three.js): tap to place, pinch to resize, swipe on the garment to rotate. `docs/ar/index.html` is the same page driven by `?m=<id>` and the generated `docs/models.json`. The engine binary requires the copyright notice and licence link that the generator keeps in every page's HTML.
- `models.json` lists the garments. `python build_site.py` regenerates the pages.
- `tools/glb_to_web.py` (called by `make_garment.py`) turns a CLO `.glb` into `model.glb` (Draco), `model.usdz` and `poster.webp`. Hair and skin materials are detected from their properties (alpha-textured materials in the head region = hair, large opaque textured materials = skin), so CLO's changing material names do not matter; hair becomes a matte double-sided alpha cutout at 0.3 with hard-alpha textures.
- `tools/usd_fix.py` is called by the export script: it patches the USD (hair alpha cutout, matte, double-sided meshes) and packages the `.usdz` with Pixar's packager. It runs on Blender's bundled `pxr`; on a machine without it, `pip install usd-core`.
- `python serve.py` previews `docs/` locally on port 8765.
- `python make_qr.py https://<user>.github.io/<repo>/` writes one printable QR PNG per garment into `qr/`.
