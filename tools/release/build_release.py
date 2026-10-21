"""Сборка релиза: BatenKaitos_Rusifikator_v2.0.exe (PyInstaller, один файл) из утверждённой тестовой сборки dist/test_v2.0.

Этапы: проверка актуальности сборки (`verify-build`), подготовка data/ (payload + manifest), упаковка, контрольные суммы.
Игра не затрагивается.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.install import full  # noqa: E402
from tools.bkloc.paths import sha256_file  # noqa: E402
from tools.release import core  # noqa: E402

DELTA = "--delta" in sys.argv
STAGE = ROOT / "work" / ("release_stage_delta" if DELTA else "release_stage")
PYI = ROOT / "work" / ("release_pyi_delta" if DELTA else "release_pyi")
OUT = ROOT / "dist" / ("release_delta" if DELTA else "release")
EXE_NAME = "BatenKaitos_Rusifikator_v2.0" + ("_delta" if DELTA else "")


def known_states():
    states = {}
    for pattern in ("pilot_*.json", "full_*.json"):
        for path in sorted(full.LOGS.glob(pattern)):
            log = json.loads(path.read_text(encoding="utf-8"))
            for f in log.get("files", []):
                states.setdefault(f["relative"].replace("\\", "/"), set()).add(f["new_sha256"])
    return states


def make_manifest():
    dist = json.loads((full.DIST / "manifest.json").read_text(encoding="utf-8"))
    built = {f["relative"]: f for f in dist["files"]}
    known = known_states()
    files = []
    for order, t in enumerate(full.plan()):
        row = built[t["relative"]]
        rel = t["relative"].replace("\\", "/")
        src = full.DIST / t["relative"]
        orig = full.ORIGINALS / t["relative"]
        if sha256_file(src) != row["built_sha256"]:
            raise SystemExit(f"Сборка повреждена: {rel}")
        if sha256_file(orig) != t["original_sha256"]:
            raise SystemExit(f"Эталон оригинала повреждён: {rel}")
        files.append({"order": order, "relative": rel, "game": t["game"], "kind": t["kind"], "payload": f"f{order:03d}.zst" if DELTA else f"f{order:03d}.bin", "payload_kind": "delta" if DELTA else "full",
                      "original_sha256": t["original_sha256"], "new_sha256": row["built_sha256"],
                      "size_original": orig.stat().st_size, "size_new": src.stat().st_size,
                      "known_states": sorted(known.get(rel, set()) - {t["original_sha256"], row["built_sha256"]})})
    return {"payload_kind": "delta" if DELTA else "full", "version": core.VERSION, "steam_buildid": dist["steam_buildid"], "built_at": dist["built_at"], "files": files}


def stage(manifest):
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "data" / "payload").mkdir(parents=True)
    for e in manifest["files"]:
        src = full.DIST / e["relative"].replace("/", "\\")
        dst = STAGE / "data" / "payload" / e["payload"]
        if DELTA:
            import zstandard as zstd
            a = (full.ORIGINALS / e["relative"].replace("/", "\\")).read_bytes()
            b = src.read_bytes()
            params = zstd.ZstdCompressionParameters.from_level(19, window_log=27, enable_ldm=True)
            cd = zstd.ZstdCompressionDict(a, dict_type=zstd.DICT_TYPE_RAWCONTENT)
            patch = zstd.ZstdCompressor(compression_params=params, dict_data=cd).compress(b)
            check = zstd.ZstdDecompressor(dict_data=cd, max_window_size=1 << 27).decompress(patch, max_output_size=len(b) + 1)
            if check != b:
                raise SystemExit("Патч не воспроизводит файл: " + e["relative"])
            dst.write_bytes(patch)
            e["payload_sha256"] = hashlib.sha256(patch).hexdigest()
            e["payload_size"] = len(patch)
            print("патч", e["relative"].split("/")[-1][:14], len(patch) >> 10, "КБ из", len(b) >> 10, flush=True)
        else:
            shutil.copyfile(src, dst)
            if sha256_file(dst) != e["new_sha256"]:
                raise SystemExit("Ошибка копирования payload: " + e["relative"])
    (STAGE / "data" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def build_exe():
    if PYI.exists():
        shutil.rmtree(PYI)
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--noconfirm", "--clean", "--noupx",
           "--name", EXE_NAME, "--distpath", str(OUT), "--workpath", str(PYI), "--specpath", str(PYI),
           "--paths", str(ROOT), "--add-data", f"{STAGE / 'data'};data",
           "--exclude-module", "UnityPy", "--exclude-module", "numpy", "--exclude-module", "PIL", "--exclude-module", "cv2",
           "--exclude-module", "yaml", "--exclude-module", "fontTools",
           *(["--hidden-import", "zstandard"] if DELTA else []),
           str(ROOT / "tools" / "release" / "app.py")]
    subprocess.run(cmd, check=True)


def main():
    skip_verify = "--skip-verify" in sys.argv
    if not skip_verify:
        r = subprocess.run([sys.executable, "-m", "tools.install.full", "verify-build"], cwd=ROOT, capture_output=True, text=True,
                           env={**__import__("os").environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"})
        if r.returncode != 0:
            print(r.stdout[-2000:])
            raise SystemExit("verify-build не пройден: релиз не собирается.")
        print("verify-build: ok")
    manifest = make_manifest()
    print("файлов в комплекте:", len(manifest["files"]), "buildid", manifest["steam_buildid"])
    stage(manifest)
    if "--stage-only" in sys.argv:
        return
    build_exe()
    exe = OUT / (EXE_NAME + ".exe")
    sha = sha256_file(exe)
    (OUT / (EXE_NAME + ".exe.sha256")).write_text(f"{sha} *{exe.name}\n", encoding="utf-8")
    size = exe.stat().st_size
    readme = (ROOT / "release" / ("README_delta.md" if DELTA else "README.md")).read_text(encoding="utf-8").replace("{{SHA256}}", sha).replace("{{SIZE}}", str(size))
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    for name in (() if DELTA else ("CHANGELOG.md", "DESCRIPTION.md")):
        shutil.copyfile(ROOT / "release" / name, OUT / name)
    print(exe, size, sha)


if __name__ == "__main__":
    main()
