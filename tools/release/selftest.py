"""Самотест установщика на изолированных копиях игры (макет в work/release_mock). Реальная игра не используется.

Макет собирается из оригиналов (backups/originals_v2) плюс «чужие» файлы (сохранения, GameAssembly.dll, appmanifest).
Проверяется ядро (tools/release/core.py) либо готовый EXE (--exe, режим без окна).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.release import core  # noqa: E402

STAGE_DATA = ROOT / "work" / ("release_stage_delta" if "--delta" in sys.argv else "release_stage") / "data"
ORIGINALS = ROOT / "backups" / "originals_v2"
OLDER = ROOT / "backups" / "full_20261020_230945"      # состояние перед установкой D-048 (прежняя тестовая сборка)
MOCK = ROOT / "work" / "release_mock"
EXTRA = {"Batenkaitos1/GameAssembly.dll": b"fake-dll-1", "Batenkaitos2/GameAssembly.dll": b"fake-dll-2",
         "Batenkaitos1/save/slot0.sav": b"save-1", "Batenkaitos2/save/slot0.sav": b"save-2", "readme.txt": b"hello"}


def rmtree(p):
    if p.exists():
        subprocess.run(["cmd", "/c", "attrib", "-R", str(p / "*"), "/S", "/D"], capture_output=True)
        shutil.rmtree(p, ignore_errors=True)


def make_game(name, manifest, buildid="20890367", older=False):
    base = MOCK / name
    rmtree(base)
    game = base / "steamapps" / "common" / core.GAME_DIR_NAME
    for e in manifest["files"]:
        src = ORIGINALS / Path(*e["relative"].split("/"))
        if older and (OLDER / Path(*e["relative"].split("/"))).exists():
            src = OLDER / Path(*e["relative"].split("/"))
        dst = game.joinpath(*e["relative"].split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o666)
    for rel, data in EXTRA.items():
        p = game.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (base / "steamapps" / f"appmanifest_{core.APP_ID}.acf").write_text('"AppState"\n{\n\t"buildid"\t\t"%s"\n}\n' % buildid, encoding="utf-8")
    return game


def extras_snapshot(game):
    return {rel: hashlib.sha256(game.joinpath(*rel.split("/")).read_bytes()).hexdigest() for rel in EXTRA}


def states(inst):
    return {e["relative"]: inst.classify(e)[0] for e in inst.files}


class Runner:
    """Единый интерфейс для ядра и EXE."""

    def __init__(self, exe=None):
        self.exe = exe
        self.manifest = core.load_manifest(STAGE_DATA / "manifest.json")

    def inst(self, game):
        return core.Installer(game, manifest=self.manifest, data=STAGE_DATA, log=lambda m: None)

    def run(self, action, game, fail_after=None):
        """Возвращает (код, текст). Для EXE — вызов процесса без окна."""
        if self.exe:
            args = [str(self.exe), "--action", action, "--game", str(game)]
            if fail_after:
                args += ["--fail-after", str(fail_after)]
            log = MOCK / "exe_log.txt"
            if log.exists():
                log.unlink()
            args += ["--log", str(log)]
            r = subprocess.run(args, capture_output=True, text=True, timeout=1800)
            return r.returncode, (log.read_text(encoding="utf-8") if log.exists() else "")
        inst = self.inst(game)
        try:
            if action == "install":
                inst.install(fail_after=fail_after)
            elif action == "restore":
                inst.restore(fail_after=fail_after)
            elif action == "check":
                rep = inst.inspect()
                return (0 if rep["ok"] else 1), "\n".join(rep["problems"])
            elif action == "verify":
                return (0 if not inst.verify() else 1), ""
            return 0, ""
        except core.InstallError as err:
            return 1, str(err)


def main():
    exe = None
    if "--exe" in sys.argv:
        exe = Path(sys.argv[sys.argv.index("--exe") + 1])
    keep = "--keep" in sys.argv
    r = Runner(exe)
    m = r.manifest
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(("OK   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)

    originals_ok = lambda inst: all(s == "original" for s in states(inst).values())
    installed_ok = lambda inst: all(s == "installed" for s in states(inst).values())

    # 1. установка поверх оригинала, откат, посторонние файлы
    game = make_game("t1", m)
    snap = extras_snapshot(game)
    inst = r.inst(game)
    check("1.1 проверка совместимости на оригинале", r.run("check", game)[0] == 0)
    code, text = r.run("install", game)
    check("1.2 установка поверх оригинала", code == 0, text[-300:])
    check("1.3 все файлы совпадают с эталоном русификатора", installed_ok(inst) and r.run("verify", game)[0] == 0)
    check("1.4 сохранения, GameAssembly.dll и посторонние файлы не тронуты", extras_snapshot(game) == snap)
    orig_dir = game / core.WORK_DIR / "originals"
    check("1.5 оригиналы сохранены в резервной папке", all((orig_dir / Path(*e["relative"].split("/"))).exists() for e in m["files"]))
    check("1.6 нет временных файлов", not list(game.rglob("*" + core.TMP_SUFFIX)))
    code, text = r.run("install", game)
    check("1.7 повторная установка безопасна", code == 0 and installed_ok(inst))
    code, text = r.run("restore", game)
    check("1.8 откат после установки возвращает оригиналы", code == 0 and originals_ok(inst), text[-300:])
    check("1.9 резервные копии после отката на месте", orig_dir.exists())
    check("1.10 сохранения не тронуты после отката", extras_snapshot(game) == snap)
    code, text = r.run("install", game)
    check("1.11 установка после отката", code == 0 and installed_ok(inst))
    if not keep:
        rmtree(MOCK / "t1")

    # 2. обновление с прежней тестовой сборки (нужны backups/full_* автора; у форка их нет — сценарий пропускается)
    if not OLDER.exists():
        print("SKIP 2.x обновление с прежней сборкой: нет backups/full_20261020_230945")
    else:
        game = make_game("t2", m, older=True)
        inst = r.inst(game)
        st = states(inst)
        check("2.1 состояние распознано как прежняя сборка/оригинал", set(st.values()) <= {"older", "original", "installed"} and "older" in st.values(), str(set(st.values())))
        if "--delta" in sys.argv:
            code, text = r.run("install", game)
            check("2.2a патчи: без сохранённых оригиналов обновление прежней сборки отклоняется понятным сообщением", code != 0 and "оригинал" in text and not installed_ok(inst), text[-200:])
            for e in m["files"]:
                dst = game / core.WORK_DIR / "originals" / Path(*e["relative"].split("/"))
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ORIGINALS / Path(*e["relative"].split("/")), dst)
        code, text = r.run("install", game)
        check("2.2 обновление с тестовой v2.0", code == 0 and installed_ok(inst), text[-300:])
        check("2.3 прежнее состояние сохранено", any((game / core.WORK_DIR / "backups").glob("*/*")))
        if not keep:
            rmtree(MOCK / "t2")

    # 3. прерванная установка
    game = make_game("t3", m)
    inst = r.inst(game)
    code, text = r.run("install", game, fail_after=6)
    check("3.1 прерывание установки сообщает об ошибке", code != 0)
    st = states(inst)
    check("3.2 после прерывания файлы только оригинал/установленный", set(st.values()) <= {"original", "installed"} and "installed" in st.values())
    cat_states = [s for rel, s in st.items() if rel.endswith("catalog.json")]
    check("3.3 каталог записан первым (нет несовпадения CRC)", all(s == "installed" for s in cat_states))
    (game / "Batenkaitos1" / "stray.bundle.rus_tmp").write_bytes(b"xx")
    check("3.4 проверка видит прерванную установку", any("прервана" in w for w in inst.inspect()["warnings"]))
    code, text = r.run("restore", game)
    check("3.5 откат после прерванной установки", code == 0 and originals_ok(inst), text[-300:])
    code, text = r.run("install", game, fail_after=6)
    code, text = r.run("install", game)
    check("3.6 установка продолжается после прерывания", code == 0 and installed_ok(inst), text[-300:])
    check("3.7 временный файл убран", not list(game.rglob("*" + core.TMP_SUFFIX)))
    code, text = r.run("restore", game, fail_after=5)
    check("3.8 откат прерван", code != 0)
    code, text = r.run("restore", game)
    check("3.9 повторный откат завершает восстановление", code == 0 and originals_ok(inst), text[-300:])
    if not keep:
        rmtree(MOCK / "t3")

    # 4. отказы
    game = make_game("t4", m, buildid="11111111")
    code, text = r.run("install", game)
    check("4.1 отказ при неизвестной версии игры", code != 0 and originals_ok(r.inst(game)), text[-200:])
    rmtree(MOCK / "t4")
    game = make_game("t5", m)
    victim = game.joinpath(*m["files"][12]["relative"].split("/"))
    data = bytearray(victim.read_bytes())
    data[5000] ^= 0xFF
    victim.write_bytes(bytes(data))
    code, text = r.run("install", game)
    inst = r.inst(game)
    check("4.2 отказ при изменённом файле", code != 0 and states(inst)[m["files"][12]["relative"]] == "unknown", text[-200:])
    check("4.3 при отказе ничего не записано", sum(1 for s in states(inst).values() if s == "installed") == 0)
    rmtree(MOCK / "t5")
    game = make_game("t6", m)
    game.joinpath(*m["files"][3]["relative"].split("/")).unlink()
    code, text = r.run("install", game)
    check("4.4 отказ при отсутствующем файле", code != 0, text[-200:])
    rmtree(MOCK / "t6")
    game = make_game("t7", m)
    code, text = r.run("restore", game)
    check("4.5 откат на оригинале ничего не делает", code == 0)
    rmtree(MOCK / "t7")

    # 5. установка, затем порча стороной → откат (оригиналы есть)
    game = make_game("t8", m)
    inst = r.inst(game)
    r.run("install", game)
    victim = game.joinpath(*m["files"][7]["relative"].split("/"))
    victim.write_bytes(b"corrupt")
    code, text = r.run("restore", game)
    check("5.1 откат работает и при посторонней порче файла", code == 0 and originals_ok(inst), text[-200:])
    rmtree(MOCK / "t8")

    failed = [n for n, ok in results if not ok]
    print(f"\nсамотест релизного установщика: {len(results) - len(failed)}/{len(results)}")
    out = ROOT / "work" / "build" / ("release_selftest_exe.json" if exe else "release_selftest.json")
    out.write_text(json.dumps({"results": results, "exe": str(exe) if exe else None}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
