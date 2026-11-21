# KDE Connect backend: проверенные интерфейсы

Модуль `veliqen_link/kde.py` использует существующую службу KDE Connect в пользовательском сеансе D-Bus. Он не подключается к телефону напрямую и не хранит отдельные ключи сопряжения. Вызовы нужно выполнять в рабочем потоке, чтобы окно GTK оставалось отзывчивым.

## Источники

Сигнатуры проверены по исходникам [KDE/kdeconnect-kde](https://github.com/KDE/kdeconnect-kde) на коммите `6fa6dac15f1019bff6a922cf3014e222f5140f66` (проверка 20 ноября 2026). Во время запуска также проверяется D-Bus introspection установленной версии.

- [`core/daemon.h`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/core/daemon.h): `devices(bool,bool)`, обнаружение устройств.
- [`core/device.h`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/core/device.h): свойства, запрос/подтверждение сопряжения, разрыв сопряжения.
- [`dbusinterfaces/dbusinterfaces.cpp`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/dbusinterfaces/dbusinterfaces.cpp): реальные пути объектов.
- [`plugins/sms/smsplugin.h`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/plugins/sms/smsplugin.h) и [`smsplugin.cpp`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/plugins/sms/smsplugin.cpp): отправка SMS с Android subscription ID.
- [`plugins/sms/conversationsdbusinterface.h`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/plugins/sms/conversationsdbusinterface.h): список диалогов и сигналы истории.
- [`models/conversationmessage.h`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/models/conversationmessage.h): сериализация сообщения и адреса в D-Bus.
- [`plugins/share/shareplugin.h`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/plugins/share/shareplugin.h): файлы, ссылки и явно переданный текст.
- [`plugins/notifications/notification.h`](https://github.com/KDE/kdeconnect-kde/blob/6fa6dac15f1019bff6a922cf3014e222f5140f66/plugins/notifications/notification.h): свойства уведомлений, закрытие и ответ.
- Включённый Android-исходник `src/main/java/org/kde/kdeconnect/plugins/share/SharePlugin.kt`, метод `receiveText`, вызывает Android `ClipboardManager.setPrimaryClip`. Поэтому явная передача текста использует `shareText`, не пытаясь читать глобальный Wayland-буфер из фонового Qt-процесса.

## Точные SMS-сигнатуры

Служба: `org.kde.kdeconnect`.

| Действие | Путь | Интерфейс и метод | Входные типы |
|---|---|---|---|
| Диалоги | `/modules/kdeconnect/devices/ID` | `org.kde.kdeconnect.device.conversations.activeConversations` | Нет, возвращает `av` |
| Обновление диалогов | тот же | `requestAllConversationThreads` | Нет |
| История | тот же | `requestConversation` | `xii`: thread ID, начало, конец диапазона |
| Отправка SMS | `/modules/kdeconnect/devices/ID/sms` | `org.kde.kdeconnect.device.sms.sendSms` | `avsavx`: адреса, текст, вложения, subscription ID |
| Резервный вызов без выбора SIM | путь устройства | `conversations.sendWithoutConversation` | `avsav` |

Каждый адрес в `av` является variant, содержащим структуру `(s)`, а не словарь. Сообщение — структура `(isa(s)xiixixa(xsss))`: события, текст, адреса, дата, тип, флаг прочтения, thread ID, message ID, subscription ID, вложения. `sim=-1` означает выбор SIM по умолчанию на телефоне. Идентификатор подписки не равен номеру слота «SIM 1/2».

История приходит сигналами. Модуль ждёт ограниченное время и возвращает доступные сообщения (по умолчанию до 100). Повторное обновление может добавить сообщения, если телефон отвечает медленно. Пустой список диалогов не доказывает, что на телефоне отсутствуют SMS: проверьте разрешения и обновите список. Кнопка штатного окна `kdeconnect-sms` остаётся доступным резервным вариантом.

## Результат операций и совместимость

- Возврат `status=requested` означает, что служба приняла запрос. Он не подтверждает получение файла, доставку SMS оператором или обработку ответа приложением на телефоне.
- Отправки никогда не повторяются автоматически. При ошибке с неопределённым результатом интерфейс должен предложить проверить телефон.
- Неподдерживаемая сигнатура приводит к понятной ошибке до выполнения операции. Выбор конкретной SIM не заменяется молча на SIM по умолчанию.
- `status()` ничего не запускает; `available`/`running` означают наличие владельца D-Bus имени, `installed` — наличие CLI. `start_service()` активирует упакованную пользовательскую D-Bus службу.
- Для файлов используются URI от `Path.as_uri()`, корректно кодирующие пробелы, кириллицу, `%` и `#`.
- GNOME/Wayland: явная отправка текста из интерфейса работает через `shareText`. Полная автоматическая фоновая синхронизация зависит от интеграции рабочего стола; оба направления необходимо проверить в целевом сеансе. Этот модуль не обещает свободный глобальный доступ обычного GTK/Qt-приложения к буферу.

## Проверка

```bash
cd linux
/usr/bin/python3 -m unittest -v test_kde
```

Тесты запускают отдельный `dbus-daemon` только на `127.0.0.1` со случайным свободным TCP-портом и аутентификацией `DBUS_COOKIE_SHA1`, затем регистрируют тестовый сервис через Gio. Этот способ позволяет проверять настоящие D-Bus вызовы даже в среде, которая не разрешает Unix-сокеты. Дополнительный набор с тестовым транспортом проверяет те же варианты без сетевого сокета.

Проверяются реальная сериализация/десериализация `GLib.Variant`, пути, introspection, сигналы истории, проверка сопряжения, отсутствие повторной отправки и варианты API. Тесты не обращаются к пользовательскому D-Bus или реальному телефону и не отправляют SMS. Это проверка программного протокола, а не совместимости конкретного телефона.
