# Garments AR

Static web AR site: one page per garment, opened from a printed QR code. 3D preview with Google `<model-viewer>`;
camera AR with the 8th Wall engine (works on iPhone Safari and Android Chrome without ARCore/ARKit), plus the phones' built-in AR as an alternative.

- `docs/` is the published site (GitHub Pages, branch `main`, folder `/docs`). Each garment lives at `docs/<id>/` with its files in `docs/models/<id>/`.
- `docs/ar/` is the shared camera AR page (`../ar/?m=<id>`): 8th Wall's free open-source engine (its own SLAM, no ARCore/ARKit needed) + three.js. Tap to place the garment on the floor at true size; drag to rotate. `docs/models.json` is generated for it. The engine binary requires the copyright notice and licence link kept in `docs/ar/index.html`.
- Each garment page keeps a secondary "built-in AR" button (WebXR / Scene Viewer / Quick Look via model-viewer) for phones where the platform tracker is better.
- `models.json` lists the garments. `python build_site.py` regenerates the pages.
- `tools/glb_to_web.py` turns a CLO `.glb` into `model.glb` (Draco), `model.usdz` and `poster.webp`:
  `blender -b --python tools/glb_to_web.py -- "<garment>.glb" docs/models/<id> 0.35 0.5 0.9`
  (arguments: cloth decimate ratio, skin decimate ratio, uniform scale; hair becomes a matte double-sided alpha cutout at 0.3)
- `tools/usd_fix.py` is called by the export script: it patches the USD (hair alpha cutout, matte, double-sided meshes) and packages the `.usdz` with Pixar's packager. It runs on Blender's bundled `pxr`; on a machine without it, `pip install usd-core`.
- `python serve.py` previews `docs/` locally on port 8765.
- `python make_qr.py https://<user>.github.io/<repo>/` writes one printable QR PNG per garment into `qr/`.
