"""Generate one printable QR code per garment.

usage: python make_qr.py https://your-host.example/base/
Writes qr/<id>-<name>.png. Each code encodes <base>/<id>/ which is that garment's AR page.
"""
import json, os, re, sys
import qrcode
from qrcode.constants import ERROR_CORRECT_Q
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "qr")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python make_qr.py https://host/base/")
    base = sys.argv[1].rstrip("/") + "/"
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(ROOT, "models.json"), encoding="utf-8") as f:
        models = json.load(f)["models"]
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    for m in models:
        url = f"{base}{m['id']}/"
        qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, box_size=12, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        code = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        w, h = code.size
        canvas = Image.new("RGB", (w, h + 90), "white")
        canvas.paste(code, (0, 0))
        draw = ImageDraw.Draw(canvas)
        label = f"{m['id']} · {m['name']}"
        tw = draw.textlength(label, font=font)
        draw.text(((w - tw) / 2, h + 10), label, fill="black", font=font)
        slug = re.sub(r"[^a-z0-9]+", "-", m["name"].lower()).strip("-")
        path = os.path.join(OUT, f"{m['id']}-{slug}.png")
        canvas.save(path)
        print(f"{path}  ->  {url}  (version {qr.version}, {w}px)")


if __name__ == "__main__":
    main()
