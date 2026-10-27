#!/usr/bin/env python3
"""Reproduce reviewed ALICE SQM2026 error-box digitization for existing O-O points.

Uses the retained native-resolution panel PNGs, not a rendered slide screenshot.
--source-pdf can reconstruct the panels from the exact source PDF with pdfimages.
The manually reviewed coordinates below are evidence, not automatic feature detection.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parent / "data/OO5360"
SOURCE_URL = "https://indico.global/event/13943/contributions/143551/attachments/68171/132172/SQM26_StrangenessOO_Pucillo-1.pdf"
SOURCE_SHA256 = "b3146a9e118398a653f08c4ec08f4c7ca1a01a8cb6d96ca4b4aebb6e5f365340"
PIXEL_MARGIN = 3.0  # Combined endpoint/marker-center reading budget, not a statistical sigma.
X_PT = [617, 771, 833, 888, 938, 986, 1023, 1059]
X_YIELD = [769, 939, 1008, 1070, 1126, 1180, 1221, 1260]
PT_K_TICKS = ([.5, .6, .7, .8, .9, 1., 1.1, 1.2],
              [1547.5, 1354., 1160., 966., 772., 578.5, 385., 190.5])
PT_B_TICKS = ([.6, .8, 1., 1.2, 1.4, 1.6, 1.8, 2., 2.2, 2.4, 2.6, 2.8],
              [1484., 1356., 1229., 1101., 973., 846., 718., 591., 463., 335., 208., 80.])
# Native pixel coordinates, origin at image top left. Each tuple is the center of the
# upper/lower horizontal box strokes; filled circular markers are not endpoints.
PANELS = {
    "mean_pT_K0S_vs_mult": dict(page=10, image=17, mask=True, figure="ALI-PREL-624471",
        fraction=.03, ticks=PT_K_TICKS, scale="linear", xs=X_PT,
        edges=[(983.,1012.), (903.5,952.), (883.,931.), (866.,913.),
               (849.,897.), (831.,878.), (808.,857.), (776.,825.)]),
    "mean_pT_Xi_tot_vs_mult": dict(page=10, image=13, mask=True, figure="ALI-PREL-624501",
        fraction=.03, ticks=PT_B_TICKS, scale="linear", xs=X_PT[1:],
        edges=[(1029.,1082.5), (1017.,1070.5), (1009.,1061.), (1002.,1054.),
               (992.,1042.), (979.,1027.5), (960.5,1007.)]),
    "mean_pT_Omega_tot_vs_mult": dict(page=10, image=11, mask=True, figure="ALI-PREL-624516",
        fraction=.03, ticks=PT_B_TICKS, scale="linear", xs=X_PT[1:],
        edges=[(865.5,964.5), (852.5,946.5), (844.5,934.5), (834.,921.5),
               (821.,906.5), (809.5,890.5), (786.,862.5)]),
    "dN_dy_K0S_vs_mult": dict(page=21, image=13, mask=True, figure="ALI-PREL-624421",
        fraction=.10, ticks=([.1,1.,10.,100.,1000.],[1178.5,911.,644.,376.,109.]),
        scale="log10", xs=[687,897,984.5,1060,1129.5,1195.5,1247,1296], edges=[None]*8),
    "dN_dy_Xi_tot_vs_mult": dict(page=21, image=16, mask=False, figure="ALI-PREL-624142",
        fraction=.12, ticks=([.01,.1,1.,10.],[1463.5,1078.5,694.,308.5]), scale="log10",
        xs=X_YIELD, edges=[(1219.5,1232.5)]+[None]*7, centers=[1224.5]+[None]*7),
    "dN_dy_Omega_tot_vs_mult": dict(page=21, image=15, mask=False, figure="ALI-PREL-624147",
        fraction=.15, ticks=([.0001,.001,.01,.1,1.,10.],[1732.5,1394.5,1055.,717.,377.5,41.5]),
        scale="log10", xs=X_YIELD,
        edges=[(1191.5,1219.5), (1011.5,1050.), (955.,991.), (902.,937.5),
               (850.,886.), (801.,837.5), (773.5,811.5), (738.,773.5)],
        centers=[1204.5,1030.5,971.,917.5,867.,817.,790.,752.]),
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def panel_name(name):
    return "ALICE_SQM2026_" + name.removesuffix("_vs_mult") + ".png"


def export_panels(pdf, root):
    from PIL import Image
    if sha256(pdf) != SOURCE_SHA256:
        raise ValueError("Source PDF hash differs; coordinates must be reviewed for another version")
    with tempfile.TemporaryDirectory(prefix="alice-sqm-panels-") as directory:
        for page in (10, 21):
            prefix = Path(directory) / f"page{page}"
            subprocess.run([shutil.which("pdfimages") or "pdfimages", "-f", str(page), "-l", str(page),
                            "-j", str(pdf), str(prefix)], check=True)
            for name, spec in PANELS.items():
                if spec["page"] != page:
                    continue
                image = Image.open(f"{prefix}-{spec['image']:03d}.jpg").convert("RGB")
                if spec["mask"]:
                    mask = Image.open(f"{prefix}-{spec['image']+1:03d}.ppm").convert("L")
                    image = Image.composite(image, Image.new("RGB", image.size, "white"), mask)
                image.save(root / "sources" / panel_name(name))


def extract(root=ROOT, *, check=False):
    root = Path(root)
    changed = []
    for name, panel in PANELS.items():
        path = root / "derived" / f"{name}.dat"
        metadata_path = root / "uncertainties" / f"{name}.json"
        existing = json.loads(metadata_path.read_text()) if metadata_path.exists() else None
        data = np.loadtxt(path, ndmin=2)
        tick_values, tick_pixels = panel["ticks"]
        coordinates = np.log10(tick_values) if panel["scale"] == "log10" else np.array(tick_values)
        slope, intercept = np.polyfit(coordinates, tick_pixels, 1)
        pixels_per_unit = abs(float(slope))
        image_path = root / "sources" / panel_name(name)
        rows = []
        for i, (x, value, previous_error) in enumerate(data):
            historical = (existing["rows"][i]["assumed_error_before_extraction"]
                          if existing else float(previous_error))
            row = dict(multiplicity=float(x), value=float(value), assumed_error_before_extraction=historical)
            edge = panel["edges"][i]
            if edge is None:
                error = historical
                row.update(status="assumed", error=error, assumed_fraction=panel["fraction"],
                           reason="Total-error endpoints obscured by the filled marker; not resolved.")
            else:
                top, bottom = edge
                center = panel["centers"][i] if "centers" in panel else (top+bottom)/2
                up, down = center-top, bottom-center
                if panel["scale"] == "linear":
                    plus, minus = up/pixels_per_unit, down/pixels_per_unit
                    error = max(plus, minus) + PIXEL_MARGIN/pixels_per_unit
                else:
                    plus = value * math.expm1(math.log(10)*up/pixels_per_unit)
                    minus = value * -math.expm1(-math.log(10)*down/pixels_per_unit)
                    error = max(value*math.expm1(math.log(10)*(up+PIXEL_MARGIN)/pixels_per_unit),
                                value*-math.expm1(-math.log(10)*(down+PIXEL_MARGIN)/pixels_per_unit))
                experimental = max(plus, minus)
                row.update(status="digitized_total", error=error, experimental_total_plus=plus,
                           experimental_total_minus=minus, experimental_total_symmetric=experimental,
                           digitization_margin=error-experimental, pixel_margin=PIXEL_MARGIN,
                           marker_x_pixel=panel["xs"][i], marker_y_pixel=center,
                           upper_edge_pixel=top, lower_edge_pixel=bottom)
            rows.append(row)
        source = dict(schema=1, source_url=SOURCE_URL, source_pdf_sha256=SOURCE_SHA256,
                      pdf_page=panel["page"], figure=panel["figure"],
                      source_image="sources/"+panel_name(name), source_image_sha256=sha256(image_path),
                      source_image_extraction=dict(pdfimages_index=panel["image"],
                                                   white_composite_of_soft_mask=panel["mask"]),
                      uncertainty_definition="Total uncertainties as labelled in the slide; no stat/sys separation inferred.",
                      export_rule="err=max(digitized total upper,lower) with endpoints expanded by 3 native pixels; "
                                  "unresolved points retain historical assumed errors. The pixel margin is a "
                                  "conservative reading bound, not an experimental or Gaussian uncertainty.",
                      central_values="Original DAT multiplicities and central values retained; log-y error fractions "
                                     "are applied to the retained value. Source center coordinates are approximate.",
                      x_uncertainties="Not extracted or propagated; no covariance information available.",
                      axis=dict(scale=panel["scale"], tick_values=tick_values, tick_y_pixels=tick_pixels,
                                pixels_per_unit=pixels_per_unit, pixel_intercept=float(intercept),
                                max_tick_fit_residual_pixels=float(np.max(abs(slope*coordinates+intercept-tick_pixels)))),
                      rows=rows)
        lines = [f"# ALICE Preliminary SQM2026; {panel['figure']}; PDF p.{panel['page']}; |y|<0.5; full-pT extrapolated.",
                 "# K0S is single-species; Xi/Omega are particle+antiparticle (mean pT of the combined sample).",
                 "# err: digitized total experimental uncertainty + conservative pixel-reading margin where resolved;",
                 "# otherwise historical ASSUMED error, explicitly flagged per point in the uncertainty JSON.",
                 f"# Source, pixel coordinates, uncertainty components/status: ../uncertainties/{name}.json",
                 "# Central values retained. No multiplicity-axis errors or covariance encoded.",
                 "# dNch_deta val err"]
        # Preserve the literal first two columns of every existing data row.
        originals = [line.split() for line in path.read_text().splitlines() if line and not line.startswith("#")]
        lines.extend(f"{old[0]} {old[1]} {row['error']:.10g}" for old,row in zip(originals,rows))
        outputs = {path: "\n".join(lines)+"\n", metadata_path: json.dumps(source,indent=2)+"\n"}
        for output, content in outputs.items():
            if not output.exists() or output.read_text() != content:
                changed.append(str(output))
                if not check:
                    output.write_text(content)
        resolved = sum(r["status"] == "digitized_total" for r in rows)
        fractions = [r["experimental_total_symmetric"]/r["value"] for r in rows if r["status"] == "digitized_total"]
        extent = f"; experimental errors {min(fractions):.2%}–{max(fractions):.2%}" if fractions else ""
        print(f"{name}: {resolved}/{len(rows)} errors resolved{extent}")
    if check and changed:
        raise ValueError("Digitization outputs differ: " + ", ".join(changed))
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT)
    parser.add_argument("--source-pdf", type=Path, help="Re-extract panel images from the pinned PDF before generating.")
    parser.add_argument("--check", action="store_true", help="Verify generated DAT/JSON contents without writing.")
    args = parser.parse_args(argv)
    if args.source_pdf:
        if args.check:
            parser.error("--check cannot be combined with --source-pdf")
        export_panels(args.source_pdf, args.data_dir)
    extract(args.data_dir, check=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
