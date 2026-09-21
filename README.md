# Garments AR

Static web AR site: one page per garment, opened from a printed QR code. Built on Google `<model-viewer>`;
AR runs through WebXR on Android Chrome (Scene Viewer fallback) and AR Quick Look on iPhone.

- `docs/` is the published site (GitHub Pages, branch `main`, folder `/docs`). Each garment lives at `docs/<id>/` with its files in `docs/models/<id>/`.
- `models.json` lists the garments. `python build_site.py` regenerates the pages.
- `tools/glb_to_web.py` turns a CLO `.glb` into `model.glb` (Draco), `model.usdz` and `poster.webp`:
  `blender -b --python tools/glb_to_web.py -- "<garment>.glb" docs/models/<id> 0.35 0.5 0.9`
  (arguments: cloth decimate ratio, skin decimate ratio, uniform scale; hair becomes a matte double-sided alpha cutout at 0.3)
- `tools/usd_fix.py` is called by the export script: it patches the USD (hair alpha cutout, matte, double-sided meshes) and packages the `.usdz` with Pixar's packager. It runs on Blender's bundled `pxr`; on a machine without it, `pip install usd-core`.
- `python serve.py` previews `docs/` locally on port 8765.
- `python make_qr.py https://<user>.github.io/<repo>/` writes one printable QR PNG per garment into `qr/`.
