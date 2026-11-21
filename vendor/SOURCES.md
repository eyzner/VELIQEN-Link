# scrcpy: исходники и уведомления / Sources and notices

VELIQEN Link поставляет неизменённые файлы официального Linux x86_64-комплекта
scrcpy 5.0.1. Архив проверяется по SHA-256 из `SCRCPY_PROVENANCE.json`.
Ни scrcpy, ни его библиотеки не становятся собственными разработками VELIQEN.

## Включённые компоненты

| Компонент | Версия | Исходники в `sources/` | Основная лицензия |
| --- | --- | --- | --- |
| scrcpy, клиент и Android server | 5.0.1 | `scrcpy-5.0.1.tar.gz` | Apache-2.0 |
| SDL | 3.4.18 | `SDL-3.4.18.tar.gz` | zlib |
| FFmpeg | 9.0.2 | `ffmpeg-9.0.2.tar.xz` | LGPL-2.1-or-later для конфигурации этого бинарного файла |
| dav1d | 1.5.4 | `dav1d-1.5.4.tar.xz` | BSD-2-Clause |
| libusb | 1.0.30 | `libusb-1.0.30.tar.gz` | LGPL-2.1-or-later |
| zlib, источник из Ubuntu Jammy | 1.2.11.dfsg-2ubuntu9.2 | `.dsc`, `.orig.tar.gz`, `.debian.tar.xz` с префиксом `zlib_` | zlib |
| v4l-utils / libv4l, источник build-зависимости Ubuntu Jammy | 1.22.1-2build1 | `.dsc`, `.orig.tar.bz2`, `.debian.tar.xz` с префиксом `v4l-utils_` | LGPL для libv4l, GPL для отдельных утилит, см. исходные файлы |
| Android Debug Bridge | platform-tools 37.0.1 | Источники AOSP по ссылке ниже | Apache-2.0 и уведомления компонентов |

Полные тексты лицензий и уведомлений находятся в `licenses/`. Уведомление
`Android-platform-tools-37.0.1-NOTICE.txt` сохранено из официального архива
platform-tools целиком: оно охватывает и инструменты SDK, которые VELIQEN не
поставляет. Из platform-tools здесь используется только ADB, как в upstream scrcpy.

Архивы исходников включены в публичный `VELIQEN-Link-1.2.1-Source.zip` и полный
комплект. Их не нужно устанавливать на пользовательскую систему. DEB содержит
уведомления в `/usr/share/doc/veliqen-link/vendor-licenses/`.

## Как определены версии

- `scrcpy --version` подтверждает 5.0.1, SDL 3.4.18, libusb 1.0.30 и версии
  библиотек FFmpeg. Бинарный файл содержит конфигурацию FFmpeg и строки
  лицензии LGPL version 2.1 or later.
- В исходном теге scrcpy 5.0.1 файлы `app/deps/{sdl,ffmpeg,dav1d,libusb}.sh`
  задают точные версии, URLs и SHA-256. Скачанные архивы сверены с этими хэшами.
- `.github/workflows/release.yml` upstream использует Ubuntu 22.04 для Linux
  x86_64. Строка компилятора в бинарном файле также указывает Ubuntu 22.04.
- Бинарный файл содержит zlib 1.2.11. Точный суффикс пакета Ubuntu из него не
  восстанавливается. В комплект добавлен опубликованный исходный пакет Jammy
  1.2.11.dfsg-2ubuntu9.2 вместе с патчами. Это источник соответствующей ветки
  Ubuntu, а не заявление о доказанной точной ревизии сборочной машины upstream.
- libv4l — build-зависимость upstream и включённая опция FFmpeg. Её исходный
  пакет из Jammy сохранён дополнительно. Само наличие опции не доказывает,
  что все функции libv4l попали в окончательный статический бинарный файл.

`sources/SOURCES.json` содержит URL, размеры и SHA-256 каждого файла. Для
архивов Ubuntu хэши и размеры проверены по секции `Checksums-Sha256` официальных
`.dsc`, полученных по HTTPS. Подписи PGP этих `.dsc` здесь отдельно не проверялись.
Хэш архива тега scrcpy зафиксирован после скачивания с официального репозитория;
он не выдаётся за отдельно подписанную контрольную сумму автора.

## Сборка и замена библиотек

Полный исходный код scrcpy и скрипты его сборки находятся внутри
`scrcpy-5.0.1.tar.gz`. Отправная точка — `doc/build.md`, а точная процедура
официального статического Linux-клиента — `release/build_linux.sh` и
`.github/workflows/release.yml` этого тега. В них перечислены системные
build-зависимости, параметры компилятора и линковщика.

