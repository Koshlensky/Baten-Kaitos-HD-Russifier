"""Пересборка tools/install/ui_targets.json: какие сцены, префабы и атласы затрагивает сборка, их оригинальные хеши и CRC.\n\nИспользование: python -m tools.install.gen_targets  (игра должна быть в оригинальном состоянии либо иметь _rus_v2/originals).\n"""
import json
from tools.install import ui_patch as u
from tools.bkloc.paths import bundle_path, game_dir, CATALOG_SUB, GAME_ROOT, sha256_file, write_json

out = {}
for game in ("bk1", "bk2"):
    rows = u.find_targets(game)
    for r in rows:
        p = bundle_path(game, r["bundle"])
        r["relative"] = str(p.relative_to(GAME_ROOT))
        r["original_sha256"] = sha256_file(u.original_or_game(p))
        r["size"] = u.original_or_game(p).stat().st_size
        print(game, r["bundle"][:8], r["hits"], r["crc"], r["assets"][0])
    atl = []
    if game in ("bk1", "bk2"):
        from tools.gfx import build as gb
        from tools.bkloc.catalog import Catalog
        c = Catalog(game, u.original_or_game(game_dir(game) / CATALOG_SUB))
        for kind, (g_, pre) in gb.KINDS.items():
            if g_ != game:
                continue
            p = next(bundle_path(game, "x").parent.glob(pre + "*.bundle"))
            atl.append({"kind": kind, "bundle": p.name, "relative": str(p.relative_to(GAME_ROOT)), "original_sha256": sha256_file(u.original_or_game(p)),
                        "size": u.original_or_game(p).stat().st_size, "crc": (c.bundle_options(p.name) or {}).get("m_Crc", 0)})
            print("atlas", kind, p.name[:8], atl[-1]["crc"])
    cat = game_dir(game) / CATALOG_SUB
    out[game] = {"atlases": atl, "bundles": rows, "catalog": {"relative": str(cat.relative_to(GAME_ROOT)), "original_sha256": sha256_file(u.original_or_game(cat))}}
write_json(u.TARGETS, out)
