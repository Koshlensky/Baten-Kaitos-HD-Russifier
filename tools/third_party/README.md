# Сторонние инструменты

Бинарники сюда скачиваются вручную и в git не попадают (см. `.gitignore`).

| Инструмент | Назначение | Источник | Версия | SHA-256 архива |
|---|---|---|---|---|
| Il2CppDumper | RE-проба: сигнатуры и строковые литералы из `GameAssembly.dll` + `global-metadata.dat` | https://github.com/Perfare/Il2CppDumper/releases/download/v6.7.46/Il2CppDumper-net6-win-v6.7.46.zip | v6.7.46 (net6-win) | `D1117D66B9F8A130FD4933AE99F8EB9A0ECBBDB79D1FC494EB738AD910FF765A` |

В `Il2CppDumper/config.json` изменено: `RequireAnyKey: false` (без ожидания клавиши), `GenerateDummyDll: false` (заглушки DLL не нужны).

Инструменты запускаются только на копиях файлов игры из `work/re/`.
