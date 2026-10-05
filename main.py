"""WebDAV Drive Mounter Application Entry Point.

Run with GUI:
    python main.py

Run via CLI:
    python main.py list
    python main.py mount Z: https://example.com/webdav -u myuser -p mypass
    python main.py unmount Z:
    python main.py optimize
"""

import argparse
import ctypes
import sys

import io

# Ensure UTF-8 console output and smart fallback for windowed (noconsole) mode
if sys.platform == "win32":
    # When built with --noconsole, attach to calling terminal if run from CLI (cmd/powershell)
    if sys.stdout is None:
        try:
            # ATTACH_PARENT_PROCESS = -1
            if ctypes.windll.kernel32.AttachConsole(-1):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        except Exception:
            pass

    if sys.stdout is not None:
        try:
            reconfig_out = getattr(sys.stdout, "reconfigure", None)
            if callable(reconfig_out):
                reconfig_out(encoding="utf-8")
        except Exception:
            pass
    else:
        sys.stdout = io.StringIO()

    if sys.stderr is not None:
        try:
            reconfig_err = getattr(sys.stderr, "reconfigure", None)
            if callable(reconfig_err):
                reconfig_err(encoding="utf-8")
        except Exception:
            pass
    else:
        sys.stderr = io.StringIO()


def set_high_dpi():
    """Enables High DPI awareness and sets AppUserModelID for taskbar icon on Windows."""
    if sys.platform != "win32":
        return

    windll = getattr(ctypes, "windll", None)
    if not windll:
        return

    try:
        # Per-monitor DPI aware (Windows 8.1+)
        shcore = getattr(windll, "shcore", None)
        if shcore and hasattr(shcore, "SetProcessDpiAwareness"):
            shcore.SetProcessDpiAwareness(1)
        else:
            user32 = getattr(windll, "user32", None)
            if user32 and hasattr(user32, "SetProcessDPIAware"):
                user32.SetProcessDPIAware()
    except Exception:
        pass

    try:
        # Guarantees taskbar icon displays app_icon.ico cleanly
        shell32 = getattr(windll, "shell32", None)
        if shell32 and hasattr(shell32, "SetCurrentProcessExplicitAppUserModelID"):
            shell32.SetCurrentProcessExplicitAppUserModelID(
                "WebDAVDriveMounter.DesktopApp.1.0"
            )
    except Exception:
        pass



def run_gui(minimized: bool = False):
    """Launches the Tkinter GUI."""
    set_high_dpi()
    from gui import WebDAVApp

    app = WebDAVApp(start_minimized=minimized)
    try:
        app.mainloop()
    finally:
        try:
            app.unmount_all_drives()
        except Exception:
            pass


