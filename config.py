import os
import json
from dataclasses import dataclass, asdict, field

@dataclass
class AppConfig:
    bulb_ips: list[str] = field(default_factory=list)
    fps: int = 40
    brightness: int = 100
    saturation: int = 14
    smoothness: int = 60
    gamma: int = 10
    kelvin: int = 6500
    mode: str = "Dominant"
    monitor_idx: int = 1
    # Multiple Schedules
    schedules: list = field(default_factory=list)
    # Static Color feature
    static_color: str = "#ffffff"
    effect: str = "None"

class ConfigManager:
    def __init__(self, filepath="ambienz_config.json"):
        self.filepath = filepath
        self.config = AppConfig()
        self.load()

    def load(self):
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath, "r") as f:
                data = json.load(f)
                # Filter out keys that are not in AppConfig to avoid TypeError
                valid_keys = {k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__}
                self.config = AppConfig(**valid_keys)
        except Exception as e:
            print(f"[Config] load error: {e}")

    def save(self):
        try:
            with open(self.filepath, "w") as f:
                json.dump(asdict(self.config), f, indent=2)
        except Exception as e:
            print(f"[Config] save error: {e}")
