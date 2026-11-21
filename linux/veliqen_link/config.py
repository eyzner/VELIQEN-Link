"""Small, private, atomic user configuration. Never stores messages or clipboard."""
import json
import os
from pathlib import Path
import tempfile

DEFAULTS = {
    "device_id": "", "adb_serial": "", "bluetooth_address": "",
    "microphone": "", "speaker": "", "clipboard_sync": False,
    "close_to_background": True, "max_size": 1600,
    "bluetooth_devices": {}, "auto_audio": True,
    "appearance": "system",
}

class Config:
    def __init__(self, directory=None):
        self.directory = Path(directory or Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config"))/"veliqen-link")
        self.path = self.directory / "settings.json"
        self.data = dict(DEFAULTS)
        self.warning = ""
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("object expected")
            for key, default in DEFAULTS.items():
                candidate = value.get(key, default)
                if type(candidate) is type(default):
                    self.data[key] = candidate
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.warning = "Настройки не удалось прочитать. Используются исходные значения."

    def get(self, key):
        return self.data[key]

    def set(self, **values):
        for key, value in values.items():
            if key not in DEFAULTS or type(value) is not type(DEFAULTS[key]):
                raise ValueError("Invalid configuration field")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        updated = dict(self.data, **values)
        fd, filename = tempfile.mkstemp(prefix=".settings-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(updated, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(filename, 0o600)
            os.replace(filename, self.path)
            self.data = updated
        finally:
            if os.path.exists(filename):
                os.unlink(filename)
