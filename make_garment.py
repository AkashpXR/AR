"""One command per garment: source/<id>/ -> optimised web assets -> page -> QR.

usage: python make_garment.py <id> "<Name>" ["description"] [--cloth 0.35] [--skin 0.5] [--scale 0.9]
                              [--hair Name1,Name2] [--skin-mats Name1,Name2] [--skip-model] [--skip-video]

Put the garment's files in source/<id>/ (this folder is git-ignored and never published):
  the CLO export            *.glb
  the intro video           *.mp4
  the pattern image         *.png / *.jpg / *.jpeg / *.webp
Outputs go to docs/models/<id>/ (model.glb, model.usdz, poster.webp, intro.mp4, patterns.<ext>),
the garment is added to models.json, the site is rebuilt and qr/<id>-<name>.png is written.
Then: git add -A && git commit && git push.
"""
import argparse, glob, json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "tools")
DOCS = os.path.join(ROOT, "docs")
BLENDER_CANDIDATES = [
    os.environ.get("BLENDER", ""),
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
    "blender",
]


def find_blender():
    for c in BLENDER_CANDIDATES:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    sys.exit("Blender not found: set the BLENDER environment variable to blender.exe")


def first(folder, patterns):
    for p in patterns:
        hits = sorted(glob.glob(os.path.join(folder, p)))
        if hits:
            return hits[0]
    return None


def run(cmd, log_keep=None):
    print("$", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (res.stdout or "") + (res.stderr or "")
    if log_keep:
        for line in out.splitlines():
            if any(k in line for k in log_keep):
                print("   ", line.strip())
    if res.returncode != 0:
        print(out[-4000:])
        sys.exit(f"command failed with exit code {res.returncode}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("name")
    ap.add_argument("description", nargs="?", default="")
    ap.add_argument("--cloth", type=float, default=0.35, help="cloth decimate ratio")
    ap.add_argument("--skin", type=float, default=0.5, help="skin decimate ratio")
    ap.add_argument("--scale", type=float, default=0.9, help="uniform scale baked into the model")
    ap.add_argument("--hair", default="", help="override hair material names, comma separated")
    ap.add_argument("--skin-mats", default="", help="override skin material names, comma separated")
    ap.add_argument("--skip-model", action="store_true")
    ap.add_argument("--skip-video", action="store_true")
    a = ap.parse_args()

    src_dir = os.path.join(ROOT, "source", a.id)
    out_dir = os.path.join(DOCS, "models", a.id)
    os.makedirs(out_dir, exist_ok=True)
    glb = first(src_dir, ["*.glb", "*.GLB"])
    video = first(src_dir, ["*.mp4", "*.MP4", "*.mov", "*.MOV"])
    pattern = first(src_dir, ["*.png", "*.PNG", "*.jpg", "*.jpeg", "*.JPG", "*.webp"])
    print(f"source/{a.id}: model={os.path.basename(glb) if glb else None} video={os.path.basename(video) if video else None} pattern={os.path.basename(pattern) if pattern else None}")
    if not glb and not a.skip_model:
        sys.exit(f"no .glb in {src_dir}")

    blender = find_blender()

    # 1. model
    if not a.skip_model:
        cmd = [blender, "-b", "--python", os.path.join(TOOLS, "glb_to_web.py"), "--", glb, out_dir, str(a.cloth), str(a.skin), str(a.scale)]
        if a.hair:
            cmd.append("hair=" + a.hair)
        if a.skin_mats:
            cmd.append("skin=" + a.skin_mats)
        run(cmd, log_keep=["avatar:", "-> HAIR", "-> SKIN", "eyes?", "HAIR:", "SKIN:", "WARNING", "-> ", "tris", "HARD-ALPHA", "USD hair", "usdz packaged", "FINAL GLB", "Traceback", "Error"])
        for f in ("model.glb", "model.usdz", "poster.webp"):
            p = os.path.join(out_dir, f)
            print(f"    {f}: {os.path.getsize(p) / 1e6:.1f} MB" if os.path.isfile(p) else f"    {f}: MISSING")

    # 2. video
    if video and not a.skip_video:
        tmp = tempfile.mkdtemp(prefix="garment_video_")
        raw = os.path.join(tmp, "intro.raw.mp4")
        run([blender, "-b", "--python", os.path.join(TOOLS, "encode_video.py"), "--", video, raw, "1280", "MEDIUM"],
            log_keep=["source:", "wrote", "no audio", "failed", "Traceback"])
        dst = os.path.join(out_dir, "intro.mp4")
        try:
            from qtfaststart import processor
            processor.process(raw, dst)
            print("    faststart applied")
        except Exception as e:
            print("    qtfaststart unavailable (pip install qtfaststart); copying as-is:", e)
            shutil.copyfile(raw, dst)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"    intro.mp4: {os.path.getsize(dst) / 1e6:.1f} MB")
    elif not video:
        print("    no video in source folder; the View Video button will be hidden")

    # 3. pattern image
    if pattern:
        ext = os.path.splitext(pattern)[1].lower().replace(".jpeg", ".jpg")
        for old in glob.glob(os.path.join(out_dir, "patterns.*")):
            os.remove(old)
        shutil.copyfile(pattern, os.path.join(out_dir, "patterns" + ext))
        print(f"    patterns{ext}: {os.path.getsize(pattern) / 1e6:.2f} MB")
    else:
        print("    no pattern image in source folder; the View Patterns button will be hidden")

    # 4. manifest
    manifest_path = os.path.join(ROOT, "models.json")
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    entry = next((m for m in data["models"] if str(m["id"]) == a.id), None)
    if entry is None:
        entry = {"id": a.id}
        data["models"].append(entry)
        data["models"].sort(key=lambda m: (len(str(m["id"])), str(m["id"])))
    entry.update({"name": a.name, "glb": f"models/{a.id}/model.glb", "usdz": f"models/{a.id}/model.usdz", "poster": f"models/{a.id}/poster.webp"})
    if a.description:
        entry["description"] = a.description
    entry.setdefault("description", "")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"    models.json: {'updated' if entry else 'added'} garment {a.id}")

    # 5. site + QR
    run([sys.executable, os.path.join(ROOT, "build_site.py")], log_keep=["wrote", "WARNING"])
    base = data.get("base_url")
    if base:
        run([sys.executable, os.path.join(ROOT, "make_qr.py"), base], log_keep=[f"{a.id}-"])
    else:
        print("    models.json has no base_url; skipping QR")
    print(f"\ndone. Review docs/models/{a.id}/poster.webp, then: git add -A && git commit -m \"Add garment {a.id}\" && git push")


if __name__ == "__main__":
    main()
