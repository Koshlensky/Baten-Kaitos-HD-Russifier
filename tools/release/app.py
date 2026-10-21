"""Русскоязычный интерфейс установщика (tkinter) и режим командной строки для проверок."""
import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.release import core
else:
    from . import core

TITLE = "Baten Kaitos I & II HD Remaster — русификатор v2.0"
DISCLAIMER = ("Неофициальная фанатская локализация, не связанная с Bandai Namco Entertainment. "
              "Для установки нужна лицензионная Steam-версия игры.")
STEAM_WARNING = ("Внимание: «Проверка целостности файлов» в Steam может восстановить оригинальные игровые ресурсы и удалить русификатор. "
                 "После неё (и после обновления игры) запустите установщик снова.")


def attach_console():
    try:
        if ctypes.windll.kernel32.AttachConsole(-1):
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = sys.stdout
    except Exception:
        pass


def format_report(rep, manifest):
    lines = [f"Версия игры (Steam buildid): {rep.get('buildid') or 'не определена'}; нужна {manifest['steam_buildid']}"]
    counts = rep.get("counts", {})
    if counts:
        lines.append("Файлов: " + ", ".join(f"{core.STATE_TEXT.get(k, k)} — {v}" for k, v in sorted(counts.items())))
    for w in rep.get("warnings", []):
        lines.append("Предупреждение: " + w)
    for p in rep.get("problems", []):
        lines.append("ОШИБКА: " + p)
    lines.append("Совместимость: " + ("можно устанавливать" if rep["ok"] else "установка невозможна"))
    return "\n".join(lines)


# ---------------------------------------------------------------- командная строка

def run_cli(args):
    manifest = core.load_manifest()
    logfile = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(msg):
        print(msg, flush=True)
        if logfile:
            logfile.write(msg + "\n")
            logfile.flush()

    game = args.game
    if not game:
        found = core.find_games()
        if not found:
            log("Игра не найдена автоматически. Укажите папку: --game \"...\\steamapps\\common\\BatenKaitos HD Remaster\"")
            return 2
        game = str(found[0])
    log(f"Игра: {game}")
    inst = core.Installer(game, manifest=manifest, log=log)
    try:
        if args.action == "check":
            rep = inst.inspect()
            log(format_report(rep, manifest))
            return 0 if rep["ok"] else 1
        if args.action == "install":
            res = inst.install(fail_after=args.fail_after)
            log(json.dumps(res, ensure_ascii=False))
            return 0
        if args.action == "restore":
            res = inst.restore(fail_after=args.fail_after)
            log(json.dumps(res, ensure_ascii=False))
            return 0
        if args.action == "verify":
            bad = inst.verify()
            log("Все файлы совпадают с русификатором" if not bad else "Расхождения: " + ", ".join(bad))
            return 0 if not bad else 1
    except core.InstallError as err:
        log("ОШИБКА: " + str(err))
        return 1
    return 0


# ---------------------------------------------------------------- окно