def run_cli():
    """Handles CLI arguments."""
    parser = argparse.ArgumentParser(
        description="WebDAV Drive Mounter (RaiDrive Alternative for Windows)"
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized to system tray (used for Windows boot autostart)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # GUI Command
    gui_parser = subparsers.add_parser("gui", help="Launch Graphical Interface (Default)")
    gui_parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized to system tray",
    )

    # List Drives Command
    subparsers.add_parser("list", help="List available and mounted drives")

    # Mount Command
    mount_parser = subparsers.add_parser("mount", help="Mount a WebDAV URL to drive letter")
    mount_parser.add_argument("drive", help="Drive letter (e.g. Z:)")
    mount_parser.add_argument("url", help="WebDAV URL (e.g. https://example.com/webdav)")
    mount_parser.add_argument("-u", "--user", default="", help="Username")
    mount_parser.add_argument("-p", "--password", default="", help="Password")

    # Mount Naver MYBOX Command
    mybox_parser = subparsers.add_parser("mount-mybox", help="Mount Naver MYBOX to drive letter using PAT token")
    mybox_parser.add_argument("drive", help="Drive letter (e.g. N:)")
    mybox_parser.add_argument("--token", required=True, help="Naver MYBOX Personal Access Token (PAT)")
    mybox_parser.add_argument("--streaming", action="store_true", help="Enable fast chunked streaming mode")

    # Authorize Google Drive Command
    subparsers.add_parser("authorize-gdrive", help="Open browser to authenticate Google Drive and obtain OAuth token")

    # Mount Google Drive Command
    gdrive_parser = subparsers.add_parser("mount-gdrive", help="Mount Google Drive to drive letter using OAuth token")
    gdrive_parser.add_argument("drive", help="Drive letter (e.g. G:)")
    gdrive_parser.add_argument("--token", required=True, help="Google Drive OAuth token JSON string")
    gdrive_parser.add_argument("--name", default="Google Drive", help="Remote profile name")
    gdrive_parser.add_argument("--root-folder-id", default="", help="Optional root folder ID")

    # Unmount Command
    unmount_parser = subparsers.add_parser("unmount", help="Unmount a drive letter")
    unmount_parser.add_argument("drive", help="Drive letter (e.g. Z:)")

    # Optimize Command
    subparsers.add_parser("optimize", help="Apply 4GB limit patch and WebClient start")

    args = parser.parse_args()

    if not args.command or args.command == "gui":
        is_min = getattr(args, "minimized", False)
        run_gui(minimized=is_min)
    elif args.command == "list":
        from mount_manager import MountManager
        print("=== 현재 마운트된 드라이브 ===")
        mounted = MountManager.get_mounted_drives()
        if mounted:
            for d, target in mounted.items():
                print(f"  {d} -> {target}")
        else:
            print("  (마운트된 네트워크 드라이브 없음)")

        print("\n=== 사용 가능한 드라이브 문자 ===")
        avail = MountManager.get_available_drives()
        print("  " + ", ".join(avail))

    elif args.command == "mount":
        from mount_manager import MountManager
        print(f"[*] {args.drive} -> {args.url} 마운트 시도 중...")
        ok, msg = MountManager.mount(args.drive, args.url, args.user, args.password)
        if ok:
            print(f"[+] 성공: {msg}")
        else:
            print(f"[-] 실패: {msg}")
            sys.exit(1)

    elif args.command == "mount-mybox":
        from mybox_gateway import MyBoxGatewayManager
        from streaming_mounter import StreamingMounter

        print(f"[*] {args.drive} -> 네이버 MYBOX 게이트웨이 시작 중...")
        gw_ok, gw_msg, port = MyBoxGatewayManager.start_gateway(args.drive, args.token)
        if not gw_ok:
            print(f"[-] 실패: {gw_msg}")
            sys.exit(1)
        print(f"[+] 게이트웨이 준비 완료 (127.0.0.1:{port})")

        if args.streaming:
            print(f"[*] {args.drive} 초고속 스트리밍 마운트 실행 중...")
            ok, msg = StreamingMounter.mount_streaming(
                args.drive, "MYBOX", "127.0.0.1", port, False, "/", "mybox", "token"
            )
        else:
            from mount_manager import MountManager
            print(f"[*] {args.drive} Windows WebDAV 드라이브 연결 중...")
            ok, msg = MountManager.mount(args.drive, f"http://127.0.0.1:{port}/", "mybox", "token")

        if ok:
            print(f"[+] 성공: {msg}")
        else:
            print(f"[-] 실패: {msg}")
            sys.exit(1)

    elif args.command == "authorize-gdrive":
        from streaming_mounter import StreamingMounter
        print("[*] 웹 브라우저를 열어 구글 계정 인증을 진행합니다...")
        print("[*] 브라우저에서 'rclone' 액세스 요청을 허용해 주세요.")
        ok, msg, token = StreamingMounter.authorize_gdrive()
        if ok and token:
            print(f"[+] {msg}")
            print("\n=== 발급된 구글 드라이브 OAuth 토큰 ===")
            print(token)
            print("=========================================")
        else:
            print(f"[-] 인증 실패: {msg}")
            sys.exit(1)

    elif args.command == "mount-gdrive":
        from streaming_mounter import StreamingMounter
        print(f"[*] {args.drive} -> Google Drive 초고속 스트리밍 마운트 실행 중...")
        ok, msg = StreamingMounter.mount_gdrive(
            drive_letter=args.drive,
            profile_name=args.name,
            token_json=args.token,
            root_folder_id=args.root_folder_id,
        )
        if ok:
            print(f"[+] 성공: {msg}")
        else:
            print(f"[-] 실패: {msg}")
            sys.exit(1)

    elif args.command == "unmount":
        from mount_manager import MountManager
        print(f"[*] {args.drive} 연결 해제 중...")
        try:
            from streaming_mounter import StreamingMounter
            StreamingMounter.unmount_streaming(args.drive)
        except Exception:
            pass
        try:
            from mybox_gateway import MyBoxGatewayManager
            MyBoxGatewayManager.stop_gateway(args.drive)
        except Exception:
            pass
        ok, msg = MountManager.unmount(args.drive)
        if ok:
            print(f"[+] 성공: {msg}")
        else:
            print(f"[-] 실패: {msg}")
            sys.exit(1)

    elif args.command == "optimize":
        from mount_manager import MountManager
        print("[*] WebDAV 레지스트리 및 WebClient 서비스 최적화 실행 중...")
        MountManager.ensure_webclient_service()
        ok, msg = MountManager.optimize_registry_settings()
        if ok:
            print(f"[+] 성공: {msg}")
        else:
            print(f"[-] 실패: {msg}")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    run_cli()