Чтобы собрать клиент с изменёнными библиотеками, распакуйте исходники в
отдельную рабочую папку, установите build-зависимости из upstream workflow и
подготовьте локальные архивы для его скриптов. Следующие команды выполняются
из каталога `vendor` исходного комплекта, не из системного каталога установки:

```bash
mkdir -p rebuild
tar -xf sources/scrcpy-5.0.1.tar.gz -C rebuild
mkdir -p rebuild/scrcpy-5.0.1/app/deps/work/sources
cp sources/SDL-3.4.18.tar.gz rebuild/scrcpy-5.0.1/app/deps/work/sources/sdl-3.4.18.tar.gz
cp sources/dav1d-1.5.4.tar.xz sources/ffmpeg-9.0.2.tar.xz sources/libusb-1.0.30.tar.gz rebuild/scrcpy-5.0.1/app/deps/work/sources/
cd rebuild/scrcpy-5.0.1
VERSION=v5.0.1 bash release/build_linux.sh x86_64
```

Upstream-скрипт дополнительно загружает официальный ADB, проверяя его SHA-256.
Для изменённых библиотек распакуйте и измените их исходники в
`app/deps/work/sources/`, затем очистите соответствующие каталоги build/install
перед повторной сборкой. Скрипты `_init` и `app/deps/*.sh` показывают имена
папок. Контрольные суммы оригинальных архивов менять для неизменённых копий
не требуется. Чтобы собрать изменённый архив под другим именем, измените
URL/имя/контрольную сумму в собственной копии соответствующего скрипта.

Исходные пакеты Ubuntu с применением патчей распаковываются обычным
`dpkg-source -x`: сохраняйте `.dsc`, `.orig.tar.*` и `.debian.tar.*` рядом.
Инструкции для сборки отдельных библиотек находятся в их исходниках.

Полученный клиент расположен в
`release/work/build-linux-x86_64/dist/scrcpy`. Его можно использовать с
сервером `scrcpy-server` **той же версии 5.0.1** либо собрать сервер из
включённых исходников через `release/build_server.sh`. Полная сборка сервера
требует Android SDK и JDK, указанных в upstream-проекте.

VELIQEN не запрещает изменение, обратную разработку для отладки изменённых
LGPL-библиотек или повторную линковку в рамках их лицензий. Для использования
пересобранного клиента в установленном VELIQEN Link администратор может
сохранить резервную копию и заменить `scrcpy` в
`/usr/lib/veliqen-link/scrcpy/`, сохранив совместимый сервер. Следующее обновление
DEB восстановит пакетную версию. Приведённые команды описывают upstream-путь
пересборки, который не выполнялся в рамках подготовки этого выпуска. Побайтовая
воспроизводимость официального бинарного архива не заявляется.

## English

The official scrcpy 5.0.1 Linux x86_64 binaries are redistributed unchanged.
The source bundle includes scrcpy's complete client/server sources and build
scripts, the exact SDL, FFmpeg, dav1d and libusb releases pinned by those
scripts, plus Jammy zlib and v4l-utils source packages with their distribution
patches. Full notices are in `licenses/`. The table and `sources/SOURCES.json`
identify every archive, URL, size and checksum.

The upstream build workflow uses Ubuntu 22.04. zlib 1.2.11 is visible in the
binary, but its precise Ubuntu package revision cannot be recovered from that
string. The supplied Jammy source revision is documented accordingly. libv4l
is included as an upstream build dependency, without asserting that every
function is linked into the final binary.

Use the commands above and upstream `doc/build.md` / `release/build_linux.sh`
to rebuild or relink against modified libraries. The same-version server must
accompany the rebuilt client. The administrator may replace the app's private
scrcpy executable with that build. No extra restriction on reverse engineering
for debugging modified LGPL components is imposed. This release does not claim
that upstream binaries were reproduced byte for byte. Ubuntu payload checksums
were checked against the HTTPS-retrieved `.dsc`; its PGP signature was not
independently validated here.

## Primary sources

- scrcpy release: https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1
- Exact scrcpy source/build workflow: https://github.com/Genymobile/scrcpy/tree/v5.0.1
- FFmpeg: https://ffmpeg.org/releases/
- SDL: https://github.com/libsdl-org/SDL/tree/release-3.4.18
- dav1d: https://downloads.videolan.org/pub/videolan/dav1d/1.5.4/
- libusb: https://github.com/libusb/libusb/tree/v1.0.30
- Ubuntu zlib source: https://packages.ubuntu.com/source/jammy/zlib
- Ubuntu v4l-utils source: https://packages.ubuntu.com/source/jammy/v4l-utils
- ADB source: https://android.googlesource.com/platform/packages/modules/adb/
- Platform tools release information: https://developer.android.com/tools/releases/platform-tools

