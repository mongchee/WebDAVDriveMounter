"""Windows Auto-start (Run on Boot) Manager using Windows Registry."""

import os
import sys
import winreg
from typing import Tuple

APP_NAME = "WebDAVDriveMounter"
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


class AutostartManager:
    """Manages Windows startup registry entry for the application."""

    @staticmethod
    def get_run_command() -> str:
        """Constructs the launch command with --minimized flag."""
        if getattr(sys, "frozen", False):
            # When compiled as an executable (PyInstaller)
            return f'"{sys.executable}" --minimized'

        # When running from source script
        exe_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(exe_dir, "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable

        main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
        return f'"{pythonw}" "{main_py}" --minimized'

    @staticmethod
    def is_autostart_enabled() -> bool:
        """Checks if WebDAVDriveMounter is registered in Windows startup registry."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ) as key:
                try:
                    val, _ = winreg.QueryValueEx(key, APP_NAME)
                    return bool(val)
                except FileNotFoundError:
                    return False
        except Exception:
            return False

    @staticmethod
    def set_autostart(enabled: bool) -> Tuple[bool, str]:
        """Enables or disables auto-start at Windows boot."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_ALL_ACCESS) as key:
                if enabled:
                    cmd = AutostartManager.get_run_command()
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                    return True, "윈도우 시작 시 자동 실행이 활성화되었습니다."
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
                    return True, "윈도우 시작 시 자동 실행이 비활성화되었습니다."
        except Exception as e:
            return False, f"레지스트리 변경 오류: {str(e)}"