def run_gui(test=None):
    """test = (папка игры, файл отчёта) — автоматический прогон интерфейса (кнопки вызываются программно)."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    recorded = []
    if test:
        class _Shim:
            @staticmethod
            def showinfo(title, text):
                recorded.append(("info", title, text))

            @staticmethod
            def showerror(title, text):
                recorded.append(("error", title, text))

            @staticmethod
            def askyesno(title, text):
                recorded.append(("ask", title, text))
                return True
        messagebox = _Shim
    manifest = core.load_manifest()
    root = tk.Tk()
    root.title(TITLE)
    root.geometry("820x640")
    root.minsize(760, 560)
    msgs = queue.Queue()
    busy = {"v": False}

    ttk.Label(root, text="Baten Kaitos I & II HD Remaster", font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=14, pady=(12, 0))
    ttk.Label(root, text=f"Русификатор v{core.VERSION} (Steam, buildid {manifest['steam_buildid']})", font=("Segoe UI", 10)).pack(anchor="w", padx=14)
    ttk.Label(root, text=DISCLAIMER, wraplength=780, foreground="#555").pack(anchor="w", padx=14, pady=(2, 8))

    frame = ttk.Frame(root)
    frame.pack(fill="x", padx=14)
    ttk.Label(frame, text="Папка игры («...\\steamapps\\common\\BatenKaitos HD Remaster»):").grid(row=0, column=0, columnspan=3, sticky="w")
    path_var = tk.StringVar()
    ttk.Entry(frame, textvariable=path_var).grid(row=1, column=0, sticky="ew", pady=4)
    frame.columnconfigure(0, weight=1)
    btn_find = ttk.Button(frame, text="Найти автоматически")
    btn_find.grid(row=1, column=1, padx=6)
    btn_browse = ttk.Button(frame, text="Обзор…")
    btn_browse.grid(row=1, column=2)

    status = tk.Text(root, height=6, wrap="word", relief="solid", borderwidth=1, font=("Segoe UI", 9))
    status.pack(fill="x", padx=14, pady=(8, 4))
    status.configure(state="disabled")

    buttons = ttk.Frame(root)
    buttons.pack(fill="x", padx=14, pady=4)
    btn_check = ttk.Button(buttons, text="Проверить совместимость")
    btn_install = ttk.Button(buttons, text="Установить / обновить русификатор")
    btn_restore = ttk.Button(buttons, text="Удалить русификатор (вернуть оригинал)")
    btn_check.pack(side="left")
    btn_install.pack(side="left", padx=8)
    btn_restore.pack(side="left")

    progress = ttk.Progressbar(root, mode="determinate")
    progress.pack(fill="x", padx=14, pady=(6, 0))
    prog_label = ttk.Label(root, text="")
    prog_label.pack(anchor="w", padx=14)
    log_box = tk.Text(root, height=12, wrap="word", font=("Consolas", 9), relief="solid", borderwidth=1)
    log_box.pack(fill="both", expand=True, padx=14, pady=6)
    log_box.configure(state="disabled")
    ttk.Label(root, text=STEAM_WARNING, wraplength=780, foreground="#8a4b00").pack(anchor="w", padx=14, pady=(0, 10))

    def set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def append_log(text):
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def set_busy(flag):
        busy["v"] = flag
        state = "disabled" if flag else "normal"
        for b in (btn_find, btn_browse, btn_check, btn_install, btn_restore):
            b.configure(state=state)

    def pump():
        try:
            while True:
                kind, payload = msgs.get_nowait()
                if kind == "log":
                    append_log(payload)
                elif kind == "progress":
                    done, total, text = payload
                    progress.configure(maximum=max(total, 1), value=done)
                    prog_label.configure(text=text)
                elif kind == "status":
                    set_text(status, payload)
                elif kind == "done":
                    set_busy(False)
                    ok, text = payload
                    prog_label.configure(text="")
                    (messagebox.showinfo if ok else messagebox.showerror)("Русификатор", text)
        except queue.Empty:
            pass
        root.after(100, pump)

    def installer():
        p = path_var.get().strip().strip('"')
        if not p:
            raise core.InstallError("Укажите папку игры или нажмите «Найти автоматически».")
        return core.Installer(p, manifest=manifest, log=lambda m: msgs.put(("log", m)),
                              progress=lambda d, t, x: msgs.put(("progress", (d, t, x))))

    def find():
        games = core.find_games()
        if not games:
            set_text(status, "Игра не найдена автоматически. Нажмите «Обзор…» и выберите папку «BatenKaitos HD Remaster».")
            return
        path_var.set(str(games[0]))
        set_text(status, f"Найдена игра: {games[0]}" + (f"\n(найдено вариантов: {len(games)}, выбран первый)" if len(games) > 1 else ""))
        check()

    def browse():
        d = filedialog.askdirectory(title="Выберите папку BatenKaitos HD Remaster")
        if d:
            path_var.set(os.path.normpath(d))
            check()

    def worker(fn):
        def run():
            try:
                ok, text = fn()
            except core.InstallError as err:
                ok, text = False, str(err)
            except Exception:
                ok, text = False, "Непредвиденная ошибка:\n" + traceback.format_exc(limit=3)
            msgs.put(("done", (ok, text)))
        set_busy(True)
        threading.Thread(target=run, daemon=True).start()

    def check():
        def job():
            inst = installer()
            rep = inst.inspect()
            msgs.put(("status", format_report(rep, manifest)))
            return rep["ok"], ("Проверка пройдена: русификатор можно устанавливать." if rep["ok"] else "Есть проблемы, подробности в окне.")
        worker(job)

    def install():
        if not messagebox.askyesno("Установка", "Установить русификатор?\n\nОригинальные файлы будут сохранены в папке _rus_v2 внутри папки игры.\n"
                                   "Игра и Steam-обновления на время установки должны быть закрыты."):
            return

        def job():
            inst = installer()
            res = inst.install()
            rep = inst.inspect()
            msgs.put(("status", format_report(rep, manifest) if not rep["ok"] else "Русификатор v2.0 установлен, все файлы проверены."))
            return True, (f"Готово. Проверено файлов: {res['checked']}, записано: {res['installed']}.\n"
                          f"Оригиналы сохранены: {res['originals']}" + (f"\nПрежнее состояние: {res['backup']}" if res['backup'] else ""))
        worker(job)

    def restore():
        if not messagebox.askyesno("Удаление", "Вернуть оригинальные файлы игры и удалить русификатор?\nРезервные копии не удаляются."):
            return

        def job():
            inst = installer()
            res = inst.restore()
            msgs.put(("status", "Оригинальные файлы восстановлены."))
            return True, f"Восстановлено файлов: {res['restored']}. Русификатор удалён, резервные копии сохранены."
        worker(job)

    btn_find.configure(command=find)
    btn_browse.configure(command=browse)
    btn_check.configure(command=check)
    btn_install.configure(command=install)
    btn_restore.configure(command=restore)
    root.after(200, pump)
    if not test:
        root.after(400, find)
    else:
        start_autotest(root, test, path_var, status, log_box, btn_check, btn_install, btn_restore, busy, manifest, recorded)
    root.mainloop()
    return 0


def start_autotest(root, test, path_var, status, log_box, btn_check, btn_install, btn_restore, busy, manifest, recorded):
    game, report_path = test
    report = {"title": root.title(), "steps": []}

    def widgets(w, acc):
        for c in w.winfo_children():
            cls = c.winfo_class()
            if cls in ("TLabel", "TButton"):
                acc.append({"class": cls, "text": c.cget("text"), "state": str(c.cget("state")) if cls == "TButton" else ""})
            widgets(c, acc)
        return acc

    def snap(name):
        inst = core.Installer(game, manifest=manifest, log=lambda m: None)
        counts = {}
        for e in inst.files:
            s = inst.classify(e)[0]
            counts[s] = counts.get(s, 0) + 1
        report["steps"].append({"name": name, "status": status.get("1.0", "end").strip(), "log_tail": log_box.get("1.0", "end").strip().splitlines()[-4:],
                                "messages": list(recorded), "file_states": counts})
        recorded.clear()

    plan = [("initial", None), ("check", btn_check), ("install", btn_install), ("check_after_install", btn_check),
            ("restore", btn_restore), ("reinstall", btn_install)]
    state = {"i": 0, "waiting": False}

    def step():
        if busy["v"]:
            root.after(300, step)
            return
        if state["i"] > 0:
            name, _ = plan[state["i"] - 1]
            snap(name)
            root.after(2500, nxt)      # пауза: внешний скрипт успевает снять окно
            return
        nxt()

    def nxt():
        if state["i"] == 0:
            report["widgets"] = widgets(root, [])
            path_var.set(game)
            snap_initial = True
        if state["i"] >= len(plan):
            Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            root.destroy()
            return
        name, btn = plan[state["i"]]
        state["i"] += 1
        if btn is None:
            root.after(2500, step_after_initial)
            return
        btn.invoke()
        root.after(600, step)

    def step_after_initial():
        report["initial_path"] = path_var.get()
        nxt()

    root.after(1500, nxt)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Установщик русификатора Baten Kaitos I & II HD Remaster v2.0")
    parser.add_argument("--action", choices=["check", "install", "restore", "verify"], help="работа без окна")
    parser.add_argument("--game", help="папка игры")
    parser.add_argument("--log", help="файл журнала")
    parser.add_argument("--fail-after", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--gui-selftest", nargs=2, metavar=("ИГРА", "ОТЧЁТ"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.action:
        attach_console()
        return run_cli(args)
    return run_gui(tuple(args.gui_selftest) if args.gui_selftest else None)


if __name__ == "__main__":
    raise SystemExit(main())
