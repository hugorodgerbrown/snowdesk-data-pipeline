"""
Parse the saved DPBRA XML samples and emit:

  - docs/research/meteofrance/massifs.json       — id, name, dept_hint, region_cluster
  - docs/research/meteofrance/sat_vocabulary.md  — SAT1/SAT2 values seen, with frequency
  - docs/research/meteofrance/field-coverage.md  — which spec fields are present /
                                                   missing / variant across all 36 samples

Run from repo root:
    python3 docs/research/meteofrance/_parse_samples.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import xml.etree.ElementTree as ET
from typing import Any

SAMPLES_DIR = pathlib.Path("docs/research/meteofrance/bulletins-2026-05-18")
OUT_DIR = pathlib.Path("docs/research/meteofrance")

# Massif ID → (cluster, dept_hint).  Cluster derived empirically from the ID
# bands MF uses:  1–23 = Alps, 40–41 = Jura/Vosges, 64–74 = Pyrenees + Corse.
# Refined per actual MF documentation below where known.
CLUSTERS = [
    (1, 23, "Alps"),
    (40, 41, "Corse"),
    (64, 74, "Pyrenees"),
]


def cluster_for(massif_id: int) -> str:
    for lo, hi, name in CLUSTERS:
        if lo <= massif_id <= hi:
            return name
    return "Unknown"


def parse_file(path: pathlib.Path) -> dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != "BULLETINS_NEIGE_AVALANCHE":
        # MF returns <message>…</message> for massifs delegated to another
        # avalanche service (e.g. Andorre).  Record it but skip downstream
        # analysis.
        return {
            "path": str(path),
            "root_tag": root.tag,
            "attrs": dict(root.attrib),
            "is_bulletin": False,
            "redirect_text": (root.text or "").strip()[:200],
        }
    return {
        "path": str(path),
        "root_tag": root.tag,
        "attrs": dict(root.attrib),
        "is_bulletin": True,
        "children": [child.tag for child in root],
        "sat": _extract_sat(root),
        "risque": _extract_risque(root),
        "has_amendement": root.attrib.get("AMENDEMENT", "").lower() == "true",
        "titre": _text(root.find(".//STABILITE/TITRE")),
        "stab_text": _text(root.find(".//STABILITE/TEXTESANSTITRE")),
    }


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _extract_sat(root: ET.Element) -> dict[str, str | None]:
    node = root.find(".//STABILITE/SitAvalTyp")
    if node is None:
        return {"SAT1": None, "SAT2": None}
    return {
        "SAT1": node.attrib.get("SAT1") or None,
        "SAT2": node.attrib.get("SAT2") or None,
    }


def _extract_risque(root: ET.Element) -> dict[str, str | None]:
    node = root.find(".//CARTOUCHERISQUE/RISQUE")
    if node is None:
        return {}
    return {k: v for k, v in node.attrib.items() if v != ""}


def main() -> None:
    files = sorted(SAMPLES_DIR.glob("massif-*.xml"))
    parsed = [parse_file(p) for p in files]

    # ---- Task 2: massif catalogue ----------------------------------------
    catalogue = []
    redirects = []
    for entry in parsed:
        if not entry["is_bulletin"]:
            # Derive massif id from filename: massif-071.xml → 71
            mid = int(pathlib.Path(entry["path"]).stem.split("-")[-1])
            redirects.append({"id": mid, "redirect_text": entry["redirect_text"]})
            continue
        massif_id = int(entry["attrs"]["ID"])
        catalogue.append({
            "id": massif_id,
            "name": entry["attrs"]["MASSIF"],
            "cluster": cluster_for(massif_id),
        })
    catalogue.sort(key=lambda r: r["id"])
    (OUT_DIR / "massifs.json").write_text(
        json.dumps({"massifs": catalogue, "redirects": redirects}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote massifs.json  ({len(catalogue)} bulletins, {len(redirects)} redirects)")
    max_name_len = max(len(c["name"]) for c in catalogue)
    print(f"  longest massif name: {max_name_len} chars")
    if redirects:
        for r in redirects:
            print(f"  redirect: id={r['id']} → {r['redirect_text'][:60]}…")

    # ---- Task 3: SAT vocabulary survey -----------------------------------
    sat_pairs: collections.Counter[tuple[str | None, str | None]] = collections.Counter()
    sat_singletons: collections.Counter[str] = collections.Counter()
    for entry in parsed:
        if not entry["is_bulletin"]:
            continue
        sat = entry["sat"]
        sat_pairs[(sat["SAT1"], sat["SAT2"])] += 1
        if sat["SAT1"]:
            sat_singletons[sat["SAT1"]] += 1
        if sat["SAT2"]:
            sat_singletons[sat["SAT2"]] += 1

    sat_doc = ["# DPBRA `SitAvalTyp` — values observed (2026-05-18 sample)", ""]
    sat_doc.append(f"Sample size: {len(parsed)} bulletins\n")
    sat_doc.append("## SAT codes (frequency = appearances across SAT1 ∪ SAT2)\n")
    sat_doc.append("| SAT code | count |")
    sat_doc.append("|----------|-------|")
    for code in sorted(sat_singletons, key=lambda c: -sat_singletons[c]):
        sat_doc.append(f"| {code} | {sat_singletons[code]} |")
    sat_doc.append("")
    sat_doc.append("## (SAT1, SAT2) pairs\n")
    sat_doc.append("| SAT1 | SAT2 | count |")
    sat_doc.append("|------|------|-------|")
    for (s1, s2), n in sat_pairs.most_common():
        sat_doc.append(f"| {s1 or '∅'} | {s2 or '∅'} | {n} |")
    sat_doc.append("")
    # Per-code text excerpts, to support manual mapping to EAWS problems.
    by_code: dict[str, list[tuple[str, str, str]]] = {}
    for entry in parsed:
        if not entry["is_bulletin"]:
            continue
        name = entry["attrs"].get("MASSIF", "?")
        titre = entry["titre"].replace("\n", " ")
        for slot in ("SAT1", "SAT2"):
            code = entry["sat"][slot]
            if not code:
                continue
            by_code.setdefault(code, []).append(
                (slot, name, titre[:160])
            )

    sat_doc.append("## Characteristic `<TITRE>` text by SAT code\n")
    sat_doc.append(
        "First three bulletins per code, to seed the EAWS-problem mapping.\n"
    )
    for code in sorted(by_code):
        sat_doc.append(f"### SAT = `{code}` ({len(by_code[code])} occurrences)\n")
        for slot, massif, titre in by_code[code][:3]:
            sat_doc.append(f"- [{slot}] **{massif}** — {titre}")
        sat_doc.append("")

    sat_doc.append(
        "## Next step\n"
        "Look up each numeric code in the Meteo-France 2026 avalanche guide "
        "and map it to the closest EAWS *avalanche problem* (`new_snow`, "
        "`wind_slab`, `persistent_weak_layer`, `wet_snow`, `gliding_snow`, "
        "`favourable`, `no_distinct`). Populate the `SAT_TO_EAWS` lookup in "
        "`docs/meteofrance-mapping.md` once confirmed.\n\n"
        "**Caveat:** this is a single end-of-season day. Codes outside {2, 4, 6} "
        "are not represented. Re-run the survey against a peak-season day "
        "(Jan-Feb) before freezing the lookup."
    )
    (OUT_DIR / "sat_vocabulary.md").write_text("\n".join(sat_doc), encoding="utf-8")
    print(f"wrote sat_vocabulary.md  ({len(sat_singletons)} distinct codes)")

    # ---- Task 1: field coverage report -----------------------------------
    root_tags: collections.Counter[str] = collections.Counter()
    root_attr_keys: collections.Counter[str] = collections.Counter()
    child_tags: collections.Counter[str] = collections.Counter()
    risque_attr_keys: collections.Counter[str] = collections.Counter()
    risque_attr_present_when_relevant: collections.Counter[str] = collections.Counter()
    amendement_count = 0
    bulletin_count = 0
    for entry in parsed:
        root_tags[entry["root_tag"]] += 1
        if not entry["is_bulletin"]:
            continue
        bulletin_count += 1
        for k in entry["attrs"]:
            root_attr_keys[k] += 1
        for c in entry["children"]:
            child_tags[c] += 1
        for k in entry["risque"]:
            risque_attr_keys[k] += 1
        if entry["has_amendement"]:
            amendement_count += 1

    fc = [f"# DPBRA field coverage — 2026-05-18 sample ({bulletin_count} bulletins + {len(redirects)} redirects)", ""]
    fc.append("## Root element\n")
    for tag, n in root_tags.most_common():
        fc.append(f"- `{tag}` × {n}")
    fc.append("")
    fc.append("## Root attributes (count = bulletins where attribute is present)\n")
    fc.append("| attribute | count |")
    fc.append("|-----------|-------|")
    for k in sorted(root_attr_keys, key=lambda x: -root_attr_keys[x]):
        fc.append(f"| `{k}` | {root_attr_keys[k]} |")
    fc.append("")
    fc.append(f"`AMENDEMENT=\"true\"` occurred in **{amendement_count}** of {bulletin_count} bulletins.")
    fc.append("")
    fc.append("## Top-level child elements (count = bulletins containing element)\n")
    fc.append("| element | count |")
    fc.append("|---------|-------|")
    for tag, n in child_tags.most_common():
        fc.append(f"| `{tag}` | {n} |")
    fc.append("")
    fc.append("## `<RISQUE>` attribute presence (non-empty value)\n")
    fc.append("| attribute | count |")
    fc.append("|-----------|-------|")
    for k in sorted(risque_attr_keys, key=lambda x: -risque_attr_keys[x]):
        fc.append(f"| `{k}` | {risque_attr_keys[k]} |")
    fc.append("")
    fc.append("## Notes for the mapping spec\n")
    fc.append(
        "- Root tag is `<BULLETINS_NEIGE_AVALANCHE>` (plural) with the bulletin\n"
        "  attributes on the root itself — there is no inner `<BULLETIN>` wrapper.\n"
        "  Update `docs/meteofrance-mapping.md` accordingly.\n"
        "- Attributes that appear < 36 times are optional in practice; the\n"
        "  parser must tolerate absence.\n"
        "- `RISQUE2`/`LOC2`/`ALTITUDE` indicate an elevation-split rating;\n"
        "  bulletins without those attributes use a single rating for the\n"
        "  whole massif.\n"
    )
    (OUT_DIR / "field-coverage.md").write_text("\n".join(fc), encoding="utf-8")
    print(f"wrote field-coverage.md")


if __name__ == "__main__":
    main()
