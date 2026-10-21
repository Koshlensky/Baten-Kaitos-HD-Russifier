"""Самотест установщика тестовой сборки на МАКЕТЕ папки игры (настоящая игра не затрагивается).

Макет создаётся в work/mock_game: 10 файлов плана в состоянии «оригинал» + фиктивный GameAssembly.dll
и appmanifest. Проверяются: отказ без разрешения, отказ при неизвестном файле и чужой версии игры,
установка, проверка, посторонние файлы без изменений, откат к оригиналу, повторная установка поверх.

    python -m tools.install.selftest_full
"""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "work" / "mock_game"
GAME = MOCK / "steamapps" / "common" / "BatenKaitos HD Remaster"
os.environ["BK_GAME_ROOT"] = str(GAME)

from tools.install import full, pilot  # noqa: E402  (после установки BK_GAME_ROOT)
from tools.bkloc import paths  # noqa: E402


def rmtree(path):
    import subprocess
    subprocess.run(["attrib", "-R", str(Path(path) / "*"), "/S", "/D"], capture_output=True)
    shutil.rmtree(path, ignore_errors=True)


def make_mock():
    if MOCK.exists():
        rmtree(MOCK)
    for t in full.plan():
        dst = GAME / t["relative"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(full.ORIGINALS / t["relative"], dst)
    for game in paths.GAMES.values():
        gd = GAME / game["folder"]
        gd.mkdir(parents=True, exist_ok=True)
        (gd / "GameAssembly.dll").write_bytes(b"fake-dll-" + game["folder"].encode())
        (gd / "Batenkaitos_Data" / "save").mkdir(parents=True, exist_ok=True)
        (gd / "Batenkaitos_Data" / "save" / "slot1.dat").write_bytes(b"save-data")
    (MOCK / "steamapps" / "appmanifest_2146170.acf").write_text('"AppState"\n{\n\t"buildid"\t\t"20890367"\n}\n')


def expect_exit(func, *args):
    try:
        func(*args)
    except SystemExit as error:
        return str(error)
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    assert paths.GAME_ROOT == GAME, "BK_GAME_ROOT не применился"
    make_mock()
    full.BACKUPS = MOCK / "backups"
    full.LOGS = MOCK / "logs"
    full.LOGS.mkdir(parents=True, exist_ok=True)
    pilot.running_games = lambda: []
    results = []

    def check(name, ok):
        results.append((name, ok))
        print(("OK   " if ok else "FAIL ") + name)

    check("все 10 файлов макета — оригинал", all(full.classify(t, None, {}) == "оригинал" for t in full.plan()))
    real_authorization = full.authorization

    def no_permission():
        raise SystemExit("Нет разрешения installs.full_test (имитация для самотеста)")

    full.authorization = no_permission
    check("установка без разрешения отклоняется", "Нет разрешения" in (expect_exit(full.cmd_install, None) or ""))
    full.authorization = lambda: {"decision": "SELFTEST"}
    manifest = full.dist_manifest()
    check("предустановочные проверки на оригинале пройдены", full.preflight(manifest) == [])

    # неизвестный файл
    victim = GAME / full.plan()[0]["relative"]
    keep = victim.read_bytes()
    victim.write_bytes(keep + b"x")
    check("неизвестное состояние файла отклоняется", any("неизвестном состоянии" in p for p in full.preflight(manifest)))
    victim.write_bytes(keep)
    # другая версия игры
    acf = MOCK / "steamapps" / "appmanifest_2146170.acf"
    acf.write_text('"AppState"\n{\n\t"buildid"\t\t"1"\n}\n')
    check("чужая версия игры отклоняется", any("версия игры" in p for p in full.preflight(manifest)))
    acf.write_text('"AppState"\n{\n\t"buildid"\t\t"20890367"\n}\n')

    snap = {str(p.relative_to(GAME)): p.read_bytes() for p in GAME.rglob("*") if p.is_file() and p.name in ("GameAssembly.dll", "slot1.dat")}
    rc = full.cmd_install(None)
    check("установка прошла без ошибок", rc == 0)
    check("файлы совпадают со сборкой", full.cmd_verify(None) == 0)
    check("состояние после установки — v2.0", all(full.classify(t, next(f["built_sha256"] for f in manifest["files"] if f["relative"] == t["relative"]), {}) == "v2.0" for t in full.plan()))
    check("сохранения и GameAssembly.dll не тронуты", all((GAME / k).read_bytes() == v for k, v in snap.items()))
    check("повторная установка поверх v2.0 допускается", full.preflight(manifest) == [])
    rc = full.cmd_restore(None)
    check("откат вернул оригиналы", rc == 0 and all(full.classify(t, None, {}) == "оригинал" for t in full.plan()))
    check("сохранения и GameAssembly.dll не тронуты после отката", all((GAME / k).read_bytes() == v for k, v in snap.items()))
    rmtree(MOCK)
    failed = [n for n, ok in results if not ok]
    print(f"самотест установщика: {len(results) - len(failed)}/{len(results)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
