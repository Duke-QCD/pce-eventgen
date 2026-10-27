#!/usr/bin/env python3
"""Check O-O measurement files and retained source evidence without a model matrix.

This is an offline consistency check, not independent certification of HEPData.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


DEFAULT_DATA = Path(__file__).resolve().parent / "data/OO5360"
CENTRALITY = {
    "dN_deta_charged_CMS": 14,
    "v2_2_charged_ALICE": 16,
    "v3_2_charged_ALICE": 16,
    "mean_pT_charged_ATLAS": 20,
}
IDENTIFIED = {
    "dN_dy_K0S_vs_mult": (8, .10),
    "dN_dy_Xi_tot_vs_mult": (8, .12),
    "dN_dy_Omega_tot_vs_mult": (8, .15),
    "mean_pT_K0S_vs_mult": (8, .03),
    "mean_pT_Xi_tot_vs_mult": (7, .03),
    "mean_pT_Omega_tot_vs_mult": (7, .03),
}
WARNINGS = (
    "15/46 identified points retain assumed errors: all 8 K0S yields and 7 Xi yields.",
    "31/46 identified errors are digitized experimental totals with a conservative 3-native-pixel "
    "reading margin; they are approximate and do not separate statistical/systematic errors.",
    "Identified multiplicity x-errors and all experimental covariance matrices are absent.",
    "The ALICE/ATLAS JSON files are retained transcriptions; fresh HEPData access was denied "
    "during the 2026-10-26 audit, so agreement with them is an internal check only.",
    "The alternative ATLAS pT=0 table has no separate uncertainty-component snapshot and is "
    "incompatible with the current model profile.",
    "The 5% discrepancy floor is an independent diagonal term, not an experimental covariance.",
    "Model acceptance, centrality, interpolation coverage and K0S mean-pT reachability require "
    "the campaign matrix; this check does not establish them.",
)


def read_rows(path, columns):
    rows = []
    for line in path.read_text().splitlines():
        content = line.split("#", 1)[0].strip()
        if content:
            row = [float(value) for value in content.split()]
            if len(row) != columns or not all(math.isfinite(v) for v in row):
                raise ValueError("wrong column count or non-finite data")
            rows.append(row)
    if not rows:
        raise ValueError("empty measurement table")
    return rows


def check_identified(root, name, rows):
    """Validate the generated errors against source coordinates and retained image hashes."""
    evidence = json.loads((root / f"uncertainties/{name}.json").read_text())
    image = root / evidence["source_image"]
    if hashlib.sha256(image.read_bytes()).hexdigest() != evidence["source_image_sha256"]:
        raise ValueError("source panel image hash differs")
    if len(rows) != len(evidence["rows"]):
        raise ValueError("identified source row count differs")
    axis = evidence["axis"]
    scale = axis["pixels_per_unit"]
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("invalid digitization axis scale")
    for value, pixel in zip(axis["tick_values"], axis["tick_y_pixels"]):
        v = math.log10(value) if axis["scale"] == "log10" else value
        if abs(axis["pixel_intercept"]-scale*v-pixel) > 1.5:
            raise ValueError("digitization tick calibration differs")
    resolved = 0
    for i, ((x, value, error), source) in enumerate(zip(rows, evidence["rows"])):
        if x != source["multiplicity"] or value != source["value"]:
            raise ValueError(f"row {i+1}: retained central value/multiplicity differs")
        if source["status"] == "assumed":
            expected = source["assumed_error_before_extraction"]
            if not math.isclose(expected, IDENTIFIED[name][1]*value, rel_tol=1e-5):
                raise ValueError(f"row {i+1}: unresolved assumed error changed")
        elif source["status"] == "digitized_total":
            resolved += 1
            up = source["marker_y_pixel"]-source["upper_edge_pixel"]
            down = source["lower_edge_pixel"]-source["marker_y_pixel"]
            budget = source["pixel_margin"]
            if min(up, down) <= 0 or budget != 3:
                raise ValueError(f"row {i+1}: invalid source error-box endpoints/reading budget")
            if axis["scale"] == "linear":
                plus, minus = up/scale, down/scale
                expected = (max(up, down)+budget)/scale
            else:
                plus = value*math.expm1(math.log(10)*up/scale)
                minus = value*-math.expm1(-math.log(10)*down/scale)
                expected = max(value*math.expm1(math.log(10)*(up+budget)/scale),
                               value*-math.expm1(-math.log(10)*(down+budget)/scale))
            if not all(math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12) for a,b in (
                    (plus, source["experimental_total_plus"]),
                    (minus, source["experimental_total_minus"]),
                    (max(plus, minus), source["experimental_total_symmetric"]),
                    (expected-max(plus, minus), source["digitization_margin"]))):
                raise ValueError(f"row {i+1}: digitized error components differ from pixel evidence")
        else:
            raise ValueError(f"row {i+1}: unknown uncertainty status")
        if not math.isclose(error, expected, rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError(f"row {i+1}: exported error differs from source evidence")
    expected_resolved = (0 if name == "dN_dy_K0S_vs_mult" else
                         1 if name == "dN_dy_Xi_tot_vs_mult" else len(rows))
    if resolved != expected_resolved:
        raise ValueError("resolved/assumed point inventory differs")


def audit(root=DEFAULT_DATA):
    root = Path(root)
    errors, checks = [], []
    tables = {}
    expected = {f"centrality/{name}.dat": (5, count) for name, count in CENTRALITY.items()}
    expected.update({f"derived/{name}.dat": (3, count) for name, (count, _) in IDENTIFIED.items()})
    expected["derived/mean_pT_charged_ATLAS_pT0.dat"] = (5, 20)
    actual = {str(p.relative_to(root)) for p in root.rglob("*.dat")}
    proxy_paths = {f"optional/proxy_dN_dy_{key}_vs_mult.dat" for key in ("pion","kaon","proton")}
    # This table is an explicitly labelled diagnostic, not an additional datum.
    diagnostic_paths = {"optional/proxy_multiplicity_closure.dat"}
    inventory = set(expected) | proxy_paths | diagnostic_paths
    if actual != inventory:
        errors.append(f"measurement inventory mismatch: missing={sorted(inventory-actual)}, "
                      f"unexpected={sorted(actual-inventory)}")
    for relative, (columns, count) in expected.items():
        try:
            rows = read_rows(root / relative, columns)
            if len(rows) != count:
                raise ValueError(f"expected {count} rows, got {len(rows)}")
            if columns == 5:
                for i, (lo, hi, mid, value, error) in enumerate(rows):
                    if not (0 <= lo < hi <= 100 and math.isclose(mid, (lo+hi)/2, abs_tol=1e-8)):
                        raise ValueError(f"row {i+1}: invalid centrality/midpoint")
                    if i and lo < rows[i-1][1]:
                        raise ValueError(f"row {i+1}: overlapping/unordered bins")
                    if value <= 0 or error <= 0:
                        raise ValueError(f"row {i+1}: nonpositive value/error")
            else:
                for i, (x, value, error) in enumerate(rows):
                    if min(x, value, error) <= 0 or (i and x <= rows[i-1][0]):
                        raise ValueError(f"row {i+1}: invalid value/error or unordered multiplicity")
                check_identified(root, Path(relative).stem, rows)
            tables[relative] = rows
            checks.append(f"{relative}: {count} rows, valid format/bins/errors")
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{relative}: {error}")
    for name in ("v2_2_charged_ALICE", "v3_2_charged_ALICE", "mean_pT_charged_ATLAS"):
        try:
            snapshot = json.loads((root / f"uncertainties/{name}.json").read_text())
            expected_record = "3170344" if "ATLAS" in name else "2967353"
            expected_table = {"v2_2_charged_ALICE": "Table_v22.yaml",
                              "v3_2_charged_ALICE": "Table_v32.yaml",
                              "mean_pT_charged_ATLAS":
                              "MeanpT_vs_eta_OO_centdep_midrapidity_pT270MeV.yaml"}[name]
            if snapshot["source_record"] != expected_record or snapshot["source_table"] != expected_table:
                raise ValueError("wrong source record/table")
            qualifiers = {q["name"]: q["value"] for q in snapshot["qualifiers"]}
            if qualifiers.get("Collision species", qualifiers.get("RE")) not in ("O-O", "OO"):
                raise ValueError("source qualifier is not O-O")
            rows = tables[f"centrality/{name}.dat"]
            if len(rows) != len(snapshot["rows"]):
                raise ValueError("source row count differs")
            for i, (stored, source) in enumerate(zip(rows, snapshot["rows"])):
                stat, plus, minus = (source[k] for k in ("statistical", "sys_plus", "sys_minus"))
                if not all(math.isfinite(v) and v >= 0 for v in (stat, plus, minus)):
                    raise ValueError(f"row {i+1}: invalid source uncertainty component")
                tp, tm = math.hypot(stat, plus), math.hypot(stat, minus)
                expected_row = [source["cent_low"], source["cent_high"], source["value"], max(tp, tm)]
                numbers = [stored[0], stored[1], stored[3], stored[4], source["total_plus"], source["total_minus"]]
                if not all(math.isclose(a, b, rel_tol=1e-10, abs_tol=5e-12)
                           for a, b in zip(numbers, expected_row + [tp, tm])):
                    raise ValueError(f"row {i+1}: source value/bin/error arithmetic differs")
            checks.append(f"{name}: all values/bins/error totals match retained source snapshot")
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{name}: {error}")
    try:
        evidence = json.loads((root / "uncertainties/dN_deta_charged_CMS.json").read_text())
        rows = tables["centrality/dN_deta_charged_CMS.dat"]
        if len(rows) != len(evidence["rows"]):
            raise ValueError("independent figure row count differs")
        # Fixed tolerance: a modified evidence file cannot loosen this check.
        for i, (stored, source) in enumerate(zip(rows, evidence["rows"])):
            if (stored[:2] != [source["cent_low"], source["cent_high"]]
                    or abs(stored[3]-source["value"]) > .005
                    or abs(stored[4]-source["error"]) > .005):
                raise ValueError(f"row {i+1}: independent CMS vector extraction differs")
        checks.append("CMS: all values/errors agree with independent figure extraction within 0.005")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f"CMS independent figure check: {error}")
    try:
        from build_oo_yield_proxies import build
        metadata = json.loads((root / "optional/yield_proxy_metadata.json").read_text())
        widths = metadata["assumed_fractional_widths"]
        build(root,check=True,transfer_kaon=widths["kaon_ratio_transfer"],
              transfer_proton=widths["proton_ratio_transfer"],isospin=widths["charged_neutral_kaon_isospin"])
        checks.append("Optional proxies: 24 yields and joint 32x32 anchor/proxy covariance reproduce from source evidence")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f"Optional yield proxies: {error}")
    return dict(checks=checks, errors=errors, warnings=list(WARNINGS) + [
        "Optional light-hadron proxies are ASSUMED, disabled by default, and require their joint "
        "covariance; their K0S anchor errors are assumed too."])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA, help="O-O root containing centrality/.")
    args = parser.parse_args(argv)
    result = audit(args.data_dir)
    for message in result["checks"]:
        print("PASS:", message)
    for message in result["warnings"]:
        print("FLAG:", message)
    for message in result["errors"]:
        print("ERROR:", message)
    print(f"{len(result['checks'])} checks passed; {len(result['errors'])} errors. "
          "Flags remain even when the offline checks pass.")
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
