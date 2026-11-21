# Публикация VELIQEN Link / GitHub release guide

## Состав выпуска 1.2.1

| Файл | Назначение |
| --- | --- |
| `veliqen-link_1.2.1_amd64.deb` | Установка Ubuntu и зависимостей через APT |
| `VELIQEN-Link-1.2.1-Android.apk` | Подписанное приложение для телефона |
| `VELIQEN-Link-1.2.1-Source.zip` | Соответствующие исходники, лицензии, скрипты сборки, исходники библиотек scrcpy |
| `VELIQEN-Link-1.2.1-Full.zip` | Полный комплект с исходниками, бинарными пакетами и дополнительным установщиком |
| `VELIQEN-Link-1.2.1-ROM.zip` | Отдельная интеграция для сборки прошивки |
| `VELIQEN-Link-1.2.1-Presentation-RU.pptx`, `VELIQEN-Link-1.2.1-Presentation-EN.pptx` | Редактируемые презентации |
| `VELIQEN-Link-1.2.1-Presentation-RU.pdf`, `VELIQEN-Link-1.2.1-Presentation-EN.pdf` | Презентации для просмотра |
| `SHA256SUMS.txt` | Контрольные суммы публичных файлов |
| `RELEASE-NOTES.md` | Текст описания GitHub Release |

Архив `VELIQEN-Link-PRIVATE-SIGNING-DO-NOT-PUBLISH.zip` предназначен только владельцу. Он содержит приватный ключ и пароль. **Не загружай его в репозиторий, GitHub Release, issue или общедоступную папку.** Сохрани личную резервную копию: этот ключ необходим для совместимых обновлений Android.

## Репозиторий

Рекомендуемое отдельное имя: **VELIQEN-Link**. Репозиторий не создаётся и не публикуется автоматически этим комплектом.

1. Создай выбранный репозиторий на GitHub. Для нового пустого репозитория не добавляй автоматически README и лицензию: они уже есть в исходниках.
2. Распакуй `VELIQEN-Link-1.2.1-Source.zip`. В репозиторий загружается **содержимое** его корневой папки, включая `.github` и `.gitignore`.
3. Сохрани изменения коммитом и отправь его в выбранный репозиторий. Для отправки используй точный адрес, который GitHub показывает на странице этого репозитория.
4. Открой вкладку Actions и дождись проверки `Build and check`. CI собирает DEB и unsigned APK. Публичная подпись хранится у владельца и в этот workflow не передаётся.

В чистой распакованной папке подготовка локального коммита выглядит так:

```bash
git init -b main
git add .
git status --short
git commit -m "Prepare VELIQEN Link 1.2.1"
```

После этого GitHub покажет команды `git remote add origin ...` и `git push -u origin main` с адресом именно твоего репозитория. Эти команды выполняет владелец после выбора места публикации. Бинарные DEB/APK не нужно хранить в истории Git: `.gitignore` исключает их. Исходники поставляемых библиотек в `vendor/sources` сохраняются с проектом.

## GitHub Release

1. После загрузки выбранного коммита создай аннотированный тег и отправь его:

```bash
git tag -a v1.2.1 -m "VELIQEN Link 1.2.1"
git push origin v1.2.1
```

2. На GitHub выбери **Releases → Draft a new release**, тег `v1.2.1`.
3. Название выпуска: **VELIQEN Link 1.2.1**. Текст возьми из `RELEASE-NOTES.md`.
4. Добавь публичные файлы из таблицы выше. Source ZIP должен соответствовать этим DEB/APK. В нём сохранены полные исходники Android, патч к upstream, исходники GNOME/Ubuntu и поставляемых библиотек.
5. Проверь вложения и нажми Publish release, когда описание и назначение репозитория утверждены владельцем.

Презентации описывают функции и показывают интерфейс. На скриншоте реальной работы Ubuntu видна предыдущая версия 1.1.0, что отмечено на слайде. Это не подменяет проверку всех функций нового выпуска на целевом оборудовании.

## Проверка скачанных файлов

Помести публичные файлы и `SHA256SUMS.txt` в одну папку:

```bash
sha256sum --check SHA256SUMS.txt
```

Если скачана только часть файлов, сообщения об отсутствующих остальных ожидаемы. Для двух установочных файлов:

```bash
grep -E '  (veliqen-link_1\.2\.1_amd64\.deb|VELIQEN-Link-1\.2\.1-Android\.apk)$' SHA256SUMS.txt | sha256sum --check
```

Хэш обнаруживает повреждение файла относительно опубликованного списка. Он сам по себе не удостоверяет владельца аккаунта GitHub. Постоянный сертификат Android указан в `android/RELEASE-SIGNING.json` и проверяется `android/VERIFY-APK.py`.

## Следующий выпуск

- Увеличь версии приложения, DEB и Android `versionCode`.
- Подпиши APK **тем же** приватным release-ключом. Не создавай новый вместо потерянного для уже опубликованного приложения.
- Пересобери исходный архив, бинарные пакеты и контрольные суммы одной версии.
- Не перезаписывай опубликованные файлы старого тега другими байтами. Создай следующий выпуск.
- Сохраняй исходные лицензии, соответствующий исходный код и уведомления компонентов.

## English

Create a separate repository for VELIQEN Link when its destination is agreed. Commit the contents of the Source ZIP, including `.github` and `.gitignore`. Keep installable DEB/APK files as release assets. The included workflow has read-only repository permissions and builds a DEB plus an unsigned Android artifact. It neither signs nor publishes releases.

Use tag `v1.2.1`, the supplied release notes and public assets listed above. Publish the corresponding source archive and notices alongside binaries. Check the hardware behavior you intend to support and describe the tested scope accurately.

The private signing ZIP is an owner backup and must never be published. Preserve the same `tech.veliqen.link` application ID and release key for subsequent Android updates. Earlier development packages used a different ID, so the first public APK installs alongside them and needs pairing and permissions once.

## Official references

- GitHub releases and assets: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- Android signing: https://developer.android.com/studio/publish/app-signing
- GitHub Actions sources are pinned to exact release commits in `.github/workflows/build.yml`.
