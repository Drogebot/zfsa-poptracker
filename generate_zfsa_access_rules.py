#!/usr/bin/env python3
"""Generate native PopTracker access_rules from ZFSA static_logic.py.

Run from anywhere:
  python generate_zfsa_access_rules.py --logic-dir path/to/apworld --locations-dir path/to/pack/locations

By default, edits each region JSON in-place after making a .bak copy. Use
--dry-run to validate and report without writing, or --backup/--no-backup.

The AP source's static_logic.py must be importable with its sibling modules,
so the script loads it by file path. Region files are expected to be one JSON
file per region, with the region container as the top-level array's first item.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# PopTracker's native rule format: comma = AND; hyphen = OR.
# Keep these codes aligned with the codes in the pack's items.json.
ITEM_CODES = {
    "pegasus_boots": "progressivepegasusboots",
    "pegasus_bootsL2": "progressivepegasusboots_stage2",
    "lamp": "progressivelamp",
    "lampL2": "progressivelamp_stage2",
    "boomerang": "progressiveboomerang",
    "boomerangL2": "progressiveboomerang_stage2",
    "bow": "progressivebow",
    "bowL2": "progressivebow_stage2",
    "magic_hammer": "progressivemagichammer",
    "magic_hammerL2": "progressivemagichammer_stage2",
    "fire_rod": "progressivefirerod",
    "fire_rodL2": "progressivefirerod_stage2",
    "rocs_feather": "progressiverocsfeather",
    "rocs_featherL2": "progressiverocsfeather_stage2",
    "bombs": "progressivebombs",
    "bombsL2": "progressivebombs_stage2",
    "shovel": "progressiveshovel",
    "shovelL2": "progressiveshovel_stage2",
    "slingshot": "progressiveslingshot",
    "slingshotL2": "progressiveslingshot_stage2",
}

# Rules emitted for AP Has(item_name, count=N). Most AP item names map to their
# lowercase alphanumeric PopTracker code. Add an alias here when the pack uses
# a different code from that normalization.
ITEM_ALIASES = {
    "Progressive Pegasus Boots": "progressivepegasusboots",
    "Progressive Lamp": "progressivelamp",
    "Progressive Boomerang": "progressiveboomerang",
    "Progressive Bow": "progressivebow",
    "Progressive Magic Hammer": "progressivemagichammer",
    "Progressive Fire Rod": "progressivefirerod",
    "Progressive Roc's Feather": "progressiverocsfeather",
    "Progressive Bombs": "progressivebombs",
    "Progressive Shovel": "progressiveshovel",
    "Progressive Slingshot": "progressiveslingshot",
    "Power Bracelet": "powerbracelet",
    "Blue Royal Jewel": "blueroyaljewel",
    "Green Royal Jewel": "greenroyaljewel",
    "Red Royal Jewel": "redroyaljewel",
    "Purple Royal Jewel": "purpleroyaljewel",
    "Progressive Maiden": "progressivemaiden",
    "The Swamp Moon Gate": "theswampmoongate",
    "Victory": "victory",
}


def code_for_item(name: str) -> str:
    if name in ITEM_ALIASES:
        return ITEM_ALIASES[name]
    # This matches the pack's usual code style, e.g. "Cave of No Return
    # Progressive Key" -> "caveofnoreturnprogressivekey".
    return re.sub(r"[^a-z0-9]", "", name.lower())


def atom(code: str, count: int = 1) -> str:
    return code if count == 1 else f"{code}_stage{count}"


def and_dnf(left: list[frozenset[str]], right: list[frozenset[str]]) -> list[frozenset[str]]:
    """Cartesian product of two DNF expressions."""
    if not left or not right:
        return []
    result = {a | b for a in left for b in right}
    return list(result)


def or_dnf(*expressions: list[frozenset[str]]) -> list[frozenset[str]]:
    return list({clause for expr in expressions for clause in expr})


def dnf_atom(value: str) -> list[frozenset[str]]:
    return [frozenset((value,))]


def dnf_true() -> list[frozenset[str]]:
    return [frozenset()]


def dnf_false() -> list[frozenset[str]]:
    return []


def normalize_dnf(expr: list[frozenset[str]]) -> list[frozenset[str]]:
    """Remove duplicate and subsumed clauses (A OR (A AND B) == A)."""
    unique = set(expr)
    keep = [c for c in unique if not any(other < c for other in unique)]
    return sorted(keep, key=lambda c: (len(c), sorted(c)))


@dataclass
class Config:
    level_access: int = 1  # 0 = vanilla regions; 1 = ticket-based
    lamp_for_dark: bool = True
    minor_tricks: bool = False
    glitches_code: str = "[glitches]"


class RuleCompiler:
    def __init__(self, logic, config: Config):
        self.logic = logic
        self.config = config
        self.unmapped: set[str] = set()

    def item_mask(self, mask) -> list[frozenset[str]]:
        rules = []
        for flag, code in ITEM_CODES_BY_FLAG.items():
            if mask & flag:
                rules.extend(dnf_atom(code).copy())
        # `none`/unknown masks should invalidate this alternative just as the
        # AP world's item_mask_rule returns None for an empty mask.
        return normalize_dnf(rules)

    def requirements(self, req) -> list[frozenset[str]]:
        rules = dnf_true()

        # Requirements.items: outer OR, inner AND, each bitmask is an OR.
        if req.items:
            item_alternatives = []
            for item_set in req.items:
                alternative = dnf_true()
                valid = True
                for mask in item_set:
                    mask_expr = self.item_mask(mask)
                    if not mask_expr:
                        valid = False
                        break
                    alternative = and_dnf(alternative, mask_expr)
                if valid:
                    item_alternatives.extend(alternative)
            rules = and_dnf(rules, normalize_dnf(item_alternatives))

        if req.power_bracelet:
            rules = and_dnf(rules, dnf_atom("powerbracelet"))
        if req.jewels:
            for jewel in ("Blue Royal Jewel", "Green Royal Jewel", "Red Royal Jewel", "Purple Royal Jewel"):
                rules = and_dnf(rules, dnf_atom(code_for_item(jewel)))
        for key in req.keys:
            rules = and_dnf(rules, dnf_atom(atom(code_for_item(key.name), key.amount)))

        if req.special_the_mountain_path:
            rules = and_dnf(rules, or_dnf(
                dnf_atom("powerbracelet"),
                dnf_atom("progressivelamp"),
                dnf_atom("progressivemagichammer"),
                dnf_atom("progressivefirerod"),
            ))
        if req.special_the_swamp:
            rules = and_dnf(rules, or_dnf(
                dnf_atom("theswampmoongate"),
                dnf_atom(atom("progressiverocsfeather", 2)),
                dnf_atom(atom("progressivepegasusboots", 2)),
            ))
        if req.special_temple_of_darkness:
            rules = and_dnf(rules, or_dnf(
                dnf_atom("templeofdarknessprogressivekey"),
                dnf_atom("progressiverocsfeather"),
            ))
        if req.special_palace_of_winds:
            rules = and_dnf(rules, or_dnf(
                dnf_atom(atom("palaceofwindsprogressive2fnorthkey", 2)),
                and_dnf(
                    or_dnf(
                        dnf_atom("progressiverocsfeather"),
                        dnf_atom(atom("progressivepegasusboots", 2)),
                    ),
                    dnf_atom("progressivebombs"),
                ),
            ))

        if self.config.lamp_for_dark and req.lamp_for_dark:
            rules = and_dnf(rules, or_dnf(
                dnf_atom("progressivelamp"),
                dnf_atom(self.config.glitches_code)
            ))

        if not self.config.minor_tricks:
            trick_exprs = []
            if req.projectile_regroup:
                trick_exprs.append(or_dnf(
                    dnf_atom("progressivebow"), dnf_atom("progressivefirerod"),
                    dnf_atom("progressiverocsfeather"), dnf_atom("progressivebombs"),
                    dnf_atom("progressiveslingshot"), dnf_atom(atom("progressivepegasusboots", 2)),
                ))
            if req.bomb_regroup:
                trick_exprs.append(dnf_atom("progressivebombs"))
            if req.helmaroc_regroup:
                trick_exprs.append(or_dnf(
                    dnf_atom("progressivemagichammer"),
                    dnf_atom("progressivebombs"),
                ))
            if req.bowless_gohma:
                trick_exprs.append(dnf_atom("progressivebow"))
            if req.jump_pound:
                trick_exprs.append(or_dnf(
                    dnf_atom("progressiverocsfeather"),
                    dnf_atom("progressivebombs"),
                ))
            if req.jump_pound_side:
                trick_exprs.append(or_dnf(
                    dnf_atom("progressivepegasusboots"),
                    dnf_atom("progressiverocsfeather"),
                    dnf_atom("progressivebombs"),
                ))
            if req.desert_cactus:
                trick_exprs.append(or_dnf(
                    dnf_atom("progressivepegasusboots"),
                    dnf_atom("progressiverocsfeather"),
                ))
            if req.desert_death_clip:
                trick_exprs.append(or_dnf(
                    dnf_atom(atom("progressivepegasusboots", 2)),
                    dnf_atom(atom("progressiverocsfeather", 2)),
                ))
            if req.gap_regroup:
                trick_exprs.append(or_dnf(
                dnf_atom("progressiverocsfeather"),
                dnf_atom(atom("progressivepegasusboots", 2)),
            ))
            if req.gap_big_regroup:
                trick_exprs.append(or_dnf(
                    dnf_atom(atom("progressivepegasusboots", 2)),
                    dnf_atom(atom("progressiverocsfeather", 2)),
                ))
            if req.gap_bigest_regroup:
                trick_exprs.append(dnf_atom(atom("progressivepegasusboots", 2)))
            if req.gap_big_bow_regroup:
                trick_exprs.append(or_dnf(
                    dnf_atom("progressivebow"),
                    dnf_atom(atom("progressivepegasusboots", 2)),
                    dnf_atom(atom("progressiverocsfeather", 2))
                ))
            if trick_exprs:
                tricks_together = dnf_true()
                for trick in trick_exprs:
                    tricks_together = and_dnf(tricks_together, trick)
                rules = and_dnf(rules, or_dnf(tricks_together, dnf_atom(self.config.glitches_code)))

        return normalize_dnf(rules)

    def entrance(self, region, parent_region=None) -> list[frozenset[str]]:
        rules = dnf_true()
        req = region.requirements
        if req.jewels:
            for jewel in ("Blue Royal Jewel", "Green Royal Jewel", "Red Royal Jewel", "Purple Royal Jewel"):
                rules = and_dnf(rules, dnf_atom(code_for_item(jewel)))
            rules = and_dnf(rules, dnf_atom(atom("progressivemaiden", 6)))
        if req.zelda:
            rules = and_dnf(rules, dnf_atom(atom("progressivemaiden", 7)))

        if self.config.level_access == 1:
            ticket = None
            if region.stageId % 10 > 0:
                level = int(region.stageId / 10)
                amount = 2 if region.stageId % 10 == 3 else 1
                ticket = (f"progressivelevel{level}ticket", amount)
            if ticket:
                rules = and_dnf(rules, dnf_atom(atom(*ticket)))
        elif parent_region is not None:
            # CanReachRegion(parent) means the whole entrance chain to the
            # parent is satisfied. Flatten that chain into native rules so the
            # tracker does not need a custom reachability evaluator.
            ancestor = parent_region
            while ancestor is not None:
                ancestor_req = ancestor.requirements
                rules = and_dnf(rules, self.requirements(ancestor_req))
                if ancestor_req.jewels:
                    for jewel in ("Blue Royal Jewel", "Green Royal Jewel", "Red Royal Jewel", "Purple Royal Jewel"):
                        rules = and_dnf(rules, dnf_atom(code_for_item(jewel)))
                    rules = and_dnf(rules, dnf_atom(atom("progressivemaiden", 6)))
                if ancestor_req.zelda:
                    rules = and_dnf(rules, dnf_atom(atom("progressivemaiden", 7)))
                ancestor = self.logic.ALL_REGIONS.get(ancestor.parentRegion) if ancestor.parentRegion else None
        return normalize_dnf(rules)

    def serialize(self, expr: list[frozenset[str]]) -> list[str]:
        expr = normalize_dnf(expr)
        if not expr:
            # Empty access_rules means no alternative is accessible in some
            # PopTracker contexts; use an impossible sentinel rule instead.
            return ["__zfsa_never_defined_item__"]
        if expr == [frozenset()]:
            return [""]
        rendered = []
        for clause in expr:
            # Do not rely on Python set ordering for stable output.
            rendered.append(",".join(sorted(clause)))
        # PopTracker uses hyphen as OR separator; item codes containing hyphens
        # would need escaping/another strategy. Current generated codes don't.
        return ["\",\"".join(rendered)]


def load_logic(logic_dir: Path):
    path = logic_dir / "static_logic.py"
    if not path.exists():
        raise FileNotFoundError(f"Could not find {path}")
    # static_logic.py has no relative imports, so it can be loaded directly.
    spec = importlib.util.spec_from_file_location("zfsa_static_logic_for_tracker", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def resolve_region_file(locations_dir: Path, region_name: str) -> Path | None:
    # Prefer exact filename, then normalized filename. Supports .json or .JSON.
    exact_candidates = [locations_dir / f"{region_name}.json", locations_dir / f"{region_name}.JSON"]
    for candidate in exact_candidates:
        if candidate.exists():
            return candidate
    norm = re.sub(r"[^a-z0-9]", "", region_name.lower())
    for path in locations_dir.glob("*.json"):
        if re.sub(r"[^a-z0-9]", "", path.stem.lower()) == norm:
            return path
    return None


def set_rules(node: dict, rules: list[str]) -> None:
    node["access_rules"] = rules


def find_child(container: dict, name: str) -> dict | None:
    for child in container.get("children", []):
        if child.get("name") == name:
            return child
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logic-dir", type=Path, required=True, help="Directory containing static_logic.py")
    parser.add_argument("--locations-dir", type=Path, required=True, help="Directory containing per-region JSON files")
    parser.add_argument("--level-access", choices=("vanilla", "tickets"), default="tickets", help="Match AP level_access option (default: tickets)")
    parser.add_argument("--lamp-for-dark", action="store_true", default=True, help="Enable AP lamp_for_dark option")
    parser.add_argument("--minor-tricks", action="store_true", help="Enable AP minor_tricks option")
    parser.add_argument("--glitches-code", default="glitches", help="PopTracker item code for the AP glitches item")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report changes without writing files")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak copies before writing")
    args = parser.parse_args()

    logic = load_logic(args.logic_dir)
    global ITEM_CODES_BY_FLAG
    ITEM_CODES_BY_FLAG = {getattr(logic.ItemType, name): code for name, code in ITEM_CODES.items()}
    config = Config(
        level_access=0 if args.level_access == "vanilla" else 1,
        lamp_for_dark=args.lamp_for_dark,
        minor_tricks=args.minor_tricks,
        glitches_code=f"[{args.glitches_code}]",
    )
    compiler = RuleCompiler(logic, config)
    errors: list[str] = []
    changed = 0

    for region_name, region in logic.ALL_REGIONS.items():
        path = resolve_region_file(args.locations_dir, region_name)
        if path is None:
            errors.append(f"Missing JSON file for region: {region_name!r}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            errors.append(f"{path.name}: expected a non-empty JSON array with region container at [0]")
            continue
        container = data[0]
        if container.get("name") != region_name:
            errors.append(f"{path.name}: root name is {container.get('name')!r}, expected {region_name!r}")
            continue

        # Root/container access represents entering this region. The AP entrance
        # rule depends on its parent and level-access configuration.
        parent = logic.ALL_REGIONS.get(region.parentRegion) if region.parentRegion else None
        set_rules(container, compiler.serialize(compiler.entrance(region, parent)))

        # The Stage Clear check uses the region's own requirements.
        stage_clear_names = ("Ganon's Defeat!", "Palace of Winds - Ganon's Defeat!") if region_name == "Palace of Winds" else ("Stage Clear",)
        stage_node = next((find_child(container, name) for name in stage_clear_names if find_child(container, name) is not None), None)
        if stage_node is None:
            errors.append(f"{path.name}: couldn't find stage-clear child; tried {stage_clear_names!r}")
        else:
            set_rules(stage_node, compiler.serialize(compiler.requirements(region.requirements)))

        # Chest rules are the local requirements only (same as zfsa_chest_rule).
        for chest in region.chests:
            node = find_child(container, chest.name)
            if node is None:
                errors.append(f"{path.name}: couldn't find chest child {chest.name!r}")
            else:
                set_rules(node, compiler.serialize(compiler.requirements(chest.requirements)))

        # Jewel rules are the local requirements only (same as zfsa_jewel_rule).
        if region.jewel is not None:
            node = find_child(container, region.jewel.name)
            if node is None:
                errors.append(f"{path.name}: couldn't find jewel child {region.jewel.name!r}")
            else:
                set_rules(node, compiler.serialize(compiler.requirements(region.jewel.requirements)))

        # Key checks are currently commented out in regions.py; don't modify any
        # JSON nodes for keys/pearls unless that AP world enables them as checks.
        serialized = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        serialized = serialized.replace("\\\"", "\"")
        original = path.read_text(encoding="utf-8")
        if serialized != original:
            if not args.dry_run:
                if not args.no_backup:
                    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
                path.write_text(serialized, encoding="utf-8")
            changed += 1

    print(f"Regions in static_logic.py: {len(logic.ALL_REGIONS)}")
    print(f"Files changed/would change: {changed}")
    print(f"Configuration: level_access={args.level_access}, lamp_for_dark={config.lamp_for_dark}, minor_tricks={config.minor_tricks}, glitches_code={config.glitches_code!r}")
    if errors:
        print("\nProblems:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("Dry run only; no files were written.")
    elif not args.no_backup:
        print("Backups were written next to changed files with a .bak suffix.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
