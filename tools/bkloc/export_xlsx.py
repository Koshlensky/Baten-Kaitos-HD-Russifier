"""Выгрузка матрицы в XLSX для просмотра (exports/<game>_matrix.xlsx).

Таблица только для чтения: правки вносятся через инструменты в base/matrix,
иначе их не увидят проверки и журнал истории.
"""
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.cell import WriteOnlyCell

from tools.bkloc.matrix import load
from tools.bkloc.paths import EXPORTS, GAMES, SOURCE, guard_write, read_jsonl

COLUMNS = [
    ("key", 30), ("scene_id", 22), ("batch_id", 12), ("order", 6), ("category", 14), ("form", 10),
    ("speaker", 18), ("speaker_status", 12), ("speaker_gender", 8), ("gender_evidence", 12), ("style", 12),
    ("addressee", 18), ("register", 8), ("en", 70), ("jp", 50), ("ru", 70), ("status", 12),
    ("context", 40), ("translator_note", 30), ("editor_note", 30),
]


def export(game):
    jp = {r["id"]: r.get("jp", "") for r in read_jsonl(SOURCE / game / "lines.jsonl")}
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(game)
    ws.freeze_panes = "B2"
    for i, (_name, width) in enumerate(COLUMNS):
        ws.column_dimensions[chr(ord("A") + i)].width = width
    header = []
    for name, _ in COLUMNS:
        cell = WriteOnlyCell(ws, value=name)
        cell.font = Font(bold=True)
        header.append(cell)
    ws.append(header)
    wrap = Alignment(wrap_text=True, vertical="top")
    for r in load(game):
        sp, ad = r["speaker"], r["addressee"]
        values = [
            r["key"], r["scene_id"], r["batch_id"], r["order"], r["category"], r["form"],
            sp["id"] if sp["id"] != "unknown" else ", ".join(sp.get("candidates", [])),
            sp["status"], r["speaker_gender"], sp.get("gender_evidence", {}).get("verdict", ""),
            ", ".join(sp.get("style", [])),
            ad["id"] if ad["id"] != "unknown" else ", ".join(ad.get("candidates", [])),
            r["register"], r["en"], jp.get(r["text_id"], ""), r["ru"], r["status"],
            r["context"], r["translator_note"], r["editor_note"],
        ]
        row = []
        for name, value in zip((c[0] for c in COLUMNS), values):
            cell = WriteOnlyCell(ws, value=value)
            if name in ("en", "jp", "ru", "context"):
                cell.alignment = wrap
            row.append(cell)
        ws.append(row)
    out = guard_write(EXPORTS / f"{game}_matrix.xlsx")
    wb.save(out)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for game in GAMES:
        print(export(game))


if __name__ == "__main__":
    main()
