"""Parse desktop entry points without performing calls, sends or file transfers."""
import re
import unicodedata
from urllib.parse import parse_qs, unquote, urlsplit

PAGES = {"overview", "messages", "calls", "files", "screen", "settings"}
MAX_BODY_LENGTH = 4096
MAX_URI_LENGTH = 65536


def _invalid_character(value):
    # C0/C1 controls include NUL, CR/LF and DEL. Unpaired surrogates cannot be
    # passed to GTK's UTF-8 text API. Format characters inside normal text stay.
    return any(unicodedata.category(character) in {"Cc", "Cs"} for character in value)


def parse_intent(uri):
    if not isinstance(uri, str) or len(uri) > MAX_URI_LENGTH or _invalid_character(uri):
        raise ValueError("Некорректная ссылка.")
    parts = urlsplit(uri)
    if parts.scheme == "veliqen":
        if parts.netloc not in PAGES or parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("Неизвестное действие VELIQEN Link.")
        return {"page": parts.netloc}
    if parts.scheme not in {"tel", "sms"}:
        return None
    if re.search(r"%(?![0-9a-fA-F]{2})", uri):
        raise ValueError("Некорректная кодировка ссылки.")
    if parts.netloc or parts.fragment:
        raise ValueError("Неверный формат номера в ссылке.")
    try:
        number = unquote(parts.path, errors="strict")
    except UnicodeError as exc:
        raise ValueError("Некорректная кодировка номера в ссылке.") from exc
    compact = re.sub(r"[(). \-]", "", number)
    if not re.fullmatch(r"[+0-9(). \-]{1,80}", number) or not re.fullmatch(r"\+?[0-9]+", compact):
        raise ValueError("Укажи обычный номер телефона без дополнительных параметров.")
    if parts.scheme == "tel" and parts.query:
        raise ValueError("Ссылка на звонок содержит неподдерживаемые параметры.")
    result = {"page": "calls" if parts.scheme == "tel" else "messages", "number": compact}
    if parts.scheme == "sms":
        try:
            query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=4, errors="strict")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Некорректные параметры ссылки на сообщение.") from exc
        if set(query) - {"body"} or len(query.get("body", [])) > 1:
            raise ValueError("Ссылка на сообщение содержит неподдерживаемые параметры.")
        body = query.get("body", [""])[0]
        if len(body) > MAX_BODY_LENGTH:
            raise ValueError("Текст сообщения в ссылке длиннее 4096 символов. Открой сообщения и вставь текст вручную.")
        if _invalid_character(body):
            raise ValueError("Текст сообщения в ссылке содержит управляющие символы.")
        result["body"] = body
    return result
