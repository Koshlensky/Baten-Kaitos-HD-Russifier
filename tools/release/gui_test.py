"""Проверка графического окна установщика на изолированной копии игры.

EXE запускается с `--gui-selftest`: окно создаётся по-настоящему, кнопки нажимаются программно, диалоги подменяются записью.
Внешний скрипт снимает окно функцией PrintWindow (работает, даже если окно закрыто другими) и проверяет итоговый отчёт.
Использование: python -m tools.release.gui_test <exe> [--shots папка]
"""
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.release import core, selftest  # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


def find_window(pid):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            buf = ctypes.create_unicode_buffer(300)
            user32.GetWindowTextW(hwnd, buf, 300)
            if "русификатор" in buf.value:
                found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def capture(hwnd, path):
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)
    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]
    bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return True


def main():
    exe = Path(sys.argv[1])
    shots = Path(sys.argv[sys.argv.index("--shots") + 1]) if "--shots" in sys.argv else ROOT / "work" / "build" / "gui_shots"
    shots.mkdir(parents=True, exist_ok=True)
    for old in shots.glob("*.png"):
        old.unlink()
    manifest = core.load_manifest(selftest.STAGE_DATA / "manifest.json")
    game = selftest.make_game("gui", manifest)
    snap = selftest.extras_snapshot(game)
    report = ROOT / "work" / "build" / "gui_report.json"
    if report.exists():
        report.unlink()
    proc = subprocess.Popen([str(exe), "--gui-selftest", str(game), str(report)])
    t0 = time.time()
    n = 0
    last = 0
    while proc.poll() is None and time.time() - t0 < 1500:
        hwnd = find_window(proc.pid) or find_window(0)
        if hwnd and time.time() - last >= 1.0:
            if capture(hwnd, shots / f"shot_{n:03d}.png"):
                n += 1
            last = time.time()
        time.sleep(0.2)
    if proc.poll() is None:
        proc.kill()
        print("FAIL окно не закрылось за отведённое время")
        return 1
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(("OK   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))

    check("окно открылось и сделаны снимки", n > 0, f"снимков: {n}")
    check("отчёт интерфейса создан", report.exists())
    if not report.exists():
        return 1
    rep = json.loads(report.read_text(encoding="utf-8"))
    texts = [w["text"] for w in rep["widgets"]]
    check("заголовок окна по-русски", "русификатор" in rep["title"] and "Baten Kaitos" in rep["title"], rep["title"])
    for needed in ("Найти автоматически", "Обзор…", "Проверить совместимость", "Установить / обновить русификатор", "Удалить русификатор (вернуть оригинал)"):
        check(f"кнопка «{needed}»", needed in texts)
    check("предупреждение про проверку целостности Steam", any("Проверка целостности файлов" in t and "Steam" in t for t in texts))
    check("пометка о неофициальной локализации", any("Неофициальная фанатская локализация" in t for t in texts))
    steps = {s["name"]: s for s in rep["steps"]}
    check("проверка: совместимо, все файлы оригинальны", steps["check"]["file_states"] == {"original": len(manifest["files"])} and "можно устанавливать" in steps["check"]["status"], str(steps["check"]["file_states"]))
    check("установка: все файлы русифицированы", steps["install"]["file_states"] == {"installed": len(manifest["files"])}, str(steps["install"]["file_states"]))
    msgs = " ".join(m[2] for m in steps["install"]["messages"])
    check("сообщения установки по-русски (подтверждение и результат)", "Установить русификатор?" in msgs and "Готово. Проверено файлов" in msgs, msgs[:200])
    check("после установки проверка показывает русификатор v2.0", steps["check_after_install"]["file_states"] == {"installed": len(manifest["files"])})
    msgs = " ".join(m[2] for m in steps["restore"]["messages"])
    check("удаление: оригинальные файлы восстановлены", steps["restore"]["file_states"] == {"original": len(manifest["files"])}, str(steps["restore"]["file_states"]))
    check("сообщения удаления по-русски", "Вернуть оригинальные файлы" in msgs and "Русификатор удалён" in msgs, msgs[:200])
    check("повторная установка после удаления", steps["reinstall"]["file_states"] == {"installed": len(manifest["files"])})
    check("сохранения и GameAssembly.dll не тронуты", selftest.extras_snapshot(game) == snap)
    # сверка с оригинальными файлами после удаления проводится отдельно (шаг restore: все 45 файлов — original по SHA-256)
    selftest.rmtree(selftest.MOCK / "gui")
    print("GUI-тест:", "пройден" if ok else "ЕСТЬ ОШИБКИ", f"; снимков окна: {n} → {shots}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
