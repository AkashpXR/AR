"""Generate one printable QR code per garment.

usage: python make_qr.py https://your-host.example/base/
Writes qr/<id>-<name>.png (one code each, labelled) and qr/qr-sheet.pdf (A4 pages at 300 dpi, eight
codes per page, each 5.6 cm wide with its label, ready to print and cut). Each code encodes <base>/<id>/,
that garment's AR page.
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
    write_sheet(models, base)


def write_sheet(models, base, dpi=300):
    """A4 pages, 2 x 4 codes per page, each code 7 cm wide with a cut margin and the garment name under it."""
    mm = dpi / 25.4
    page_w, page_h = int(210 * mm), int(297 * mm)
    cols, rows = 2, 4
    cell_w, cell_h = page_w // cols, page_h // rows
    code_px = int(56 * mm)   # 5 mm margin + 56 mm code + label + url fit the 74 mm row
    try:
        font = ImageFont.truetype("arial.ttf", int(4.5 * mm))
        small = ImageFont.truetype("arial.ttf", int(3 * mm))
    except OSError:
        font = small = ImageFont.load_default()
    pages = []
    for start in range(0, len(models), cols * rows):
        page = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(page)
        for k, m in enumerate(models[start:start + cols * rows]):
            url = f"{base}{m['id']}/"
            qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, box_size=10, border=2)
            qr.add_data(url); qr.make(fit=True)
            code = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((code_px, code_px), Image.NEAREST)
            cx = (k % cols) * cell_w; cy = (k // cols) * cell_h
            x = cx + (cell_w - code_px) // 2; y = cy + int(5 * mm)
            page.paste(code, (x, y))
            label = f"{m['id']} · {m['name']}"
            tw = draw.textlength(label, font=font)
            draw.text((cx + (cell_w - tw) / 2, y + code_px + int(2 * mm)), label, fill="black", font=font)
            tw = draw.textlength(url, font=small)
            draw.text((cx + (cell_w - tw) / 2, y + code_px + int(8.5 * mm)), url, fill=(110, 110, 110), font=small)
            # light cut guides around the cell
            draw.rectangle([cx + 2, cy + 2, cx + cell_w - 3, cy + cell_h - 3], outline=(200, 200, 200), width=2)
        pages.append(page)
    path = os.path.join(OUT, "qr-sheet.pdf")
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=dpi)
    print(f"{path}  ({len(pages)} A4 page(s), {len(models)} codes)")
    return pages


if __name__ == "__main__":
    main()
