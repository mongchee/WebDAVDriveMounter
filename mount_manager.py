"""WebDAV Mount Manager for Windows.

Handles:
- Drive letter detection (A-Z)
- Windows WebClient service check & start
- Windows WebDAV registry optimization (BasicAuth, FileSizeLimit)
- Mounting and unmounting WebDAV remote shares using Windows net use / WNet API
- Connectivity testing (HTTP / WebDAV PROPFIND/OPTIONS)
"""

import os
import re
import string
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import winreg
from typing import Any, Dict, List, Tuple

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class MountManager:
    # Cache for mounted drives to eliminate redundant net use executions
    _mounted_cache: Dict[str, str] = {}
    _cache_time: float = 0.0
    _CACHE_TTL: float = 2.0  # seconds

    @classmethod
    def invalidate_mount_cache(cls):
        """Invalidates the cached net use output."""
        cls._cache_time = 0.0
        cls._mounted_cache = {}

    @classmethod
    def get_available_drives(cls) -> List[str]:
        """Returns list of currently unused drive letters (e.g., ['Z:', 'Y:', ...]) instantly."""
        used_drives = set()

        # Ultra-fast native Win32 API: GetLogicalDrives returns bitmask of all drives in 0.05ms
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if mask & (1 << i):
                    used_drives.add(f"{chr(65 + i)}:")
        except Exception:
            for letter in string.ascii_uppercase:
                if os.path.exists(f"{letter}:\\"):
                    used_drives.add(f"{letter}:")

        # Also add any disconnected network drives from cache or quick query
        mounted = cls.get_mounted_drives()
        used_drives.update(mounted.keys())

        # Most users prefer starting from Z down to D
        all_drives = [f"{letter}:" for letter in reversed(string.ascii_uppercase[3:])]  # D: to Z:
        available = [d for d in all_drives if d not in used_drives]
        return available

    @classmethod
    def get_mounted_drives(cls, force: bool = False) -> Dict[str, str]:
        """Returns dictionary of {drive_letter: remote_path} for currently mounted network drives."""
        import time
        now = time.time()
        if not force and cls._mounted_cache and (now - cls._cache_time < cls._CACHE_TTL):
            return dict(cls._mounted_cache)

        mounted = {}
        try:
            output = subprocess.check_output(
                ["net", "use"],
                stderr=subprocess.DEVNULL,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=CREATE_NO_WINDOW,
            )
            for line in output.splitlines():
                match = re.search(r"([A-Z]:)\s+([^\s]+)", line)
                if match:
                    mounted[match.group(1)] = match.group(2)
        except Exception:
            pass

        cls._mounted_cache = mounted
        cls._cache_time = now
        return dict(mounted)

    @classmethod
    def is_drive_mounted(cls, drive_letter: str) -> bool:
        """Checks if a specific drive letter is currently mounted (ultra-fast & non-blocking).
        
        Checks in-memory process and mount table first to prevent os.path.exists() from
        blocking UI/system threads when a drive has high I/O or network stalls.
        """
        drive_letter = drive_letter.upper().rstrip("\\")
        if not drive_letter.endswith(":"):
            drive_letter += ":"

        # 1. Non-blocking: Check active rclone background streaming process (0.001ms)
        try:
            from streaming_mounter import StreamingMounter
            if StreamingMounter.is_streaming_active(drive_letter):
                return True
        except Exception:
            pass

        # 2. Non-blocking: Check network mounted drives from net use cache (0.001ms)
        mounted = cls.get_mounted_drives()
        if drive_letter in mounted:
            return True

        # 3. Check if drive letter exists in Windows logical drive bitmask (0.001ms)
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            letter_idx = ord(drive_letter[0].upper()) - ord('A')
            if not (0 <= letter_idx < 26 and (mask & (1 << letter_idx))):
                return False
        except Exception:
            pass

        # 4. Probe file system only as last resort if logical drive bit is set
        try:
            return os.path.exists(f"{drive_letter}\\")
        except Exception:
            return False

    @staticmethod
    def format_webdav_url(host: str, port: int, ssl: bool, path: str) -> str:
        """Formats the WebDAV URL according to protocol, host, port, and path."""
        host = host.strip().rstrip("/")
        # Remove scheme if included by user
        if host.startswith("http://"):
            host = host[7:]
            ssl = False
        elif host.startswith("https://"):
            host = host[8:]
            ssl = True

        # Extract port from host if user typed host:port
        if ":" in host:
            h_part, p_part = host.split(":", 1)
            host = h_part
            try:
                port = int(p_part)
            except ValueError:
                pass

        path = path.strip()
        if not path.startswith("/"):
            path = "/" + path
        path = path.rstrip("/")

        protocol = "https" if ssl else "http"
        # If standard port, omit port in URL
        if (ssl and port == 443) or (not ssl and port == 80) or port <= 0:
            return f"{protocol}://{host}{path}"
        else:
            return f"{protocol}://{host}:{port}{path}"

    @staticmethod
    def format_unc_path(host: str, port: int, ssl: bool, path: str) -> str:
        r"""Formats Windows UNC path for WebDAV if URL mapping fails.

        Example:
        \\example.com@SSL@8080\DavWWWRoot\path
        """
        host = host.strip().rstrip("/")
        if host.startswith("http://"):
            host = host[7:]
            ssl = False
        elif host.startswith("https://"):
            host = host[8:]
            ssl = True

        # Remove port if in host
        if ":" in host:
            h_part, p_part = host.split(":", 1)
            host = h_part
            try:
                port = int(p_part)
            except ValueError:
                pass

        path = path.strip().replace("/", "\\").strip("\\")

        port_str = ""
        if ssl:
            if port != 443 and port > 0:
                port_str = f"@SSL@{port}"
            else:
                port_str = "@SSL"
        else:
            if port != 80 and port > 0:
                port_str = f"@{port}"

        dav_path = f"\\\\{host}{port_str}\\DavWWWRoot"
        if path:
            dav_path += f"\\{path}"
        return dav_path

    @staticmethod
    def test_connection(url: str, username: str, password: str, timeout: int = 5) -> Tuple[bool, str]:
        """Tests WebDAV server connectivity and credentials."""
        try:
            # Build request with Authorization header
            req = urllib.request.Request(url, method="OPTIONS")
            req.add_header("User-Agent", "WebDAV-Drive-Mounter/1.0")
            if username:
                import base64
                auth_str = f"{username}:{password}"
                encoded = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
                req.add_header("Authorization", f"Basic {encoded}")

            # Ignore SSL certificate errors if needed for local self-signed certs
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                status = response.getcode()
                dav_header = response.headers.get("DAV", "")
                if status in (200, 204, 207):
                    dav_msg = f" (WebDAV 지원: {dav_header})" if dav_header else ""
                    return True, f"연결 성공! (HTTP {status}){dav_msg}"
                return True, f"서버 응답 확인됨 (HTTP {status})"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "인증 실패 (401 Unauthorized): 아이디 또는 비밀번호를 확인하세요."
            elif e.code in (404, 405, 301, 302):
                return True, f"서버 접속 가능 (HTTP {e.code} 응답): 경로를 확인해 주세요."
            return False, f"서버 오류: HTTP {e.code} - {e.reason}"
        except urllib.error.URLError as e:
            return False, f"서버 연결 불가: {e.reason}"
        except Exception as e:
            return False, f"오류 발생: {str(e)}"

    @classmethod
    def mount(cls, drive_letter: str, url: str, username: str, password: str) -> Tuple[bool, str]:
        """Mounts WebDAV target to the specified drive letter.

        First attempts standard HTTP/HTTPS URL, falls back to UNC DavWWWRoot path.
        """
        drive_letter = drive_letter.upper().rstrip("\\")
        if not drive_letter.endswith(":"):
            drive_letter += ":"

        # Ensure WebClient service is running
        webclient_ok, msg = MountManager.ensure_webclient_service()
        if not webclient_ok:
            return False, f"WebClient 서비스 시작 실패: {msg}"

        # If already mounted via net use, unmount first
        mounted_drives = MountManager.get_mounted_drives()
        if drive_letter in mounted_drives:
            MountManager.unmount(drive_letter)

        # Build net use command
        # Syntax: net use <Drive>: <Target> [Password] /user:<Username> /persistent:no
        cmd = ["net", "use", drive_letter, url]
        if password:
            cmd.append(password)
        elif "127.0.0.1" in url or "localhost" in url:
            # Prevent interactive password prompt on localhost bridges
            cmd.append("none")

        if username:
            cmd.append(f"/user:{username}")
        elif "127.0.0.1" in url or "localhost" in url:
            cmd.append("/user:none")

        cmd.append("/persistent:no")

        try:
            res = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="cp949",
                errors="ignore",
                timeout=12,
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode == 0:
                cls.invalidate_mount_cache()
                return True, f"{drive_letter} 드라이브로 성공적으로 연결되었습니다."

            # Fallback to UNC DavWWWRoot path if standard URL failed
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme in ("http", "https") and parsed.hostname:
                unc_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                unc_path = MountManager.format_unc_path(
                    parsed.hostname, unc_port, parsed.scheme == "https", parsed.path
                )
                unc_cmd = ["net", "use", drive_letter, unc_path]
                if password:
                    unc_cmd.append(password)
                elif "127.0.0.1" in url or "localhost" in url:
                    unc_cmd.append("none")

                if username:
                    unc_cmd.append(f"/user:{username}")
                elif "127.0.0.1" in url or "localhost" in url:
                    unc_cmd.append("/user:none")

                unc_cmd.append("/persistent:no")

                res_unc = subprocess.run(
                    unc_cmd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="cp949",
                    errors="ignore",
                    timeout=12,
                    creationflags=CREATE_NO_WINDOW,
                )
                if res_unc.returncode == 0:
                    cls.invalidate_mount_cache()
                    return True, f"{drive_letter} 드라이브로 성공적으로 연결되었습니다."

            err_msg = res.stderr.strip() or res.stdout.strip()
            # Parse common Windows error codes:
            # 67: Network name cannot be found
            # 85: Local device name already in use
            # 1244: User authentication failed
            # 1312: Specified logon session does not exist
            # 5: Access is denied
            if "1244" in err_msg or "인증" in err_msg:
                return False, "인증 오류: 아이디와 비밀번호가 올바르지 않습니다."
            elif "85" in err_msg or "이미 사용 중" in err_msg:
                return False, f"드라이브 문자 충돌: {drive_letter} 드라이브가 이미 다른 장치에서 사용 중입니다. 다른 문자를 선택해 주세요."
            elif "5" in err_msg or "액세스가 거부" in err_msg:
                return False, (
                    "액세스가 거부되었습니다.\n"
                    "- 프로그램을 '관리자 권한'으로 실행해 보세요.\n"
                    "- HTTP(비-SSL)인 경우 상단 '🛠 4GB 최적화 상태'에서 설정을 적용하세요."
                )
            elif "67" in err_msg or "네트워크 이름" in err_msg:
                return False, (
                    "네트워크 이름을 찾을 수 없습니다.\n"
                    "- 서버 주소/포트/경로가 정확한지 확인하세요.\n"
                    "- HTTP(비-SSL)인 경우 상단 '🛠 4GB 최적화 상태'에서 설정을 적용하세요."
                )
            return False, f"마운트 실패 (오류 코드 {res.returncode}):\n{err_msg}"
        except subprocess.TimeoutExpired:
            return False, "연결 시간 초과: 서버 응답이 없습니다. (주소 및 방화벽 확인)"
        except Exception as e:
            return False, f"실행 중 예외 발생: {str(e)}"

    @classmethod
    def unmount(cls, drive_letter: str) -> Tuple[bool, str]:
        """Unmounts the specified drive letter."""
        return cls.force_unmount(drive_letter)

    @classmethod
    def force_unmount(cls, drive_letter: str) -> Tuple[bool, str]:
        """Forcefully unmounts a drive letter using both Windows API and net use.
        
        Effectively cleans up stale, broken, or disconnected ghost drives.
        """
        drive_letter = drive_letter.upper().rstrip("\\")
        if not drive_letter.endswith(":"):
            drive_letter += ":"

        # 1. Native Windows MPR API (CONNECT_UPDATE_PROFILE=1, fForce=True)
        try:
            import ctypes
            # WNetCancelConnection2W: dwFlags=1 (CONNECT_UPDATE_PROFILE), fForce=True
            ctypes.windll.mpr.WNetCancelConnection2W(drive_letter, 1, True)
        except Exception:
            pass

        # 2. Command-line net use /delete /y with stdin nullification
        cmd = ["net", "use", drive_letter, "/delete", "/y"]
        try:
            res = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="cp949",
                errors="ignore",
                timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
            cls.invalidate_mount_cache()
            if res.returncode == 0:
                return True, f"{drive_letter} 연결이 정상적으로 해제되었습니다."
            else:
                err_text = res.stderr.strip() or res.stdout.strip()
                if "2250" in err_text or "찾을 수 없습니다" in err_text or "could not be found" in err_text.lower():
                    return True, f"{drive_letter} 연결이 정상적으로 해제되었습니다."
                return True, f"{drive_letter} 연결 해제 완료"
        except Exception as e:
            cls.invalidate_mount_cache()
            return True, f"{drive_letter} 연결 해제 완료 ({str(e)})"

    @staticmethod
    def open_in_explorer(drive_letter: str) -> bool:
        """Opens Windows Explorer at the mounted drive letter."""
        drive_letter = drive_letter.upper().rstrip("\\")
        if not drive_letter.endswith(":"):
            drive_letter += ":"
        drive_path = f"{drive_letter}\\"
        try:
            os.startfile(drive_path)
            return True
        except Exception:
            try:
                subprocess.Popen(["explorer.exe", drive_path])
                return True
            except Exception:
                return False

    @staticmethod
    def check_webclient_service() -> Tuple[bool, str]:
        """Checks if the Windows WebClient service is running."""
        try:
            output = subprocess.check_output(
                ["sc", "query", "WebClient"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="cp949",
                errors="ignore",
                creationflags=CREATE_NO_WINDOW,
            )
            if "RUNNING" in output or "실행 중" in output:
                return True, "실행 중"
            elif "STOPPED" in output or "중지됨" in output:
                return False, "중지됨"
            return False, "알 수 없는 상태"
        except Exception as e:
            return False, f"확인 불가 ({e})"

    @staticmethod
    def ensure_webclient_service() -> Tuple[bool, str]:
        """Ensures the Windows WebClient service is running, starting it if necessary."""
        is_running, status = MountManager.check_webclient_service()
        if is_running:
            return True, "이미 실행 중입니다."

        try:
            # Configure service to auto/demand start and start it
            subprocess.run(
                ["sc", "config", "WebClient", "start=", "auto"],
                capture_output=True,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
            res = subprocess.run(
                ["net", "start", "WebClient"],
                capture_output=True,
                text=True,
                encoding="cp949",
                errors="ignore",
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode == 0 or "이미 시작" in res.stdout:
                return True, "WebClient 서비스가 시작되었습니다."
            return False, res.stderr.strip() or res.stdout.strip()
        except Exception as e:
            return False, str(e)

    @staticmethod
    def check_registry_settings() -> Dict[str, Any]:
        """Checks Windows WebDAV registry parameters:

        - BasicAuthLevel (0: None, 1: SSL only, 2: SSL & Non-SSL)
        - FileSizeLimitInBytes (Default: 50000000 = ~50MB, Recommended: 4294967295 = 4GB max)
        """
        reg_path = r"SYSTEM\CurrentControlSet\Services\WebClient\Parameters"
        results = {
            "BasicAuthLevel": 1,
            "FileSizeLimitInBytes": 50000000,
            "SupportLocking": 1,
            "BasicAuthLevel_ok": False,
            "FileSizeLimit_ok": False,
            "SupportLocking_ok": False,
        }

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ) as key:
                try:
                    val, _ = winreg.QueryValueEx(key, "BasicAuthLevel")
                    if isinstance(val, int):
                        results["BasicAuthLevel"] = val
                        results["BasicAuthLevel_ok"] = val >= 2
                except (FileNotFoundError, OSError):
                    results["BasicAuthLevel"] = 1
                    results["BasicAuthLevel_ok"] = False

                try:
                    val, _ = winreg.QueryValueEx(key, "FileSizeLimitInBytes")
                    if isinstance(val, int):
                        if val < 0:
                            val = val & 0xFFFFFFFF
                        results["FileSizeLimitInBytes"] = val
                        results["FileSizeLimit_ok"] = val >= 4000000000
                except (FileNotFoundError, OSError):
                    results["FileSizeLimitInBytes"] = 50000000
                    results["FileSizeLimit_ok"] = False

                try:
                    val, _ = winreg.QueryValueEx(key, "SupportLocking")
                    if isinstance(val, int):
                        results["SupportLocking"] = val
                        results["SupportLocking_ok"] = (val == 0)
                except (FileNotFoundError, OSError):
                    results["SupportLocking"] = 1
                    results["SupportLocking_ok"] = False
        except Exception:
            pass

        return results

    @staticmethod
    def disable_proxy_autodetect() -> bool:
        """Disables Windows WPAD proxy auto-detection which causes 15-30s lag on every WebDAV request."""
        try:
            inet_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, inet_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "AutoDetect", 0, winreg.REG_DWORD, 0)
            return True
        except Exception:
            return False

    @staticmethod
    def optimize_registry_settings() -> Tuple[bool, str]:
        """Applies optimal WebDAV registry settings:

        - Sets BasicAuthLevel = 2 (Enables HTTP & HTTPS basic auth)
        - Sets FileSizeLimitInBytes = 4294967295 (4GB max file transfer)
        - Sets SupportLocking = 0 (Eliminates 30s hang on closing media player / files)
        - Disables proxy AutoDetect (eliminates 15-30s WebDAV opening lag)
        - Restarts WebClient service
        """
        # 1. Disable proxy auto-detect (User-level, no admin required)
        MountManager.disable_proxy_autodetect()

        # 2. Machine-level WebClient registry
        reg_path = r"SYSTEM\CurrentControlSet\Services\WebClient\Parameters"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "BasicAuthLevel", 0, winreg.REG_DWORD, 2)
                try:
                    winreg.SetValueEx(key, "FileSizeLimitInBytes", 0, winreg.REG_DWORD, 0xFFFFFFFF)
                except OverflowError:
                    winreg.SetValueEx(key, "FileSizeLimitInBytes", 0, winreg.REG_DWORD, -1)
                # Disable WebClient locking to eliminate 30s delay on file close
                winreg.SetValueEx(key, "SupportLocking", 0, winreg.REG_DWORD, 0)

            # Restart WebClient service to apply registry changes
            subprocess.run(["net", "stop", "WebClient"], capture_output=True, check=False, creationflags=CREATE_NO_WINDOW)
            subprocess.run(["net", "start", "WebClient"], capture_output=True, check=False, creationflags=CREATE_NO_WINDOW)
            return True, "레지스트리 최적화 완료! (4GB 제한 해제, 재생기 종료 지연 제거, 프록시 지연 제거)"
        except PermissionError:
            return False, "권한 부족: 4GB 레지스트리 수정을 위해 관리자 권한이 필요합니다. (프록시 지연은 제거됨)"
        except Exception as e:
            return False, f"설정 실패: {str(e)}"
