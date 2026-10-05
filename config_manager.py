"""Configuration and profile manager for WebDAV Drive Mounter."""

import base64
import json
import os
import uuid
import sys
from typing import Any, Dict, List


class ConfigManager:
    """Manages user profiles and application settings."""

    BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    CONFIG_FILE = os.path.join(BASE_DIR, "profiles.json")

    @staticmethod
    def _encode_password(password: str) -> str:
        """Obfuscates password for storage."""
        if not password:
            return ""
        # XOR with fixed key + base64 encoding to avoid plain-text storage
        key = 0x5A
        xored = bytes([b ^ key for b in password.encode("utf-8")])
        return base64.b64encode(xored).decode("ascii")

    @staticmethod
    def _decode_password(encoded: str) -> str:
        """Decodes obfuscated password."""
        if not encoded:
            return ""
        try:
            raw = base64.b64decode(encoded.encode("ascii"))
            key = 0x5A
            return bytes([b ^ key for b in raw]).decode("utf-8")
        except Exception:
            return ""

    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        """Loads all profiles and settings from json."""
        default_config = {
            "settings": {
                "start_minimized": False,
                "auto_mount_on_startup": False,
                "last_selected_profile": None,
                "vfs_cache_max_age": "1h",
                "vfs_cache_max_size": "10G",
                "custom_cache_dir": "",
            },
            "profiles": [],
        }

        if not os.path.exists(cls.CONFIG_FILE):
            return default_config

        try:
            with open(cls.CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Ensure schema
            if "profiles" not in data:
                data["profiles"] = []
            if "settings" not in data:
                data["settings"] = default_config["settings"]

            # Decode passwords and tokens
            for p in data["profiles"]:
                if "password" in p:
                    p["password"] = cls._decode_password(p.get("password", ""))
                if "mybox_token" in p:
                    p["mybox_token"] = cls._decode_password(p.get("mybox_token", ""))
                if "gdrive_token" in p:
                    p["gdrive_token"] = cls._decode_password(p.get("gdrive_token", ""))
                if "storage_type" not in p:
                    p["storage_type"] = "webdav"

            return data
        except Exception:
            return default_config

    @classmethod
    def save_config(cls, data: Dict[str, Any]) -> bool:
        """Saves profiles and settings to json."""
        try:
            # Create a deep copy to encode passwords and tokens without affecting memory state
            save_data = {
                "settings": data.get("settings", {}),
                "profiles": [],
            }
            for p in data.get("profiles", []):
                p_copy = dict(p)
                p_copy["password"] = cls._encode_password(p.get("password", ""))
                if "mybox_token" in p_copy:
                    p_copy["mybox_token"] = cls._encode_password(p_copy.get("mybox_token", ""))
                if "gdrive_token" in p_copy:
                    p_copy["gdrive_token"] = cls._encode_password(p_copy.get("gdrive_token", ""))
                if "storage_type" not in p_copy:
                    p_copy["storage_type"] = "webdav"
                save_data["profiles"].append(p_copy)

            with open(cls.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @classmethod
    def get_profiles(cls) -> List[Dict[str, Any]]:
        """Returns list of all saved profiles."""
        return cls.load_config().get("profiles", [])

    @classmethod
    def save_profile(cls, profile: Dict[str, Any]) -> str:
        """Creates or updates a profile."""
        config = cls.load_config()
        profiles = config.get("profiles", [])

        if "id" not in profile or not profile["id"]:
            profile["id"] = str(uuid.uuid4())[:8]
            profiles.append(profile)
        else:
            # Update existing
            found = False
            for i, p in enumerate(profiles):
                if p.get("id") == profile["id"]:
                    profiles[i] = profile
                    found = True
                    break
            if not found:
                profiles.append(profile)

        config["profiles"] = profiles
        cls.save_config(config)
        return profile["id"]

    @classmethod
    def delete_profile(cls, profile_id: str) -> bool:
        """Deletes a profile by ID."""
        config = cls.load_config()
        profiles = [p for p in config.get("profiles", []) if p.get("id") != profile_id]
        config["profiles"] = profiles
        return cls.save_config(config)

    @classmethod
    def get_setting(cls, key: str, default: Any = None) -> Any:
        config = cls.load_config()
        return config.get("settings", {}).get(key, default)

    @classmethod
    def set_setting(cls, key: str, value: Any) -> None:
        config = cls.load_config()
        if "settings" not in config:
            config["settings"] = {}
        config["settings"][key] = value
        cls.save_config(config)
