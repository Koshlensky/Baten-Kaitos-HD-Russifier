"""Ядро установщика русификатора Baten Kaitos I & II HD Remaster v2.0 (только стандартная библиотека Python).

Работает и из исходников, и внутри автономного EXE. Данные (манифест и файлы) лежат в папке data/ рядом с модулем
или в каталоге распаковки PyInstaller. Установка устроена так, чтобы в любой момент оставаться восстановимой:

* перед заменой оригинальные файлы один раз сохраняются в `_rus_v2/originals` (с проверкой хеша), а предыдущее состояние
  (например, прежняя тестовая сборка) — в `_rus_v2/backups/<время>`;
* каждый файл пишется во временный файл, проверяется по SHA-256 и подменяется атомарно;
* каталог Addressables записывается первым (в нём обнулены CRC изменённых бандлов), при откате — последним,
  поэтому прерванная операция не оставляет игру с несовпадающими контрольными суммами;
* неизвестное состояние файла (другая версия игры, другой русификатор, изменение сторонней программой) — отказ с объяснением.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

VERSION = "2.0"
APP_ID = "2146170"
GAME_DIR_NAME = "BatenKaitos HD Remaster"
WORK_DIR = "_rus_v2"
TMP_SUFFIX = ".rus_tmp"
CHUNK = 1024 * 1024


class InstallError(Exception):
    """Ошибка с понятным пользователю текстом."""


def data_dir():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "data"
    env = os.environ.get("BK_RUS_DATA")
    return Path(env) if env else Path(__file__).resolve().parent / "data"


def load_manifest(path=None):
    path = Path(path) if path else data_dir() / "manifest.json"
    if not path.exists():
        raise InstallError("В установщике нет данных (manifest.json). Скачайте установщик заново.")
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def human(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024.0


# ---------------------------------------------------------------- поиск игры

def _steam_roots():
    roots = []
    try:
        import winreg
        for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]) / "Steam")
    return roots


def _library_dirs(root):
    dirs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                dirs.append(Path(m.group(1).replace("\\\\", "\\")))
        except OSError:
            pass
    return dirs


def find_games():
    """Список папок «BatenKaitos HD Remaster», найденных автоматически."""
    found, seen = [], set()
    candidates = []
    for root in _steam_roots():
        candidates += _library_dirs(root)
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        for name in ("SteamLibrary", "Steam", "Games\\Steam", "Games\\SteamLibrary"):
            candidates.append(Path(f"{letter}:\\") / name)
    for lib in candidates:
        game = lib / "steamapps" / "common" / GAME_DIR_NAME
        key = str(game).lower()
        if key not in seen and game.is_dir():
            seen.add(key)
            found.append(game)
    return found


def read_buildid(game_root):
    acf = Path(game_root).parent.parent / f"appmanifest_{APP_ID}.acf"
    if not acf.exists():
        return None
    m = re.search(r'"buildid"\s+"(\d+)"', acf.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else None


def running_game_processes(game_root):
    """Имена запущенных процессов, совпадающих с .exe из папки игры."""
    names = set()
    root = Path(game_root)
    try:
        for p in list(root.glob("*.exe")) + list(root.glob("*/*.exe")):
            names.add(p.name.lower())
    except OSError:
        return []
    if not names:
        return []
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    running = {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line}
    return sorted(names & running)


# ---------------------------------------------------------------- установщик

class Installer:
    def __init__(self, game_root, manifest=None, data=None, log=print, progress=None):
        self.root = Path(game_root)
        self.manifest = manifest or load_manifest()
        self.data = Path(data) if data else data_dir()
        self.log = log
        self.progress = progress or (lambda done, total, text: None)
        self.work = self.root / WORK_DIR
        self.files = sorted(self.manifest["files"], key=lambda f: f["order"])

    # --- пути
    def game_path(self, entry):
        return self.root.joinpath(*entry["relative"].split("/"))

    def payload_path(self, entry):
        return self.data / "payload" / entry["payload"]

    def original_path(self, entry):
        return self.work / "originals" / Path(*entry["relative"].split("/"))

    # --- состояние
    def classify(self, entry):
        path = self.game_path(entry)
        if not path.exists():
            return "missing", None
        sha = sha256_file(path)
        if sha == entry["original_sha256"]:
            return "original", sha
        if sha == entry["new_sha256"]:
            return "installed", sha
        if sha in entry.get("known_states", ()):
            return "older", sha
        return "unknown", sha

    def inspect(self):
        """Полная проверка: версия, процессы, состояние всех файлов, место на диске."""
        report = {"ok": True, "problems": [], "warnings": [], "states": {}, "buildid": None}
        if not self.root.is_dir():
            report["problems"].append("Папка не существует.")
            report["ok"] = False
            return report
        for sub in ("Batenkaitos1", "Batenkaitos2"):
            if not (self.root / sub).is_dir():
                report["problems"].append(f"В выбранной папке нет «{sub}»: это не папка Baten Kaitos I & II HD Remaster.")
        if report["problems"]:
            report["ok"] = False
            return report
        build = read_buildid(self.root)
        report["buildid"] = build
        want = self.manifest["steam_buildid"]
        if build is None:
            report["warnings"].append("Не найден файл appmanifest Steam: версия игры будет проверена только по контрольным суммам файлов.")
        elif build != want:
            report["problems"].append(f"Версия игры в Steam (buildid {build}) не совпадает с версией, для которой сделан русификатор (buildid {want}). "
                                      "Обновите русификатор или не устанавливайте эту версию: установка остановлена.")
        running = running_game_processes(self.root)
        if running:
            report["problems"].append("Игра запущена (" + ", ".join(running) + "). Закройте её и повторите.")
        counts = {}
        for i, e in enumerate(self.files):
            self.progress(i, len(self.files), "Проверка файлов: " + Path(e["relative"]).name)
            state, _ = self.classify(e)
            report["states"][e["relative"]] = state
            counts[state] = counts.get(state, 0) + 1
            if state == "missing":
                report["problems"].append(f"Нет файла игры: {e['relative']}. Проверьте целостность файлов игры в Steam.")
            elif state == "unknown":
                report["problems"].append(f"Файл изменён или принадлежит другой версии игры/другому русификатору: {e['relative']}. "
                                          "Чтобы вернуть оригинал, используйте в Steam «Проверку целостности файлов» (или кнопку удаления, если русификатор ставился этим установщиком), затем повторите.")
        if self.manifest.get("payload_kind") == "delta":
            for e in self.files:
                if report["states"].get(e["relative"]) == "older":
                    op = self.original_path(e)
                    if not op.exists() or sha256_file(op) != e["original_sha256"]:
                        report["problems"].append(f"Для обновления прежней сборки нужна сохранённая копия оригинала ({e['relative']}), её нет. "
                                                  "Верните оригиналы через Steam («Проверка целостности файлов») и повторите установку.")
                        break
        report["counts"] = counts
        if (self.work / "journal.json").exists():
            report["warnings"].append("Прошлая установка была прервана. Повторная установка безопасна и продолжит работу.")
        need = sum(e["size_new"] for e in self.files) + sum(e["size_original"] for e in self.files)
        try:
            free = shutil.disk_usage(self.root).free
            if free < need * 1.05:
                report["problems"].append(f"Мало места на диске: нужно около {human(need)}, свободно {human(free)}.")
        except OSError:
            pass
        report["ok"] = not report["problems"]
        return report

    # --- запись
    @staticmethod
    def _chunks_of_file(src):
        with open(src, "rb") as fi:
            while True:
                chunk = fi.read(CHUNK)
                if not chunk:
                    return
                yield chunk

    @staticmethod
    def _chunks_of_delta(patch, base, original_sha):
        """Применение бинарного патча (zstd, база — оригинальный файл) потоком; хеш базы проверяется заранее."""
        try:
            import zstandard as zstd
        except ImportError as err:
            raise InstallError("В этой сборке установщика нет модуля применения патчей.") from err
        base_bytes = Path(base).read_bytes()
        if hashlib.sha256(base_bytes).hexdigest() != original_sha:
            raise InstallError(f"Исходный файл для патча не совпадает с оригиналом: {Path(base).name}.")
        cd = zstd.ZstdCompressionDict(base_bytes, dict_type=zstd.DICT_TYPE_RAWCONTENT)
        dctx = zstd.ZstdDecompressor(dict_data=cd, max_window_size=1 << 27)
        with open(patch, "rb") as fi:
            reader = dctx.stream_reader(fi)
            while True:
                chunk = reader.read(CHUNK)
                if not chunk:
                    return
                yield chunk

    @staticmethod
    def _write_atomic(dst, expect_sha, chunks):
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + TMP_SUFFIX)
        h = hashlib.sha256()
        if dst.exists() and not os.access(dst, os.W_OK):
            try:
                os.chmod(dst, 0o666)      # файл только для чтения — снимаем атрибут, иначе замена не удастся
            except OSError:
                pass
        try:
            with open(tmp, "wb") as fo:
                for chunk in chunks:
                    h.update(chunk)
                    fo.write(chunk)
                fo.flush()
                os.fsync(fo.fileno())
            if h.hexdigest() != expect_sha:
                raise InstallError(f"Контрольная сумма не совпала при записи {dst.name}: исходные данные повреждены.")
            os.replace(tmp, dst)
        except PermissionError as err:
            raise InstallError(f"Нет доступа к файлу {dst.name}. Закройте игру и Steam-обновления или запустите установщик от имени администратора.") from err
        except OSError as err:
            raise InstallError(f"Ошибка записи {dst.name}: {err}") from err
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    @classmethod
    def _copy_verified(cls, src, dst, expect_sha):
        cls._write_atomic(dst, expect_sha, cls._chunks_of_file(src))
    def cleanup_temp(self):
        for p in self.root.rglob("*" + TMP_SUFFIX):
            try:
                p.unlink()
                self.log(f"Удалён временный файл прерванной установки: {p.name}")
            except OSError:
                pass

    def _journal(self, stage, **extra):
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / "journal.json").write_text(json.dumps({"stage": stage, "time": datetime.datetime.now().isoformat(timespec="seconds"), **extra},
                                                         ensure_ascii=False), encoding="utf-8")

    def _history(self, action, files):
        self.work.mkdir(parents=True, exist_ok=True)
        path = self.work / "history.json"
        hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        hist.append({"action": action, "version": VERSION, "time": datetime.datetime.now().isoformat(timespec="seconds"), "files": files})
        path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")

    def install(self, assume_checked=False, fail_after=None):
        """Установка или обновление. fail_after — только для тестов: прервать после N записанных файлов."""
        report = self.inspect()
        if not report["ok"]:
            raise InstallError("Установка невозможна:\n- " + "\n- ".join(report["problems"]))
        self.cleanup_temp()
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.work / "backups" / stamp
        self._journal("installing", stamp=stamp)
        order = sorted(self.files, key=lambda f: (0 if f["kind"] == "catalog" else 1, f["order"]))   # каталог первым
        done, changed = 0, []
        total = len(order)
        for i, e in enumerate(order):
            state = report["states"][e["relative"]]
            name = Path(e["relative"]).name
            self.progress(i, total, "Установка: " + name)
            if state == "installed":
                self.log(f"Уже установлен: {e['relative']}")
                continue
            src = self.payload_path(e)
            if not src.exists():
                raise InstallError(f"В установщике нет файла {e['payload']}: установщик повреждён, скачайте его заново.")
            gp = self.game_path(e)
            op = self.original_path(e)
            if state == "original":
                if not op.exists():
                    self._copy_verified(gp, op, e["original_sha256"])
                    self.log(f"Сохранён оригинал: {e['relative']}")
            else:  # прежняя сборка: сохраняем её отдельно
                self._copy_verified(gp, backup_dir / Path(*e["relative"].split("/")), report_sha(gp))
                self.log(f"Сохранено прежнее состояние: {e['relative']}")
            if e.get("payload_kind", "full") == "delta":
                base = gp if state == "original" else op
                if sha256_file(src) != e["payload_sha256"]:
                    raise InstallError(f"Патч {e['payload']} повреждён: установщик нужно скачать заново.")
                self._write_atomic(gp, e["new_sha256"], self._chunks_of_delta(src, base, e["original_sha256"]))
            else:
                self._copy_verified(src, gp, e["new_sha256"])
            done += 1
            changed.append({"relative": e["relative"], "before": state})
            self.log(f"Установлено: {e['relative']}")
            if fail_after is not None and done >= fail_after:
                raise InstallError("Тестовое прерывание установки.")
        self.progress(total, total, "Проверка результата")
        bad = self.verify()
        if bad:
            raise InstallError("После установки не совпали контрольные суммы:\n- " + "\n- ".join(bad))
        self._history("install", changed)
        j = self.work / "journal.json"
        if j.exists():
            j.unlink()
        self.log(f"Готово: установлено файлов — {done}, всего проверено — {len(self.files)}.")
        return {"installed": done, "checked": len(self.files), "backup": str(backup_dir) if (backup_dir.exists()) else None,
                "originals": str(self.work / "originals")}

    def verify(self):
        """Список расхождений: файл должен совпадать с эталоном русификатора."""
        bad = []
        for e in self.files:
            p = self.game_path(e)
            if not p.exists() or sha256_file(p) != e["new_sha256"]:
                bad.append(e["relative"])
        return bad

    # --- откат
    def restore(self, fail_after=None):
        report = {"problems": []}
        running = running_game_processes(self.root)
        if running:
            raise InstallError("Игра запущена (" + ", ".join(running) + "). Закройте её и повторите.")
        order = sorted(self.files, key=lambda f: (1 if f["kind"] == "catalog" else 0, f["order"]))   # каталог последним
        todo = []
        for e in order:
            state, _ = self.classify(e)
            if state == "original":
                continue
            op = self.original_path(e)
            if not op.exists() or sha256_file(op) != e["original_sha256"]:
                raise InstallError(f"Нет проверенной копии оригинала для {e['relative']}. Используйте в Steam «Проверку целостности файлов»; "
                                   "резервные копии не удалены (папка _rus_v2 в папке игры).")
            todo.append((e, state))
        if not todo:
            self.log("Все файлы уже оригинальные: восстанавливать нечего.")
            return {"restored": 0}
        self._journal("restoring")
        for i, (e, state) in enumerate(todo):
            self.progress(i, len(todo), "Восстановление: " + Path(e["relative"]).name)
            self._copy_verified(self.original_path(e), self.game_path(e), e["original_sha256"])
            self.log(f"Восстановлен оригинал: {e['relative']}" + (" (файл был в неизвестном состоянии)" if state == "unknown" else ""))
            if fail_after is not None and i + 1 >= fail_after:
                raise InstallError("Тестовое прерывание отката.")
        bad = [e["relative"] for e in self.files if self.classify(e)[0] != "original"]
        if bad:
            raise InstallError("После отката не совпали контрольные суммы:\n- " + "\n- ".join(bad))
        self._history("restore", [{"relative": e["relative"], "before": s} for e, s in todo])
        j = self.work / "journal.json"
        if j.exists():
            j.unlink()
        self.log(f"Оригинальные файлы восстановлены: {len(todo)}. Резервные копии в папке {WORK_DIR} сохранены.")
        return {"restored": len(todo)}


def report_sha(path):
    return sha256_file(path)


STATE_TEXT = {"original": "оригинал", "installed": "русификатор v2.0", "older": "прежняя сборка v2.0 (будет обновлена)",
              "unknown": "НЕИЗВЕСТНО", "missing": "нет файла"}
