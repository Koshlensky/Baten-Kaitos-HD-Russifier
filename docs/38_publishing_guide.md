# 38. Публикация в существующем проекте Koshlensky/Baten-Kaitos-HD-Russifier (без force-push)

Адрес проекта сохраняется. Публичная папка с чистой историей — `bk_loc_public` (один коммит, тег `v2.0.0`). Ничего ниже автоматически не выполнялось.

## 0. Подготовка (один раз)
1. Скопируйте старый репозиторий целиком (резервная копия, в том числе всех веток и тегов):
   `git clone --mirror https://github.com/Koshlensky/Baten-Kaitos-HD-Russifier.git old-v1-mirror.git`
2. Проверьте историю старого репозитория тем же инструментом, что и рабочую:
   `git clone https://github.com/Koshlensky/Baten-Kaitos-HD-Russifier.git old-v1` → в рабочем проекте: `python -m tools.publish.audit_history <путь>\old-v1`.
   Если в истории v1.0 есть ресурсы игры, перевод Grok v1.0 или другие данные, которые не должны оставаться публичными, **не оставляйте эту историю в проекте**: сделайте её приватной/архивной копией (Settings → Change visibility) или создайте новый репозиторий; удалить объекты из публичной истории без force-push нельзя, а у GitHub они остаются в кэше и в форках.
3. По желанию замените анонимного автора единственного коммита на свою учётную запись (до публикации): в папке `bk_loc_public`
   `git config user.name "<имя>"`, `git config user.email "<e-mail GitHub или id+имя@users.noreply.github.com>"`, `git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" --amend --reset-author --no-edit`, затем `git tag -d v2.0.0` и `git tag -a v2.0.0 -m "Русификатор v2.0.0"`.
4. Выберите лицензию на данные (`DATA_LICENSE.md`) и при необходимости поправьте файл до публикации.

## 1. Публикация без перезаписи истории (рекомендуется)
Новая история заводится отдельной веткой существующего репозитория; старая `main` не затрагивается и не перезаписывается.
```powershell
cd <библиотека Steam>\bk_loc_public
git remote add origin https://github.com/Koshlensky/Baten-Kaitos-HD-Russifier.git
git fetch origin                              # убедиться, что доступ есть и ветка main старая
git push origin main:v2                       # новая ветка v2 (несвязанная история), без --force
git push origin v2.0.0                        # тег
```
Затем сделайте `v2` основной веткой, не трогая историю:
- через веб: Settings → Branches → Default branch → `v2`;
- или `gh repo edit Koshlensky/Baten-Kaitos-HD-Russifier --default-branch v2`.

Если нужно, чтобы основная ветка снова называлась `main` (переименование, не force-push):
```powershell
gh api -X POST repos/Koshlensky/Baten-Kaitos-HD-Russifier/branches/main/rename -f new_name=v1-archive
gh api -X POST repos/Koshlensky/Baten-Kaitos-HD-Russifier/branches/v2/rename -f new_name=main
```
Адрес `github.com/Koshlensky/Baten-Kaitos-HD-Russifier` остаётся прежним; старая история доступна в ветке `v1-archive`.

## 2. GitHub Release
EXE хранится только в релизе (в репозиторий не добавляется). Проверьте хеш перед загрузкой:
```powershell
(Get-FileHash <репозиторий>\dist\release_delta\BatenKaitos_Rusifikator_v2.0_delta.exe -Algorithm SHA256).Hash
# должно быть: 78AFE1361092B784E48E6D64AA34BC170FCAE40339996C664CF86A399D6A884D
gh release create v2.0.0 "<репозиторий>\dist\release_delta\BatenKaitos_Rusifikator_v2.0_delta.exe" `
  --repo Koshlensky/Baten-Kaitos-HD-Russifier --title "Русификатор v2.0" `
  --notes-file <библиотека Steam>\bk_loc_public\RELEASE_NOTES_v2.0.md --verify-tag
```
После загрузки скачайте файл из релиза и сверьте SHA-256 с указанным в описании.

## 3. Проверка после публикации
- `git ls-remote https://github.com/Koshlensky/Baten-Kaitos-HD-Russifier.git` — есть ветка v2/main и тег v2.0.0.
- Страница проекта показывает новый README, в релизе один файл `BatenKaitos_Rusifikator_v2.0_delta.exe`.
- Свежий клон проходит шаги из README («Быстрый старт») на собственной копии игры.

## Чего не делать
- Не использовать `git push --force` и `--force-with-lease`; не заливать в проект папку `bk_loc_v2` и её историю (в ней тексты игры и пакеты перевода с оригиналом).
- Не добавлять EXE, `source/`, `backups/`, `dist/` в репозиторий.
