#!/usr/bin/env python3
"""Make visual checks of retained ALICE error-box coordinates, without editing data.

Requires Pillow, numpy and reportlab. Review pages contain the native source panel
with annotations and paired raw/annotated nearest-neighbour point enlargements.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from extract_alice_uncertainties import PANELS, ROOT


BLUE = "#0077bb"
MAGENTA = "#bc2289"
AMBER = "#966000"
INK = "#172b3a"
GRAY = "#53616b"
FONT_DIR = Path("/usr/share/fonts/truetype/liberation2")
ORDER = [f"{observable}_{species}_vs_mult"
         for observable in ("mean_pT", "dN_dy")
         for species in ("K0S", "Xi_tot", "Omega_tot")]


def font(size, bold=False):
    return ImageFont.truetype(str(FONT_DIR / ("LiberationSans-Bold.ttf" if bold
                                            else "LiberationSans-Regular.ttf")), size)


def source_position(row, spec, axis, index):
    if row["status"] == "digitized_total":
        return row["marker_x_pixel"], row["marker_y_pixel"]
    value = math.log10(row["value"]) if axis["scale"] == "log10" else row["value"]
    return spec["xs"][index], axis["pixel_intercept"] - axis["pixels_per_unit"] * value


def annotate(image, row, x, y):
    """Use separated side ticks so that source endpoints remain visible."""
    draw = ImageDraw.Draw(image)
    if row["status"] == "assumed":
        draw.ellipse((x-23, y-23, x+23, y+23), outline=AMBER, width=2)
        return
    for edge, sign in ((row["upper_edge_pixel"], -1), (row["lower_edge_pixel"], 1)):
        for side in (-1, 1):
            draw.line((x+side*18, edge, x+side*30, edge), fill=BLUE, width=2)
            outer = edge + sign * row["pixel_margin"]
            draw.line((x+side*33, outer, x+side*42, outer), fill=MAGENTA, width=2)


def make_page(name, root, out):
    metadata = json.loads((root / "uncertainties" / f"{name}.json").read_text())
    source_path = root / metadata["source_image"]
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != metadata["source_image_sha256"]:
        raise ValueError(f"Source image hash differs: {source_path}")
    source = Image.open(source_path).convert("RGB")
    overlay = source.copy()
    spec = PANELS[name]
    positions = [source_position(row, spec, metadata["axis"], i)
                 for i, row in enumerate(metadata["rows"])]
    for row, (x, y) in zip(metadata["rows"], positions):
        annotate(overlay, row, x, y)
    overlay.save(out / f"{name}_overlay.png")

    sheet = Image.new("RGB", (3300, 2333), "white")
    draw = ImageDraw.Draw(sheet)
    observable = "Mean pT" if name.startswith("mean") else "Integrated yield dN/dy"
    species = "K0S" if "K0S" in name else "Xi + anti-Xi" if "Xi" in name else "Omega + anti-Omega"
    draw.text((40, 25), f"{observable}: {species} | O-O, 5.36 TeV", font=font(46, True), fill=INK)
    draw.text((40, 85), f"ALICE SQM2026 | PDF page {metadata['pdf_page']} | {metadata['figure']}",
              font=font(30), fill=GRAY)
    for x, color, text in ((40, BLUE, "Blue: extracted total-error edges"),
                           (1120, MAGENTA, "Magenta: edges expanded by 3 native pixels"),
                           (2440, AMBER, "Amber: error still assumed")):
        draw.line((x, 154, x+50, 154), fill=color, width=6)
        draw.text((x+65, 133), text, font=font(28), fill=color)
    draw.text((40, 196), "Full source panel with annotations", font=font(32, True), fill=INK)
    draw.text((1690, 196), "Point checks: original pixels | annotated pixels", font=font(32, True), fill=INK)

    # The full panel retains its aspect ratio. Only the point crops use nearest
    # neighbour enlargement; their coordinates are rounded only at crop bounds.
    panel = overlay.copy()
    panel.thumbnail((1590, 1870), Image.Resampling.LANCZOS)
    sheet.paste(panel, (40 + (1590-panel.width)//2, 250 + (1870-panel.height)//2))
    resolved = 0
    for i, (row, (x, y)) in enumerate(zip(metadata["rows"], positions)):
        col, line = i % 2, i // 2
        left, top = 1690 + col*795, 255 + line*470
        draw.rounded_rectangle((left, top, left+770, top+445), radius=12,
                               fill="#f5f7f9", outline="#d1dbe1", width=2)
        status = row["status"] == "digitized_total"
        color = BLUE if status else AMBER
        draw.text((left+16, top+12), f"#{i+1}   <dNch/deta> = {row['multiplicity']:.4g}",
                  font=font(28, True), fill=INK)
        box = (round(x)-50, round(y)-68, round(x)+50, round(y)+68)
        # Show only this point's annotations in each enlargement. Neighbouring
        # points remain visible as source context, without unrelated blue ticks.
        point_overlay = source.copy()
        annotate(point_overlay, row, x, y)
        for offset, im, label in ((16, source, "Original"), (230, point_overlay, "Overlay")):
            crop = im.crop(box).resize((200, 272), Image.Resampling.NEAREST)
            sheet.paste(crop, (left+offset, top+92))
            draw.text((left+offset+28, top+55), label, font=font(25), fill=GRAY)
        text_x = left+450
        draw.text((text_x, top+65), "Resolved" if status else "ASSUMED", font=font(27, True), fill=color)
        draw.text((text_x, top+107), f"y = {row['value']:.6g}", font=font(26), fill=INK)
        draw.text((text_x, top+146), f"DAT error = {row['error']:.5g}", font=font(25), fill=INK)
        draw.text((text_x, top+185), f"({100*row['error']/row['value']:.2f}% of y)", font=font(25), fill=INK)
        if status:
            resolved += 1
            draw.text((text_x, top+232), "Exp. total: + / -", font=font(25), fill=BLUE)
            draw.text((text_x, top+269), f"{row['experimental_total_plus']:.5g} /", font=font(25), fill=BLUE)
            draw.text((text_x, top+304), f"{row['experimental_total_minus']:.5g}", font=font(25), fill=BLUE)
            draw.text((left+16, top+394),
                      f"Edges y(px): {row['upper_edge_pixel']:g}, {row['lower_edge_pixel']:g}; "
                      f"center {row['marker_y_pixel']:g}", font=font(25), fill=GRAY)
        else:
            draw.text((text_x, top+232), "Endpoints hidden", font=font(25), fill=AMBER)
            draw.text((text_x, top+270), "by filled marker.", font=font(25), fill=AMBER)
            draw.text((left+16, top+394), "No experimental error extracted; ring locates the point.",
                      font=font(25), fill=AMBER)

    # The last row has only seven points for Xi/Omega mean pT.
    if len(metadata["rows"]) == 7:
        draw.text((2510, 1850), "Seven O-O points retained in this table.", font=font(27), fill=GRAY)
    draw.text((40, 2175), f"{resolved}/{len(metadata['rows'])} uncertainties resolved. "
              "Source pixels are unchanged in the Original crops; all crops enlarged 2x with nearest-neighbour sampling.",
              font=font(28), fill=INK)
    draw.text((40, 2220), "For resolved points, DAT error = larger extracted +/- error, with the 3-pixel reading margin; "
              "the reading margin is separate from the experimental uncertainty.", font=font(28), fill=GRAY)
    draw.text((40, 2265), "Marks follow recorded source-pixel coordinates. Retained DAT central values "
              "can differ slightly from source centers. No model-error floor is drawn.", font=font(28), fill=GRAY)
    path = out / f"{name}_review.png"
    sheet.save(path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "review")
    args = parser.parse_args()
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    args.output_dir.mkdir(parents=True, exist_ok=True)
    pages = [make_page(name, args.data_dir, args.output_dir) for name in ORDER]
    pdf_path = args.output_dir / "ALICE_OO_uncertainty_digitization_review.pdf"
    size = landscape(A3)
    pdf = canvas.Canvas(str(pdf_path), pagesize=size)
    pdf.setTitle("ALICE O-O uncertainty digitization: original plots and overlays")
    pdf.setAuthor("O-O calibration data review")
    for page in pages:
        pdf.drawImage(ImageReader(str(page)), 0, 0, width=size[0], height=size[1])
        pdf.showPage()
    pdf.save()
    print(f"Wrote {len(pages)} review sheets, {len(pages)} native-panel overlays and {pdf_path}")


if __name__ == "__main__":
    main()
