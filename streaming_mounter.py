"""RaiDrive-style Fast Streaming WebDAV Mounter using rclone VFS.

Allows instant video playback without downloading the whole file first,
using HTTP Range Requests and chunked streaming.
"""

import io
import os
import socket
import subprocess
import sys
import threading
import urllib.request
import zipfile
from typing import Dict, Optional, Tuple

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class StreamingMounter:
    """Manages fast chunked streaming mounts (RaiDrive-like)."""

    TOOLS_DIR = os.path.join(os.path.dirname(__file__), "tools")
    RCLONE_EXE = os.path.join(TOOLS_DIR, "rclone.exe")
    RCLONE_CONF = os.path.join(TOOLS_DIR, "rclone.conf")

    # Map of active mount processes: {drive_letter: subprocess.Popen}
    _active_mounts: Dict[str, subprocess.Popen] = {}
    _conf_lock = threading.Lock()

    @classmethod
    def is_streaming_active(cls, drive_letter: str) -> bool:
        """Checks if a background rclone process is active for this drive letter."""
        drive_letter = drive_letter.upper().rstrip("\\")
        if not drive_letter.endswith(":"):
            drive_letter += ":"
        proc = cls._active_mounts.get(drive_letter)
        return proc is not None and proc.poll() is None

    @staticmethod
    def _find_free_port() -> int:
        """Finds an available local TCP port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @classmethod
    def _write_rclone_section(cls, section_name: str, options: Dict[str, str]):
        """Safely saves or updates a remote section in rclone.conf preserving other remotes."""
        with cls._conf_lock:
            cls._refresh_paths()
            os.makedirs(cls.TOOLS_DIR, exist_ok=True)
            sections: Dict[str, Dict[str, str]] = {}
            current_section = None
            if os.path.exists(cls.RCLONE_CONF):
                try:
                    with open(cls.RCLONE_CONF, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line_s = line.strip()
                            if line_s.startswith("[") and line_s.endswith("]"):
                                current_section = line_s[1:-1]
                                sections[current_section] = {}
                            elif "=" in line_s and current_section:
                                k, v = line_s.split("=", 1)
                                sections[current_section][k.strip()] = v.strip()
                except Exception:
                    pass
            sections[section_name] = options
            out_lines = []
            for sec, opts in sections.items():
                out_lines.append(f"[{sec}]")
                for k, v in opts.items():
                    out_lines.append(f"{k} = {v}")
                out_lines.append("")

            import time
            for attempt in range(5):
                try:
                    with open(cls.RCLONE_CONF, "w", encoding="utf-8") as f:
                        f.write("\n".join(out_lines).strip() + "\n")
                    break
                except Exception:
                    time.sleep(0.05)

    @classmethod
    def get_tools_dir(cls) -> str:
        """Resolves the tools directory across frozen exe, bundle, and script environments."""
        if getattr(sys, "frozen", False):
            exe_tools = os.path.join(os.path.dirname(sys.executable), "tools")
            if os.path.exists(exe_tools):
                return exe_tools
        mei_pass = getattr(sys, "_MEIPASS", None)
        if mei_pass:
            mei_tools = os.path.join(mei_pass, "tools")
            if os.path.exists(mei_tools):
                return mei_tools
        src_tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
        if os.path.exists(src_tools):
            return src_tools
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "tools")
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def get_cache_dir(cls, drive_letter: Optional[str] = None) -> str:
        """Returns isolated VFS cache directory for the specified drive or general cache.
        
        Supports custom cache directory (e.g. D:\\Cache) to prevent C: drive space exhaustion.
        """
        try:
            from config_manager import ConfigManager
            custom_dir = ConfigManager.get_setting("custom_cache_dir", "").strip()
        except Exception:
            custom_dir = ""

        if custom_dir and os.path.exists(custom_dir):
            base_cache = os.path.join(custom_dir, "vfs_cache")
        else:
            local_app = os.environ.get("LOCALAPPDATA")
            if local_app:
                base_cache = os.path.join(local_app, "WebDAVDriveMounter", "vfs_cache")
            else:
                base_cache = os.path.join(os.path.expanduser("~"), ".webdav_mounter_cache")
        if drive_letter:
            drive_clean = drive_letter.upper().rstrip(":\\").strip()
            cache_path = os.path.join(base_cache, f"drive_{drive_clean}")
        else:
            cache_path = base_cache
        os.makedirs(cache_path, exist_ok=True)
        return cache_path

    @classmethod
    def get_cache_settings(cls) -> Tuple[str, str]:
        """Returns (vfs_cache_max_age, vfs_cache_max_size) settings."""
        try:
            from config_manager import ConfigManager
            max_age = ConfigManager.get_setting("vfs_cache_max_age", "1h")
            max_size = ConfigManager.get_setting("vfs_cache_max_size", "10G")
        except Exception:
            max_age, max_size = "1h", "10G"
        return max_age or "1h", max_size or "10G"

    @classmethod
    def purge_cache(cls, drive_letter: Optional[str] = None) -> Tuple[bool, str]:
        """Purges cached VFS files from local disk to free up C: or custom drive space."""
        import shutil
        try:
            cache_dir = cls.get_cache_dir(drive_letter)
            if not os.path.exists(cache_dir):
                return True, "삭제할 캐시 파일이 없습니다."

            freed_files = 0
            freed_bytes = 0
            for root, _, files in os.walk(cache_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        freed_bytes += os.path.getsize(fp)
                        os.remove(fp)
                        freed_files += 1
                    except Exception:
                        pass
            for root, dirs, _ in os.walk(cache_dir, topdown=False):
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except Exception:
                        pass

            mb = freed_bytes / (1024 * 1024)
            return True, f"캐시 정리 완료: {freed_files}개 파일 ({mb:.1f} MB 공간 확보됨)"
        except Exception as e:
            return False, f"캐시 정리 실패: {str(e)}"

    @classmethod
    def _refresh_paths(cls):
        """Refreshes path variables according to execution context."""
        cls.TOOLS_DIR = cls.get_tools_dir()
        cls.RCLONE_EXE = os.path.join(cls.TOOLS_DIR, "rclone.exe")
        cls.RCLONE_CONF = os.path.join(cls.TOOLS_DIR, "rclone.conf")

    @classmethod
    def is_rclone_available(cls) -> bool:
        """Checks if rclone binary exists locally or in PATH."""
        cls._refresh_paths()
        if os.path.exists(cls.RCLONE_EXE):
            try:
                if os.path.getsize(cls.RCLONE_EXE) > 5000000:
                    return True
            except Exception:
                pass
        try:
            res = subprocess.run(
                ["rclone", "version"],
                capture_output=True,
                check=False,
                timeout=3,
                creationflags=CREATE_NO_WINDOW,
            )
            return res.returncode == 0
        except Exception:
            return False

    @classmethod
    def get_rclone_path(cls) -> str:
        """Returns path to rclone executable."""
        cls._refresh_paths()
        if os.path.exists(cls.RCLONE_EXE):
            return cls.RCLONE_EXE
        return "rclone"

    @classmethod
    def download_rclone(cls, progress_callback=None) -> Tuple[bool, str]:
        """Downloads official rclone windows amd64 binary automatically."""
        cls._refresh_paths()
        os.makedirs(cls.TOOLS_DIR, exist_ok=True)
        url = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"

        try:
            if progress_callback:
                progress_callback("초고속 스트리밍 엔진 다운로드 연결 중...")

            req = urllib.request.Request(url, headers={"User-Agent": "WebDAV-Mounter/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunks = []
                chunk_size = 256 * 1024
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        if total_size > 0:
                            pct = int((downloaded / total_size) * 100)
                            d_mb = downloaded / (1024 * 1024)
                            t_mb = total_size / (1024 * 1024)
                            progress_callback(f"스트리밍 엔진 다운로드 중... ({d_mb:.1f}MB / {t_mb:.1f}MB, {pct}%)")
                        else:
                            d_mb = downloaded / (1024 * 1024)
                            progress_callback(f"스트리밍 엔진 다운로드 중... ({d_mb:.1f}MB)")
                zip_data = b"".join(chunks)

            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for member in zf.namelist():
                    if member.endswith("rclone.exe"):
                        with zf.open(member) as source, open(cls.RCLONE_EXE, "wb") as target:
                            target.write(source.read())
                        break

            if os.path.exists(cls.RCLONE_EXE):
                return True, "스트리밍 엔진 준비 완료!"
            return False, "zip 파일 내에서 rclone.exe를 찾을 수 없습니다."
        except Exception as e:
            return False, f"다운로드 실패: {str(e)}"

    @classmethod
    def _create_rclone_config(cls, profile_name: str, host: str, port: int, ssl: bool, path: str, user: str, password: str, drive_letter: str = "") -> str:
        """Generates a temporary rclone config entry for WebDAV."""
        cls._refresh_paths()
        os.makedirs(cls.TOOLS_DIR, exist_ok=True)
        from mount_manager import MountManager
        url = MountManager.format_webdav_url(host, port, ssl, path)

        # rclone uses obfuscated passwords via 'rclone obscure'
        # For simplicity, pass pass/user directly or obscure via CLI
        obscured_pass = ""
        if password:
            try:
                rclone_bin = cls.get_rclone_path()
                res = subprocess.run(
                    [rclone_bin, "obscure", password],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=CREATE_NO_WINDOW,
                )
                if res.returncode == 0:
                    obscured_pass = res.stdout.strip()
            except Exception:
                pass

        import hashlib
        tag = drive_letter.replace(":", "").strip().upper()
        h = hashlib.md5(f"{profile_name}_{host}_{port}_{path}".encode("utf-8")).hexdigest()[:6]
        remote_name = f"webdav_{tag}_{h}" if tag else f"webdav_{h}"

        options = {
            "type": "webdav",
            "url": url,
            "vendor": "other",
            "user": user,
        }
        if obscured_pass:
            options["pass"] = obscured_pass

        cls._write_rclone_section(remote_name, options)
        return remote_name

    @classmethod
    def _create_rclone_gdrive_config(
        cls,
        profile_name: str,
        token_json: str,
        client_id: str = "",
        client_secret: str = "",
        root_folder_id: str = "",
        team_drive: str = "",
    ) -> str:
        """Generates an rclone config entry for Google Drive."""
        cls._refresh_paths()
        os.makedirs(cls.TOOLS_DIR, exist_ok=True)
        import hashlib
        ascii_name = "".join(c for c in profile_name if c.isascii() and c.isalnum())
        remote_name = f"gdrive_{ascii_name}" if ascii_name else f"gdrive_{hashlib.md5(profile_name.encode('utf-8')).hexdigest()[:8]}"

        options = {
            "type": "drive",
            "scope": "drive",
        }
        if client_id:
            options["client_id"] = client_id.strip()
        if client_secret:
            options["client_secret"] = client_secret.strip()
        if root_folder_id:
            options["root_folder_id"] = root_folder_id.strip()
        if team_drive:
            options["team_drive"] = team_drive.strip()
        if token_json:
            token_clean = token_json.strip().replace("\r", "").replace("\n", " ")
            options["token"] = token_clean

        cls._write_rclone_section(remote_name, options)
        return remote_name

    @classmethod
    def authorize_gdrive(cls, client_id: str = "", client_secret: str = "") -> Tuple[bool, str, Optional[str]]:
        """Runs rclone authorize drive, opening the user's browser to authenticate Google account."""
        if not cls.is_rclone_available():
            ok, msg = cls.download_rclone()
            if not ok:
                return False, f"스트리밍 엔진 준비 실패: {msg}", None

        rclone_bin = cls.get_rclone_path()
        cmd = [rclone_bin, "authorize", "drive"]
        if client_id and client_secret:
            cmd.extend([client_id.strip(), client_secret.strip()])

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            # Wait up to 180 seconds for user to complete OAuth in browser
            stdout, stderr = proc.communicate(timeout=180)
            if proc.returncode == 0:
                import re
                match = re.search(r"\{[\s\S]*\"access_token\"[\s\S]*\}", stdout)
                if match:
                    token_str = match.group(0).strip()
                    return True, "구글 드라이브 인증이 성공적으로 완료되었습니다.", token_str
                for line in stdout.splitlines():
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}") and "access_token" in line:
                        return True, "구글 드라이브 인증이 성공적으로 완료되었습니다.", line
                return True, "인증 성공", stdout.strip()
            else:
                return False, f"인증 실패: {stderr.strip()}", None
        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            return False, "인증 시간 초과 (3분 이내에 브라우저에서 승인해 주세요)", None
        except Exception as e:
            return False, f"인증 오류: {str(e)}", None

    @classmethod
    def test_gdrive_connection(
        cls,
        token_json: str,
        client_id: str = "",
        client_secret: str = "",
        root_folder_id: str = "",
        team_drive: str = "",
    ) -> Tuple[bool, str]:
        """Tests Google Drive token connectivity via rclone."""
        if not cls.is_rclone_available():
            ok, msg = cls.download_rclone()
            if not ok:
                return False, f"스트리밍 엔진 준비 실패: {msg}"

        remote_name = cls._create_rclone_gdrive_config(
            "test_gdrive", token_json, client_id, client_secret, root_folder_id, team_drive
        )
        rclone_bin = cls.get_rclone_path()
        try:
            res = subprocess.run(
                [rclone_bin, "lsf", f"{remote_name}:", "--max-depth", "1", "--config", cls.RCLONE_CONF],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode == 0:
                lines = [l for l in res.stdout.strip().splitlines() if l.strip()]
                return True, f"구글 드라이브 연결 성공! (최상위 항목 {len(lines)}개 확인됨)"
            else:
                err = res.stderr.strip() or res.stdout.strip() or "토큰 유효성 검증 실패"
                return False, f"구글 드라이브 연결 실패: {err}"
        except Exception as e:
            return False, f"연결 테스트 중 예외: {str(e)}"

    @classmethod
    def mount_gdrive(
        cls,
        drive_letter: str,
        profile_name: str,
        token_json: str,
        client_id: str = "",
        client_secret: str = "",
        root_folder_id: str = "",
        team_drive: str = "",
        progress_callback=None,
    ) -> Tuple[bool, str]:
        """Mounts Google Drive as a Windows drive letter using high-performance VFS streaming."""
        if not drive_letter or not drive_letter.strip():
            return False, "드라이브 문자가 지정되지 않았습니다."

        drive_letter = drive_letter.upper().rstrip("\\")
        if not drive_letter.endswith(":"):
            drive_letter += ":"

        if not cls.is_rclone_available():
            ok, msg = cls.download_rclone(progress_callback)
            if not ok:
                return False, f"스트리밍 엔진 자동 다운로드 실패: {msg}"

        remote_name = cls._create_rclone_gdrive_config(
            profile_name, token_json, client_id, client_secret, root_folder_id, team_drive
        )
        rclone_bin = cls.get_rclone_path()

        # Clean any existing mount on this drive letter first
        cls.unmount_streaming(drive_letter)

        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = CREATE_NO_WINDOW

        # Ensure WinFsp driver is available; if missing, attempt automatic installation
        if not cls.is_winfsp_available():
            cls.ensure_winfsp(progress_callback)

        # Mode A: WinFsp kernel driver available -> Direct virtual filesystem mount
        if cls.is_winfsp_available():
            if progress_callback:
                progress_callback("WinFsp 초고속 드라이버 모드로 구글 드라이브 마운트 중...")

            clean_volname = "".join(c for c in profile_name if c.isalnum() or c in (" ", "_", "-"))[:20].strip()
            volname = f"GDrive ({clean_volname})" if clean_volname else f"Google Drive ({drive_letter})"
            drive_cache_dir = cls.get_cache_dir(drive_letter)
            cache_max_age, cache_max_size = cls.get_cache_settings()

            cmd = [
                rclone_bin,
                "mount",
                f"{remote_name}:",
                drive_letter,
                "--config", cls.RCLONE_CONF,
                "--cache-dir", drive_cache_dir,
                "--vfs-cache-mode", "full",
                "--vfs-read-chunk-size", "4M",
                "--vfs-read-chunk-size-limit", "2G",
                "--buffer-size", "32M",
                "--vfs-read-ahead", "32M",
                "--vfs-read-wait", "0ms",
                "--vfs-handle-caching", "0s",
                "--timeout", "10s",
                "--contimeout", "5s",
                "--low-level-retries", "3",
                "--retries", "2",
                "--drive-pacer-min-sleep", "0ms",
                "--drive-pacer-burst", "200",
                "--drive-skip-gdocs",
                "--drive-acknowledge-abuse",
                "--drive-chunk-size", "64M",
                "--vfs-fast-fingerprint",
                "--vfs-cache-max-age", cache_max_age,
                "--vfs-cache-max-size", cache_max_size,
                "--vfs-cache-poll-interval", "15s",
                "--no-modtime",
                "--no-checksum",
                "--dir-cache-time", "1h",
                "--volname", volname,
                "-o", "FileSystemName=NTFS",
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    startupinfo=startupinfo,
                    creationflags=creationflags,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                cls._active_mounts[drive_letter] = proc

                import time
                for _ in range(8):
                    time.sleep(0.3)
                    if proc.poll() is not None:
                        err = proc.stderr.read() if proc.stderr else "알 수 없는 오류"
                        return False, f"구글 드라이브 마운트 실패:\n{err}"
                    if os.path.exists(f"{drive_letter}\\"):
                        break

                return True, f"{drive_letter} 구글 드라이브로 연결되었습니다. (고용량 동영상 즉시 재생 및 초고속 탐색)"
            except Exception as e:
                return False, f"마운트 예외: {str(e)}"

        # Mode B: Zero-Driver WebDAV Gateway mode (Works on ALL Windows PCs with no admin rights / no drivers!)
        else:
            if progress_callback:
                progress_callback("구글 드라이브 고속 로컬 브리지 시작 중 (드라이버 설치 불필요)...")

            port = cls._find_free_port()
            drive_cache_dir = cls.get_cache_dir(drive_letter)
            cache_max_age, cache_max_size = cls.get_cache_settings()
            cmd = [
                rclone_bin,
                "serve",
                "webdav",
                f"{remote_name}:",
                "--addr", f"127.0.0.1:{port}",
                "--user", "gdrive",
                "--pass", "gdrive",
                "--config", cls.RCLONE_CONF,
                "--cache-dir", drive_cache_dir,
                "--vfs-cache-mode", "full",
                "--vfs-read-chunk-size", "4M",
                "--vfs-read-chunk-size-limit", "2G",
                "--buffer-size", "32M",
                "--vfs-read-ahead", "32M",
                "--vfs-read-wait", "0ms",
                "--vfs-handle-caching", "0s",
                "--vfs-fast-fingerprint",
                "--vfs-cache-max-age", cache_max_age,
                "--vfs-cache-max-size", cache_max_size,
                "--vfs-cache-poll-interval", "15s",
                "--no-modtime",
                "--no-checksum",
                "--dir-cache-time", "1h",
                "--drive-pacer-min-sleep", "0ms",
                "--drive-pacer-burst", "200",
                "--drive-skip-gdocs",
                "--drive-acknowledge-abuse",
                "--drive-chunk-size", "64M",
                "--low-level-retries", "3",
                "--retries", "2",
                "--timeout", "10s",
                "--contimeout", "5s",
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    startupinfo=startupinfo,
                    creationflags=creationflags,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                cls._active_mounts[drive_letter] = proc

                import time
                for _ in range(5):
                    time.sleep(0.2)
                    if proc.poll() is not None:
                        err = proc.stderr.read() if proc.stderr else "알 수 없는 오류"
                        return False, f"구글 드라이브 로컬 브리지 시작 실패:\n{err}"

                if progress_callback:
                    progress_callback(f"Windows 가상 드라이브 {drive_letter} 연결 중...")

                from mount_manager import MountManager
                mount_ok, mount_msg = MountManager.mount(
                    drive_letter, f"http://127.0.0.1:{port}/", "gdrive", "gdrive"
                )

                if not mount_ok:
                    cls.unmount_streaming(drive_letter)
                    return False, f"구글 드라이브 가상 드라이브 마운트 실패:\n{mount_msg}"

                return True, f"{drive_letter} 구글 드라이브로 연결되었습니다. (고용량 동영상 즉시 재생 및 초고속 탐색)"
            except Exception as e:
                cls.unmount_streaming(drive_letter)
                return False, f"마운트 예외: {str(e)}"

    @classmethod
    def is_winfsp_available(cls) -> bool:
        """Checks if WinFsp driver is installed on Windows."""
        # 1. Check known file paths
        prog_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        prog_64 = os.environ.get("ProgramFiles", r"C:\Program Files")
        for base in [prog_x86, prog_64]:
            if not base:
                continue
            if (
                os.path.exists(os.path.join(base, "WinFsp", "bin", "winfsp-x64.dll"))
                or os.path.exists(os.path.join(base, "WinFsp", "bin", "winfsp-x86.dll"))
                or os.path.exists(os.path.join(base, "WinFsp", "bin", "winfsp.dll"))
            ):
                return True

        # 2. Check Windows services
        for svc in ["WinFsp.Launcher", "winfsp", "winfsp-x64"]:
            try:
                res = subprocess.run(["sc", "query", svc], capture_output=True, check=False, creationflags=CREATE_NO_WINDOW)
                if res.returncode == 0:
                    return True
            except Exception:
                pass

        # 3. Check Windows Registry (both 64-bit and WOW6432Node)
        try:
            import winreg
            for subkey in [r"SOFTWARE\WinFsp", r"SOFTWARE\WOW6432Node\WinFsp"]:
                for flag in [0, getattr(winreg, "KEY_WOW64_32KEY", 0), getattr(winreg, "KEY_WOW64_64KEY", 0)]:
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey, 0, winreg.KEY_READ | flag):
                            return True
                    except Exception:
                        continue
        except Exception:
            pass

        return False

    @classmethod
    def ensure_winfsp(cls, progress_callback=None) -> Tuple[bool, str]:
        """Ensures WinFsp is installed, downloading and installing silently if missing."""
        if cls.is_winfsp_available():
            return True, "WinFsp 드라이버 준비됨"

        cls._refresh_paths()
        os.makedirs(cls.TOOLS_DIR, exist_ok=True)
        msi_path = os.path.join(cls.TOOLS_DIR, "winfsp.msi")
        url = "https://github.com/winfsp/winfsp/releases/download/v2.0/winfsp-2.0.23075.msi"

        try:
            # Only download if local file is missing or corrupted (< 100KB)
            if not os.path.exists(msi_path) or os.path.getsize(msi_path) < 100000:
                if progress_callback:
                    progress_callback("스트리밍 드라이버(WinFsp) 연결 중...")

                req = urllib.request.Request(url, headers={"User-Agent": "WebDAV-Mounter/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    total_size = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    chunk_size = 128 * 1024
                    with open(msi_path, "wb") as f:
                        while True:
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                if total_size > 0:
                                    pct = int((downloaded / total_size) * 100)
                                    d_mb = downloaded / (1024 * 1024)
                                    t_mb = total_size / (1024 * 1024)
                                    progress_callback(f"WinFsp 드라이버 다운로드 중... ({d_mb:.1f}MB / {t_mb:.1f}MB, {pct}%)")
                                else:
                                    d_mb = downloaded / (1024 * 1024)
                                    progress_callback(f"WinFsp 드라이버 다운로드 중... ({d_mb:.1f}MB)")

            if progress_callback:
                progress_callback("스트리밍 드라이버(WinFsp) 자동 설치 중...")

            res = subprocess.run(
                ["msiexec.exe", "/i", msi_path, "/quiet", "/qn", "/norestart"],
                capture_output=True,
                check=False,
                timeout=60,
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode == 0 or cls.is_winfsp_available():
                return True, "WinFsp 설치 완료"
            return False, f"WinFsp 설치 오류 (코드 {res.returncode})"
        except Exception as e:
            return False, f"WinFsp 설치 예외: {str(e)}"

    @classmethod
    def mount_streaming(
        cls,
        drive_letter: str = "",
        profile_name: str = "WebDAV",
        host: str = "",
        port: int = 443,
        ssl: bool = True,
        path: str = "/",
        user: str = "",
        password: str = "",
        progress_callback=None,
        **kwargs,
    ) -> Tuple[bool, str]:
        """Mounts WebDAV with chunked streaming VFS (instant video playback)."""
        if not drive_letter and "drive" in kwargs:
            drive_letter = str(kwargs.pop("drive"))
        if not user and "username" in kwargs:
            user = str(kwargs.pop("username"))

        if not drive_letter or not drive_letter.strip():
            return False, "드라이브 문자가 지정되지 않았습니다."

        drive_letter = drive_letter.upper().rstrip("\\")
        if not drive_letter.endswith(":"):
            drive_letter += ":"

        # 1. Ensure rclone is available
        if not cls.is_rclone_available():
            ok, msg = cls.download_rclone(progress_callback)
            if not ok:
                return False, f"스트리밍 엔진 자동 다운로드 실패: {msg}"

        # 2. Ensure WinFsp is available; if missing, attempt automatic silent installation
        if not cls.is_winfsp_available():
            cls.ensure_winfsp(progress_callback)

        if not cls.is_winfsp_available():
            if progress_callback:
                progress_callback("Windows 표준 WebDAV 엔진으로 자동 전환하여 연결 중...")
            from mount_manager import MountManager
            url = MountManager.format_webdav_url(host, port, ssl, path)
            return MountManager.mount(drive_letter, url, user, password)

        remote_name = cls._create_rclone_config(profile_name, host, port, ssl, path, user, password, drive_letter)
        rclone_bin = cls.get_rclone_path()

        clean_volname = "".join(c for c in profile_name if c.isalnum() or c in (" ", "_", "-"))[:20].strip()
        volname = f"WebDAV ({clean_volname})" if clean_volname else f"WebDAV ({drive_letter})"
        drive_cache_dir = cls.get_cache_dir(drive_letter)
        cache_max_age, cache_max_size = cls.get_cache_settings()

        cmd = [
            rclone_bin,
            "mount",
            f"{remote_name}:",
            drive_letter,
            "--config", cls.RCLONE_CONF,
            "--cache-dir", drive_cache_dir,
            "--vfs-cache-mode", "full",
            "--vfs-read-chunk-size", "4M",
            "--vfs-read-chunk-size-limit", "2G",
            "--buffer-size", "32M",
            "--vfs-read-ahead", "32M",
            "--vfs-read-wait", "0ms",
            "--vfs-handle-caching", "0s",
            "--timeout", "10s",
            "--contimeout", "5s",
            "--low-level-retries", "3",
            "--retries", "2",
            "--vfs-fast-fingerprint",
            "--vfs-cache-max-age", cache_max_age,
            "--vfs-cache-max-size", cache_max_size,
            "--vfs-cache-poll-interval", "15s",
            "--no-modtime",
            "--no-checksum",
            "--dir-cache-time", "1h",
            "--volname", volname,
            "-o", "FileSystemName=NTFS",
        ]

        # Hide console window
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = CREATE_NO_WINDOW

        try:
            # Terminate existing mount process on same drive if any
            cls.unmount_streaming(drive_letter)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=creationflags,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            cls._active_mounts[drive_letter] = proc

            # Verify mount initialized
            import time
            for _ in range(6):
                time.sleep(0.5)
                if proc.poll() is not None:
                    err = proc.stderr.read() if proc.stderr else "알 수 없는 오류"
                    return False, f"스트리밍 마운트 실패:\n{err}"
                if os.path.exists(f"{drive_letter}\\"):
                    break

            return True, f"{drive_letter} 초고속 스트리밍 드라이브로 연결되었습니다. (고용량 동영상 즉시 재생 및 초고속 탐색)"
        except Exception as e:
            return False, f"마운트 예외: {str(e)}"

    @classmethod
    def unmount_streaming(cls, drive_letter: str) -> bool:
        """Unmounts streaming drive process."""
        drive_letter = drive_letter.upper().rstrip("\\")
        if not drive_letter.endswith(":"):
            drive_letter += ":"

        proc = cls._active_mounts.pop(drive_letter, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass

        # Also run net use delete just in case
        from mount_manager import MountManager
        MountManager.unmount(drive_letter)
        return True

    @classmethod
    def unmount_all(cls):
        """Unmounts all active streaming drives."""
        drives = list(cls._active_mounts.keys())
        for d in drives:
            cls.unmount_streaming(d)


import atexit
atexit.register(StreamingMounter.unmount_all)

