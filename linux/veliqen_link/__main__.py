import argparse
import sys

def main(argv=None):
    from .intents import PAGES
    parser = argparse.ArgumentParser(description="VELIQEN Link — связь Ubuntu и Android")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--doctor", action="store_true", help="Проверить зависимости без доступа к сообщениям")
    parser.add_argument("--background", action="store_true", help="Запуск в фоне")
    parser.add_argument("--share", metavar="FILE", help="Отправить файл выбранному телефону")
    parser.add_argument("--page", choices=sorted(PAGES), help="Открыть раздел в текущем окне приложения")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0
    if args.doctor:
        from .doctor import report
        print(report())
        return 0
    try:
        from .app import LinkApplication
    except ImportError as exc:
        print("Не хватает библиотек интерфейса. Установи DEB через «Установку приложений» Ubuntu или sudo apt install ./veliqen-link_1.2.1_amd64.deb.\n" + str(exc), file=sys.stderr)
        return 2
    files = ([args.share] if args.share else []) + args.files
    if args.page:
        files.insert(0, "veliqen://" + args.page)
    app = LinkApplication(background=args.background)
    return app.run(["veliqen-link"] + files)

if __name__ == "__main__":
    sys.exit(main())
