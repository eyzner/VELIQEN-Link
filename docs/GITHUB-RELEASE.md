# Публикация VELIQEN Link

## Обновить готовый выпуск

В комплекте публикации находятся `PUBLISH-GITHUB.sh` и `VELIQEN-Link-1.2.1-Full.zip`. Открой терминал в папке с ними и выполни:

```bash
bash PUBLISH-GITHUB.sh
```

При необходимости войди в аккаунт GitHub **eyzner**, следуя подсказкам в терминале. Скрипт обновит инструкции и названия папок в существующем репозитории `eyzner/VELIQEN-Link`, загрузит файлы и опубликует комплект **v1.2.1-r2**. Для нового пустого репозитория он также выполнит первоначальную публикацию.

Весь комплект уже подготовлен. На компьютере нужны Python 3, Git и GitHub CLI; если Git или GitHub CLI отсутствуют, скрипт предложит установить их через APT. Запускай скрипт обычным пользователем.

## Что получат пользователи

Для установки достаточно двух файлов:

| Устройство | Файл |
| --- | --- |
| Ubuntu | `veliqen-link_1.2.1_amd64.deb` |
| Телефон | `VELIQEN-Link-1.2.1-Android.apk` |

В полном архиве они находятся в папке **Установочные-файлы**. На странице GitHub их можно скачать отдельно. Дополнительно в выпуске размещены полный комплект, исходники, комплект интеграции с прошивкой, презентации на русском и английском в PPTX и PDF, описание выпуска и контрольные суммы.

Версия приложений остаётся **1.2.1**. Отметка **r2** означает исправленное издание комплекта с упрощёнными инструкциями и понятными названиями папок.

## Если загрузка прервалась

Запусти ту же команду снова. Скрипт проверит загруженные файлы и продолжит с недостающих. Выпуск становится публичным после проверки всех вложений.

Если основная ветка GitHub содержит дополнительные изменения, скрипт остановится и сообщит об этом. Старый выпуск `v1.2.1` сохраняется; исправленный комплект публикуется отдельно и становится последним выпуском.

Личный архив с ключом подписи предназначен владельцу проекта и не входит в публикуемые файлы.

## Проверить комплект локально

```bash
bash PUBLISH-GITHUB.sh --dry-run
```

Эта команда проверяет файлы без обращения к GitHub.

## English

Place `PUBLISH-GITHUB.sh` beside `VELIQEN-Link-1.2.1-Full.zip` and run `bash PUBLISH-GITHUB.sh` as your ordinary Ubuntu user. Follow GitHub CLI's sign-in instructions when prompted.

The script updates the existing VELIQEN Link repository with the revised instructions and folder names, then publishes **v1.2.1-r2** with all prepared downloads. It also supports an empty new repository. Ready-to-install files are in the **Установочные-файлы** (Installers) folder inside the full kit and are attached separately to the GitHub release.

The app version remains **1.2.1**. The **r2** label identifies the revised kit. The earlier release remains available. Run the same command again after an interrupted upload. Use `--dry-run` for a local check.
