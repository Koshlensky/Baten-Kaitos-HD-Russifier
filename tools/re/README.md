# Скрипты реверс-инжиниринга

Работают только с копиями файлов игры в `work/re/<игра>/` (создаются `copy_from_game`). Il2CppDumper лежит в `tools/third_party/Il2CppDumper/` (в git не попадает).

## Этап A: говорящие и порядок реплик

```powershell
.\tools\third_party\Il2CppDumper\Il2CppDumper.exe work\re\bk1\GameAssembly.dll work\re\bk1\global-metadata.dat work\re\bk1\dump
.\tools\third_party\Il2CppDumper\Il2CppDumper.exe work\re\bk2\GameAssembly.dll work\re\bk2\global-metadata.dat work\re\bk2\dump
foreach ($g in "bk1", "bk2") {
  .\.venv\Scripts\python.exe -m tools.re.script_data --game $g   # массивы ScriptCode/ScriptResource -> work/re/<g>/scripts.json
  .\.venv\Scripts\python.exe -m tools.re.sysfuncs $g             # номера системных функций -> work/re/<g>/sysfuncs.json
  .\.venv\Scripts\python.exe -m tools.re.vm_spec $g              # обработчики опкодов -> work/re/<g>/vm_spec.json
  .\.venv\Scripts\python.exe -m tools.re.calls $g                # вызовы с аргументами -> work/re/<g>/calls.json
  .\.venv\Scripts\python.exe -m tools.re.speakers --game $g      # base/evidence/<g>_script_speakers.jsonl, base/scenes/<g>_script_sequences.json
  .\.venv\Scripts\python.exe -m tools.re.text_map --game $g      # base/scenes/<g>_script_files.json
}
.\.venv\Scripts\python.exe -m tools.bkloc.matrix                 # говорящие попадают в матрицу
```

Модули:
- `script_data.py` — данные скриптов из статических конструкторов (привязка массива к полю по смещению записи).
- `vm_disasm.py` — длины операндов, поправка номеров опкодов BK2, распаковка байткода, таблицы перемещений, разбор с якорями.
- `sysfuncs.py` — таблица системных функций `FunctionData(номер, имя)`.
- `calls.py` — вызовы и их аргументы (адреса ресурсов с маркерами, числа, загрузки из кадра).
- `speakers.py` — функции реплики по отпечаткам и обёрткам, таблички имени, продолжения окна.
- `text_map.py` — карта «строка → файл скрипта».

Результаты и выводы — в [docs/03d_re_stage_a_report.md](../../docs/03d_re_stage_a_report.md).

## Первая проба (RE-spike)

```powershell
.\.venv\Scripts\python.exe tools\re\list_classes.py            # классы игры, связанные с текстом и окнами
.\.venv\Scripts\python.exe tools\re\list_system_functions.py   # системные функции скриптовой машины
.\.venv\Scripts\python.exe tools\re\count_markers.py           # маркеры @@ID@@ в ресурсах скриптов
.\.venv\Scripts\python.exe tools\re\spirit_gender_variants.py  # варианты _0/_1 реплик духа-хранителя
.\.venv\Scripts\python.exe tools\re\disasm.py имя:файловое_смещение:VA
```

Выводы пробы — в [docs/03b_re_spike_report.md](../../docs/03b_re_spike_report.md).
