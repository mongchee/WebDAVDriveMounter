import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional
import webbrowser

from autostart_manager import AutostartManager
from config_manager import ConfigManager
from mount_manager import MountManager
from mybox_client import MyBoxClient
from mybox_gateway import MyBoxGatewayManager
from streaming_mounter import StreamingMounter
from tray_manager import TrayManager


class WebDAVApp(tk.Tk):
    """Main Application Window."""

    # Modern RaiDrive Clean White / Light Theme
    BG_MAIN = "#f5f6f8"       # Window background
    BG_SIDEBAR = "#ffffff"    # Sidebar background
    BG_CARD = "#ffffff"       # Cards and panels
    BG_INPUT = "#fafbfc"      # Text inputs
    TEXT_MAIN = "#2d3748"     # Main text (dark slate, crisp)
    TEXT_MUTED = "#718096"    # Subtext / labels
    ACCENT_BLUE = "#1a73e8"   # RaiDrive signature blue
    ACCENT_HOVER = "#1557b0"  # Blue hover
    ACCENT_GREEN = "#059669"  # Mounted green
    ACCENT_RED = "#e53e3e"    # Unmount / delete red
    BORDER_LIGHT = "#e2e8f0"  # Crisp light border
    BORDER_INPUT = "#cbd5e1"  # Input border
    BADGE_DISCONNECTED = "#fef2f2"
    BADGE_CONNECTED = "#ecfdf5"

    # Sleek normal-weight Korean & English typography
    FONT_TITLE = ("맑은 고딕", 12, "normal")
    FONT_CARD_TITLE = ("맑은 고딕", 11, "normal")
    FONT_LABEL = ("맑은 고딕", 9, "normal")
    FONT_INPUT = ("맑은 고딕", 9, "normal")
    FONT_BUTTON = ("맑은 고딕", 9, "normal")
    FONT_SMALL = ("맑은 고딕", 8, "normal")

    def __init__(self, start_minimized: bool = False):
        super().__init__()
        self.title("WebDAV Drive Mounter")
        # Apply window and taskbar icon if available
        base_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "app_icon.ico")
        mei_pass = getattr(sys, "_MEIPASS", None)
        if not os.path.exists(icon_path) and mei_pass:
            icon_path = os.path.join(mei_pass, "app_icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        self.geometry("960x800")
        self.minsize(880, 680)
        self.configure(bg=self.BG_MAIN)

        # State
        self.current_profile_id: Optional[str] = None
        self.profiles: List[Dict[str, Any]] = []
        self.is_busy = False

        self._setup_styles()
        self._build_layout()

        # Render saved profile list immediately using cached/fast drive detection
        self.refresh_profiles_list()

        # Force immediate window render (First Paint within 0.1s)
        self.update_idletasks()

        # Register process exit hook to guarantee cleanup under all termination paths
        import atexit
        atexit.register(self.unmount_all_drives)

        # Intercept window close (X) to minimize to system tray
        self.protocol("WM_DELETE_WINDOW", self.on_close_to_tray)

        # Tray holder
        self.tray: Optional[TrayManager] = None

        # If started with --minimized flag (e.g. at Windows boot), hide immediately
        if start_minimized:
            self.withdraw()

        # Defer background initialization by 30ms to guarantee instant window appearance
        self.after(30, self._deferred_startup_tasks)

    def _deferred_startup_tasks(self):
        """Initializes tray, cleanup, 4GB patch, and auto-mount without blocking UI display."""
        # 1. Initialize System Tray Icon
        self.tray = TrayManager(
            on_show=self.restore_from_tray,
            on_exit=self.quit_completely,
            on_open_explorer=self.open_explorer,
        )
        self.tray.start()

        # 2. Clean up any broken/orphan drives from previous unclean shutdowns in background
        threading.Thread(target=self._cleanup_stale_mounts_on_startup, daemon=True).start()

        # 3. Automatically apply 4GB optimization and WebClient service in background
        threading.Thread(target=self._auto_apply_4gb_optimization, daemon=True).start()

        # 4. Automatically connect all saved storage profiles in background
        threading.Thread(target=self._auto_mount_profiles, daemon=True).start()

        # 5. Start periodic background check to keep drive connection status in sync
        self._schedule_status_sync()

    def _schedule_status_sync(self):
        """Silently syncs mount status badges periodically."""
        if not getattr(self, "is_busy", False):
            try:
                self._update_mount_status_ui()
            except Exception:
                pass
        self.after(8000, self._schedule_status_sync)

    def _setup_styles(self):
        """Configures ttk styles for modern clean look."""
        self.style = ttk.Style(self)
        self.style.theme_use("clam")

        # Scrollbar styling
        self.style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background="#e2e8f0",
            darkcolor="#cbd5e1",
            lightcolor="#ffffff",
            troughcolor="#f8f9fa",
            bordercolor="#e2e8f0",
            arrowcolor=self.TEXT_MUTED,
        )

        # Combobox styling
        self.style.configure(
            "TCombobox",
            fieldbackground=self.BG_INPUT,
            background="#ffffff",
            foreground=self.TEXT_MAIN,
            arrowcolor=self.ACCENT_BLUE,
            bordercolor=self.BORDER_INPUT,
            lightcolor=self.BORDER_INPUT,
            darkcolor=self.BORDER_INPUT,
            font=self.FONT_INPUT,
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.BG_INPUT)],
            selectbackground=[("readonly", self.ACCENT_BLUE)],
            selectforeground=[("readonly", "#ffffff")],
        )

    def safe_after(self, delay: int, callback):
        """Thread-safe and destruction-safe wrapper around self.after."""
        try:
            if self.winfo_exists():
                return super().after(delay, callback)
        except Exception:
            pass
        return None

    def _auto_apply_4gb_optimization(self):
        """Automatically checks and applies 4GB limit and WebClient service on startup."""
        # 1. Ensure WebClient service is running
        MountManager.ensure_webclient_service()

        # 2. Check if 4GB limit and fast close optimizations are applied
        reg = MountManager.check_registry_settings()
        if not reg.get("FileSizeLimit_ok") or not reg.get("BasicAuthLevel_ok") or not reg.get("SupportLocking_ok"):
            ok, msg = MountManager.optimize_registry_settings()
            if ok:
                self.safe_after(0, lambda: self.lbl_log.config(
                    text="✔ 4GB 파일 전송 제한 해제 및 WebClient 서비스가 기본 적용되었습니다."
                ))
            else:
                self.safe_after(0, lambda: self.lbl_log.config(
                    text="알림: 4GB 제한 해제를 완벽 적용하려면 프로그램을 '관리자 권한'으로 실행해 주세요."
                ))
        else:
            self.safe_after(0, lambda: self.lbl_log.config(
                text="✔ 4GB 대용량 전송 모드가 활성화되어 있습니다."
            ))

    def _build_layout(self):
        """Constructs sidebar, toolbar, and main settings panel."""
        # Top Header / Toolbar
        self.header_frame = tk.Frame(self, bg=self.BG_CARD, height=52, bd=0)
        self.header_frame.pack(side=tk.TOP, fill=tk.X)
        self.header_frame.pack_propagate(False)

        # Bottom border line for header
        header_divider = tk.Frame(self.header_frame, bg=self.BORDER_LIGHT, height=1)
        header_divider.pack(side=tk.BOTTOM, fill=tk.X)

        # Logo / Title
        logo_label = tk.Label(
            self.header_frame,
            text="⚡ WebDAV Drive",
            font=self.FONT_TITLE,
            bg=self.BG_CARD,
            fg=self.ACCENT_BLUE,
        )
        logo_label.pack(side=tk.LEFT, padx=(18, 10), pady=10)

        subtitle_label = tk.Label(
            self.header_frame,
            text="가상 드라이브 마운터",
            font=self.FONT_SMALL,
            bg=self.BG_CARD,
            fg=self.TEXT_MUTED,
        )
        subtitle_label.pack(side=tk.LEFT, pady=12)

        # Optimization Tool Button
        opt_btn = tk.Button(
            self.header_frame,
            text="🛠 4GB 최적화 상태",
            font=self.FONT_BUTTON,
            bg="#f1f5f9",
            fg=self.ACCENT_BLUE,
            activebackground="#e2e8f0",
            activeforeground=self.ACCENT_BLUE,
            bd=1,
            relief=tk.SOLID,
            highlightthickness=0,
            padx=12,
            pady=4,
            cursor="hand2",
            command=self.open_optimizer_dialog,
        )
        opt_btn.pack(side=tk.RIGHT, padx=8, pady=10)

        # Refresh Button
        refresh_btn = tk.Button(
            self.header_frame,
            text="🔄 새로고침",
            font=self.FONT_BUTTON,
            bg="#f1f5f9",
            fg=self.TEXT_MAIN,
            activebackground="#e2e8f0",
            activeforeground=self.TEXT_MAIN,
            bd=1,
            relief=tk.SOLID,
            highlightthickness=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.refresh_and_mount_all,
        )
        refresh_btn.pack(side=tk.RIGHT, padx=4, pady=10)

        # Minimize to System Tray Button
        tray_btn = tk.Button(
            self.header_frame,
            text="📥 트레이 최소화",
            font=self.FONT_BUTTON,
            bg="#f1f5f9",
            fg=self.TEXT_MAIN,
            activebackground="#e2e8f0",
            activeforeground=self.TEXT_MAIN,
            bd=1,
            relief=tk.SOLID,
            highlightthickness=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.on_close_to_tray,
        )
        tray_btn.pack(side=tk.RIGHT, padx=4, pady=10)

        # Auto-start on boot Checkbutton
        self.var_autostart = tk.BooleanVar(value=AutostartManager.is_autostart_enabled())
        chk_autostart = tk.Checkbutton(
            self.header_frame,
            text="부팅 시 자동실행",
            variable=self.var_autostart,
            font=self.FONT_BUTTON,
            bg=self.BG_CARD,
            fg=self.TEXT_MAIN,
            activebackground=self.BG_CARD,
            activeforeground=self.TEXT_MAIN,
            selectcolor="#ffffff",
            cursor="hand2",
            command=self._on_toggle_autostart,
        )
        chk_autostart.pack(side=tk.RIGHT, padx=8, pady=10)

        # Container for Sidebar and Main Content
        self.body_frame = tk.Frame(self, bg=self.BG_MAIN)
        self.body_frame.pack(fill=tk.BOTH, expand=True)

        # ----------------- Left Sidebar: Drive Profiles -----------------
        self.sidebar_frame = tk.Frame(self.body_frame, bg=self.BG_SIDEBAR, width=280)
        self.sidebar_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar_frame.pack_propagate(False)

        # Divider between sidebar and body
        sb_divider = tk.Frame(self.sidebar_frame, bg=self.BORDER_LIGHT, width=1)
        sb_divider.pack(side=tk.RIGHT, fill=tk.Y)

        # Sidebar Header
        sb_header = tk.Frame(self.sidebar_frame, bg=self.BG_SIDEBAR)
        sb_header.pack(fill=tk.X, padx=14, pady=(14, 8))

        sb_title = tk.Label(
            sb_header,
            text="스토리지 목록",
            font=self.FONT_LABEL,
            bg=self.BG_SIDEBAR,
            fg=self.TEXT_MUTED,
        )
        sb_title.pack(side=tk.LEFT)

        add_btn = tk.Button(
            sb_header,
            text="+ 새 드라이브 추가",
            font=self.FONT_BUTTON,
            bg=self.ACCENT_BLUE,
            fg="#ffffff",
            activebackground=self.ACCENT_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.create_new_profile,
        )
        add_btn.pack(side=tk.RIGHT)

        # Sidebar Footer: Cache location & Cache purge buttons at the bottom of storage list
        sb_footer = tk.Frame(self.sidebar_frame, bg=self.BG_SIDEBAR)
        sb_footer.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

        btn_sidebar_dir = tk.Button(
            sb_footer,
            text="📁 캐시 경로",
            font=self.FONT_BUTTON,
            bg="#f1f5f9",
            fg=self.TEXT_MAIN,
            activebackground="#e2e8f0",
            activeforeground=self.TEXT_MAIN,
            bd=1,
            relief=tk.SOLID,
            highlightthickness=0,
            padx=4,
            pady=5,
            cursor="hand2",
            command=self.change_cache_dir_ui,
        )
        btn_sidebar_dir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        btn_sidebar_purge = tk.Button(
            sb_footer,
            text="🧹 캐시 비우기",
            font=self.FONT_BUTTON,
            bg="#f0fdf4",
            fg=self.ACCENT_GREEN,
            activebackground="#dcfce7",
            activeforeground=self.ACCENT_GREEN,
            bd=1,
            relief=tk.SOLID,
            highlightbackground="#bbf7d0",
            highlightthickness=1,
            padx=4,
            pady=5,
            cursor="hand2",
            command=self.purge_cache_ui,
        )
        btn_sidebar_purge.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        # Scrollable list for profiles
        self.profiles_canvas = tk.Canvas(
            self.sidebar_frame,
            bg=self.BG_SIDEBAR,
            bd=0,
            highlightthickness=0,
        )
        self.scrollbar = ttk.Scrollbar(
            self.sidebar_frame,
            orient=tk.VERTICAL,
            command=self.profiles_canvas.yview,
            style="Vertical.TScrollbar",
        )
        self.profiles_inner = tk.Frame(self.profiles_canvas, bg=self.BG_SIDEBAR)

        self.profiles_inner.bind(
            "<Configure>",
            lambda e: self.profiles_canvas.configure(scrollregion=self.profiles_canvas.bbox("all")),
        )
        self.canvas_window = self.profiles_canvas.create_window(
            (0, 0), window=self.profiles_inner, anchor="nw", width=255
        )

        def _on_mousewheel(event):
            self.profiles_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.profiles_canvas.bind("<MouseWheel>", _on_mousewheel)
        self.profiles_inner.bind("<MouseWheel>", _on_mousewheel)
        self.profiles_canvas.bind(
            "<Configure>",
            lambda e: self.profiles_canvas.itemconfig(self.canvas_window, width=max(220, e.width - 5)),
        )

        self.profiles_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.profiles_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=6)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=6)

        # ----------------- Right Main Panel: Profile Details -----------------
        self.detail_frame = tk.Frame(self.body_frame, bg=self.BG_MAIN)
        self.detail_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=20, pady=16)

        self._build_detail_panel()

    def _build_detail_panel(self):
        """Builds configuration inputs and action controls in right panel."""
        # 1. Pinned Bottom: Status log label (permanently at the very bottom)
        self.lbl_log = tk.Label(
            self.detail_frame,
            text="4GB 대용량 전송 모드 기본 적용됨",
            font=self.FONT_SMALL,
            bg=self.BG_MAIN,
            fg=self.TEXT_MUTED,
            anchor="w",
        )
        self.lbl_log.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))

        # 2. Pinned Bottom: Action Bar (permanently visible right above log label)
        self.action_bar = tk.Frame(self.detail_frame, bg=self.BG_MAIN, pady=8)
        self.action_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Left action buttons (Save / Delete / Test)
        left_actions = tk.Frame(self.action_bar, bg=self.BG_MAIN)
        left_actions.pack(side=tk.LEFT)

        self.btn_save = tk.Button(
            left_actions,
            text="💾 설정 저장",
            font=self.FONT_BUTTON,
            bg="#ffffff",
            fg=self.TEXT_MAIN,
            activebackground="#f1f5f9",
            activeforeground=self.TEXT_MAIN,
            bd=1,
            relief=tk.SOLID,
            padx=12,
            pady=6,
            cursor="hand2",
            command=self.save_current_profile,
        )
        self.btn_save.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_test = tk.Button(
            left_actions,
            text="🔍 연결 테스트",
            font=self.FONT_BUTTON,
            bg="#ffffff",
            fg=self.ACCENT_BLUE,
            activebackground="#f1f5f9",
            activeforeground=self.ACCENT_BLUE,
            bd=1,
            relief=tk.SOLID,
            padx=10,
            pady=6,
            cursor="hand2",
            command=self.test_connection,
        )
        self.btn_test.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_delete = tk.Button(
            left_actions,
            text="🗑 삭제",
            font=self.FONT_BUTTON,
            bg="#ffffff",
            fg=self.ACCENT_RED,
            activebackground="#fef2f2",
            activeforeground=self.ACCENT_RED,
            bd=1,
            relief=tk.SOLID,
            padx=10,
            pady=6,
            cursor="hand2",
            command=self.delete_current_profile,
        )
        self.btn_delete.pack(side=tk.LEFT)

        # Right action buttons (Mount / Unmount / Open Explorer)
        right_actions = tk.Frame(self.action_bar, bg=self.BG_MAIN)
        right_actions.pack(side=tk.RIGHT)

        self.btn_explorer = tk.Button(
            right_actions,
            text="📁 탐색기 열기",
            font=self.FONT_BUTTON,
            bg="#ffffff",
            fg=self.TEXT_MAIN,
            activebackground="#f1f5f9",
            activeforeground=self.TEXT_MAIN,
            bd=1,
            relief=tk.SOLID,
            padx=12,
            pady=6,
            cursor="hand2",
            command=self.open_explorer,
        )
        self.btn_explorer.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_mount = tk.Button(
            right_actions,
            text="▶ 연결 (드라이브 마운트)",
            font=self.FONT_BUTTON,
            bg=self.ACCENT_BLUE,
            fg="#ffffff",
            activebackground=self.ACCENT_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            padx=16,
            pady=7,
            cursor="hand2",
            command=self.toggle_mount,
        )
        self.btn_mount.pack(side=tk.LEFT)

        # 3. Top status card
        self.status_card = tk.Frame(
            self.detail_frame,
            bg=self.BG_CARD,
            bd=1,
            relief=tk.SOLID,
            highlightbackground=self.BORDER_LIGHT,
            highlightthickness=1,
        )
        self.status_card.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))

        card_top = tk.Frame(self.status_card, bg=self.BG_CARD)
        card_top.pack(fill=tk.X, padx=16, pady=10)

        self.card_name_label = tk.Label(
            card_top,
            text="새 스토리지 설정",
            font=self.FONT_CARD_TITLE,
            bg=self.BG_CARD,
            fg=self.TEXT_MAIN,
        )
        self.card_name_label.pack(side=tk.LEFT)

        self.card_status_badge = tk.Label(
            card_top,
            text="○ 연결 해제됨",
            font=self.FONT_BUTTON,
            bg=self.BADGE_DISCONNECTED,
            fg=self.ACCENT_RED,
            padx=10,
            pady=3,
        )
        self.card_status_badge.pack(side=tk.RIGHT)

        # Quick connect button right in the top card as well!
        self.btn_top_mount = tk.Button(
            card_top,
            text="▶ 연결 (드라이브 마운트)",
            font=self.FONT_BUTTON,
            bg=self.ACCENT_BLUE,
            fg="#ffffff",
            activebackground=self.ACCENT_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            padx=14,
            pady=4,
            cursor="hand2",
            command=self.toggle_mount,
        )
        self.btn_top_mount.pack(side=tk.RIGHT, padx=(0, 10))

        # 4. Form Container (Card) - fills all remaining middle space
        form_frame = tk.Frame(
            self.detail_frame,
            bg=self.BG_CARD,
            padx=18,
            pady=14,
            bd=1,
            relief=tk.SOLID,
            highlightbackground=self.BORDER_LIGHT,
            highlightthickness=1,
        )
        form_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        def make_entry(parent, **kwargs):
            return tk.Entry(
                parent,
                font=self.FONT_INPUT,
                bg=self.BG_INPUT,
                fg=self.TEXT_MAIN,
                insertbackground=self.TEXT_MAIN,
                bd=1,
                relief=tk.SOLID,
                highlightthickness=0,
                **kwargs,
            )

        # Row 0: Storage Type Selection
        r0 = tk.Frame(form_frame, bg=self.BG_CARD)
        r0.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            r0, text="스토리지 종류", font=self.FONT_LABEL, bg=self.BG_CARD, fg=self.TEXT_MUTED
        ).pack(anchor="w")

        type_col = tk.Frame(r0, bg=self.BG_CARD)
        type_col.pack(fill=tk.X, pady=(4, 0))

        self.var_storage_type = tk.StringVar(value="webdav")
        self.rb_type_webdav = tk.Radiobutton(
            type_col,
            text="🌐 일반 WebDAV (Nextcloud, Synology NAS, QNAP, Alist, 자체 서버 등)",
            variable=self.var_storage_type,
            value="webdav",
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MAIN,
            activebackground=self.BG_CARD,
            cursor="hand2",
            command=self._on_storage_type_change,
        )
        self.rb_type_webdav.pack(anchor="w", pady=(2, 2))

        self.rb_type_mybox = tk.Radiobutton(
            type_col,
            text="🟢 네이버 MYBOX (네이버 공식 개인 액세스 토큰 PAT 연동)",
            variable=self.var_storage_type,
            value="mybox",
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MAIN,
            activebackground=self.BG_CARD,
            cursor="hand2",
            command=self._on_storage_type_change,
        )
        self.rb_type_mybox.pack(anchor="w", pady=(2, 2))

        self.rb_type_gdrive = tk.Radiobutton(
            type_col,
            text="🔴 구글 드라이브 (Google Drive - 원클릭 브라우저 로그인 및 초고속 스트리밍)",
            variable=self.var_storage_type,
            value="gdrive",
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MAIN,
            activebackground=self.BG_CARD,
            cursor="hand2",
            command=self._on_storage_type_change,
        )
        self.rb_type_gdrive.pack(anchor="w", pady=(2, 2))

        # Row 1: Profile Name & Drive Letter
        r1 = tk.Frame(form_frame, bg=self.BG_CARD)
        r1.pack(fill=tk.X, pady=6)

        # Profile Name
        f_name = tk.Frame(r1, bg=self.BG_CARD)
        f_name.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        tk.Label(
            f_name, text="스토리지 이름 (별칭)", font=self.FONT_LABEL, bg=self.BG_CARD, fg=self.TEXT_MUTED
        ).pack(anchor="w")
        self.entry_name = make_entry(f_name)
        self.entry_name.pack(fill=tk.X, ipady=5, pady=(4, 0))

        # Drive Letter Selection
        f_drive = tk.Frame(r1, bg=self.BG_CARD, width=120)
        f_drive.pack(side=tk.RIGHT)
        tk.Label(
            f_drive, text="드라이브 문자", font=self.FONT_LABEL, bg=self.BG_CARD, fg=self.TEXT_MUTED
        ).pack(anchor="w")
        self.combo_drive = ttk.Combobox(f_drive, state="readonly", width=8, font=self.FONT_INPUT)
        self.combo_drive.pack(ipady=3, pady=(4, 0))

        # ----------------- WebDAV Specific Fields Container -----------------
        self.frame_webdav_fields = tk.Frame(form_frame, bg=self.BG_CARD)
        self.frame_webdav_fields.pack(fill=tk.X)

        # Row 2: Host & Port & SSL
        r2 = tk.Frame(self.frame_webdav_fields, bg=self.BG_CARD)
        r2.pack(fill=tk.X, pady=6)

        # Host
        f_host = tk.Frame(r2, bg=self.BG_CARD)
        f_host.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        tk.Label(
            f_host,
            text="WebDAV 주소 (도메인 또는 IP)",
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MUTED,
        ).pack(anchor="w")
        self.entry_host = make_entry(f_host)
        self.entry_host.pack(fill=tk.X, ipady=5, pady=(4, 0))

        # Port
        f_port = tk.Frame(r2, bg=self.BG_CARD, width=80)
        f_port.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(f_port, text="포트", font=self.FONT_LABEL, bg=self.BG_CARD, fg=self.TEXT_MUTED).pack(
            anchor="w"
        )
        self.entry_port = make_entry(f_port, width=7)
        self.entry_port.pack(ipady=5, pady=(4, 0))
        self.entry_port.insert(0, "443")

        # SSL Checkbox
        f_ssl = tk.Frame(r2, bg=self.BG_CARD)
        f_ssl.pack(side=tk.RIGHT, pady=(18, 0))
        self.var_ssl = tk.BooleanVar(value=True)
        self.chk_ssl = tk.Checkbutton(
            f_ssl,
            text="HTTPS (보안 SSL)",
            variable=self.var_ssl,
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MAIN,
            activebackground=self.BG_CARD,
            activeforeground=self.TEXT_MAIN,
            selectcolor="#ffffff",
            command=self._on_ssl_toggle,
        )
        self.chk_ssl.pack()

        # Row 3: Remote Path
        r3 = tk.Frame(self.frame_webdav_fields, bg=self.BG_CARD)
        r3.pack(fill=tk.X, pady=6)
        tk.Label(
            r3,
            text="원격 경로 (예: / or /remote.php/webdav or /dav)",
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MUTED,
        ).pack(anchor="w")
        self.entry_path = make_entry(r3)
        self.entry_path.pack(fill=tk.X, ipady=5, pady=(4, 0))
        self.entry_path.insert(0, "/")

        # Row 4: Username & Password
        r4 = tk.Frame(self.frame_webdav_fields, bg=self.BG_CARD)
        r4.pack(fill=tk.X, pady=6)

        # Username
        f_user = tk.Frame(r4, bg=self.BG_CARD)
        f_user.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        tk.Label(
            f_user, text="사용자 계정 (아이디)", font=self.FONT_LABEL, bg=self.BG_CARD, fg=self.TEXT_MUTED
        ).pack(anchor="w")
        self.entry_user = make_entry(f_user)
        self.entry_user.pack(fill=tk.X, ipady=5, pady=(4, 0))

        # Password
        f_pass = tk.Frame(r4, bg=self.BG_CARD)
        f_pass.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        tk.Label(
            f_pass, text="비밀번호", font=self.FONT_LABEL, bg=self.BG_CARD, fg=self.TEXT_MUTED
        ).pack(anchor="w")
        self.entry_pass = make_entry(f_pass, show="●")
        self.entry_pass.pack(fill=tk.X, ipady=5, pady=(4, 0))

        # ----------------- Naver MYBOX Specific Fields Container -----------------
        self.frame_mybox_fields = tk.Frame(form_frame, bg=self.BG_CARD)

        # Token input row
        r_token = tk.Frame(self.frame_mybox_fields, bg=self.BG_CARD)
        r_token.pack(fill=tk.X, pady=6)

        tk.Label(
            r_token,
            text="네이버 MYBOX 개인 액세스 토큰 (Personal Access Token, PAT)",
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MUTED,
        ).pack(anchor="w")

        token_input_row = tk.Frame(r_token, bg=self.BG_CARD)
        token_input_row.pack(fill=tk.X, pady=(4, 0))

        self.var_token_visible = tk.BooleanVar(value=False)
        self.entry_mybox_token = make_entry(token_input_row, show="●")
        self.entry_mybox_token.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)

        self.btn_toggle_token = tk.Button(
            token_input_row,
            text="👁 보기",
            font=self.FONT_SMALL,
            bg="#f8f9fa",
            fg=self.TEXT_MAIN,
            activebackground="#e2e8f0",
            bd=1,
            relief=tk.SOLID,
            padx=8,
            cursor="hand2",
            command=self._toggle_token_visibility,
        )
        self.btn_toggle_token.pack(side=tk.RIGHT, padx=(6, 0))

        # Compact Sleek MYBOX Guide Card
        card_guide = tk.Frame(
            self.frame_mybox_fields,
            bg="#f0fdf4",
            bd=1,
            relief=tk.SOLID,
            highlightbackground="#bbf7d0",
            highlightthickness=1,
            padx=12,
            pady=8,
        )
        card_guide.pack(fill=tk.X, pady=(6, 2))

        guide_top_row = tk.Frame(card_guide, bg="#f0fdf4")
        guide_top_row.pack(fill=tk.X)

        tk.Label(
            guide_top_row,
            text="💡 네이버 MYBOX 연동 토큰 발급 방법",
            font=("맑은 고딕", 10, "bold"),
            bg="#f0fdf4",
            fg="#166534",
        ).pack(side=tk.LEFT)

        btn_open_mybox = tk.Button(
            guide_top_row,
            text="🌐 토큰 관리 페이지 바로가기",
            font=self.FONT_SMALL,
            bg="#ffffff",
            fg="#166534",
            activebackground="#dcfce7",
            activeforeground="#166534",
            bd=1,
            relief=tk.SOLID,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._open_mybox_website,
        )
        btn_open_mybox.pack(side=tk.RIGHT)

        guide_steps = (
            "MYBOX 웹 접속 > 우측 상단 '환경설정' > [계정 및 개인 액세스 토큰 관리] > [토큰 생성] 후 위 입력칸에 붙여넣고 [연결]을 클릭하세요."
        )
        tk.Label(
            card_guide,
            text=guide_steps,
            font=("맑은 고딕", 9, "normal"),
            bg="#f0fdf4",
            fg="#14532d",
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, pady=(4, 0))

        # ----------------- Google Drive Specific Fields Container -----------------
        self.frame_gdrive_fields = tk.Frame(form_frame, bg=self.BG_CARD)

        # 1. Quick One-Click OAuth Button Row
        r_gdrive_auth = tk.Frame(self.frame_gdrive_fields, bg=self.BG_CARD)
        r_gdrive_auth.pack(fill=tk.X, pady=(4, 8))

        self.btn_gdrive_login = tk.Button(
            r_gdrive_auth,
            text="🚀 구글 계정 간편 로그인 (원클릭 자동 인증)",
            font=("맑은 고딕", 10, "bold"),
            bg="#1a73e8",
            fg="#ffffff",
            activebackground="#1557b0",
            activeforeground="#ffffff",
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            command=self._start_gdrive_oauth,
        )
        self.btn_gdrive_login.pack(side=tk.LEFT)

        # 2. Sleek High-Visibility Guide Card (Prominently placed right below login button)
        card_g_guide = tk.Frame(
            self.frame_gdrive_fields,
            bg="#f0f7ff",
            bd=1,
            relief=tk.SOLID,
            highlightbackground="#60a5fa",
            highlightthickness=1,
            padx=14,
            pady=10,
        )
        card_g_guide.pack(fill=tk.X, pady=(0, 10))

        guide_top_row = tk.Frame(card_g_guide, bg="#f0f7ff")
        guide_top_row.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            guide_top_row,
            text="💡 Google Drive 원클릭 연동 및 사용 안내",
            font=("맑은 고딕", 10, "bold"),
            bg="#f0f7ff",
            fg="#1d4ed8",
        ).pack(side=tk.LEFT)

        guide_g_text = (
            "1. 위의 [구글 계정 간편 로그인] 버튼을 누르면 기본 웹 브라우저가 자동으로 열립니다.\n"
            "2. 브라우저에서 사용할 구글 계정으로 로그인하고 계정 액세스를 허용(승인)해 주세요.\n"
            "3. 인증이 완료되면 아래 [인증 토큰] 입력칸에 토큰이 자동으로 채워집니다.\n"
            "4. 하단의 [▶ 연결 (드라이브 마운트)] 버튼을 누르면 가상 드라이브로 즉시 연결됩니다.\n"
            "※ WinFsp 가상 파일시스템 드라이버와 초고속 스트리밍 캐시로 대용량 동영상도 즉시 재생됩니다."
        )
        tk.Label(
            card_g_guide,
            text=guide_g_text,
            font=("맑은 고딕", 9, "normal"),
            bg="#f0f7ff",
            fg="#1e293b",
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)

        # 3. Token field
        r_gdrive_token = tk.Frame(self.frame_gdrive_fields, bg=self.BG_CARD)
        r_gdrive_token.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            r_gdrive_token,
            text="구글 드라이브 OAuth 인증 토큰 (위 로그인 클릭 시 자동 입력됨)",
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MUTED,
        ).pack(anchor="w")

        token_g_input_row = tk.Frame(r_gdrive_token, bg=self.BG_CARD)
        token_g_input_row.pack(fill=tk.X, pady=(4, 0))

        self.var_gdrive_token_visible = tk.BooleanVar(value=False)
        self.entry_gdrive_token = make_entry(token_g_input_row, show="●")
        self.entry_gdrive_token.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)

        self.btn_paste_gdrive_token = tk.Button(
            token_g_input_row,
            text="📋 붙여넣기",
            font=self.FONT_SMALL,
            bg="#f8f9fa",
            fg=self.TEXT_MAIN,
            activebackground="#e2e8f0",
            bd=1,
            relief=tk.SOLID,
            padx=8,
            cursor="hand2",
            command=self._paste_gdrive_token,
        )
        self.btn_paste_gdrive_token.pack(side=tk.RIGHT, padx=(6, 0))

        self.btn_toggle_gdrive_token = tk.Button(
            token_g_input_row,
            text="👁 보기",
            font=self.FONT_SMALL,
            bg="#f8f9fa",
            fg=self.TEXT_MAIN,
            activebackground="#e2e8f0",
            bd=1,
            relief=tk.SOLID,
            padx=8,
            cursor="hand2",
            command=self._toggle_gdrive_token_visibility,
        )
        self.btn_toggle_gdrive_token.pack(side=tk.RIGHT, padx=(6, 0))

        # 4. Optional Root Folder ID
        r_root = tk.Frame(self.frame_gdrive_fields, bg=self.BG_CARD)
        r_root.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            r_root,
            text="특정 루트 폴더 ID (선택 사항: 비워두면 내 드라이브 전체 연결)",
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.TEXT_MUTED,
        ).pack(anchor="w")
        self.entry_gdrive_root_id = make_entry(r_root)
        self.entry_gdrive_root_id.pack(fill=tk.X, ipady=5, pady=(4, 0))

        # Row 5: Fast Streaming Mode (RaiDrive-style instant video playback)
        r5 = tk.Frame(form_frame, bg=self.BG_CARD)
        r5.pack(fill=tk.X, pady=(10, 0))

        self.var_streaming = tk.BooleanVar(value=True)
        self.chk_streaming = tk.Checkbutton(
            r5,
            text="🚀 초고속 스트리밍 모드 (RaiDrive 방식: 대용량 영상 클릭 시 다운로드 없이 즉시 재생)",
            variable=self.var_streaming,
            font=self.FONT_LABEL,
            bg=self.BG_CARD,
            fg=self.ACCENT_BLUE,
            activebackground=self.BG_CARD,
            activeforeground=self.ACCENT_BLUE,
            selectcolor="#ffffff",
            cursor="hand2",
        )
        self.chk_streaming.pack(anchor="w")

    def _on_toggle_autostart(self):
        """Toggles Windows startup registry entry."""
        enabled = self.var_autostart.get()
        ok, msg = AutostartManager.set_autostart(enabled)
        self.lbl_log.config(text=msg)
        if not ok:
            messagebox.showwarning("자동 실행 설정", msg)
            self.var_autostart.set(not enabled)

    def purge_cache_ui(self):
        """Clears local VFS cache files from main GUI sidebar button."""
        ok, msg = StreamingMounter.purge_cache()
        if ok:
            messagebox.showinfo("캐시 비우기 완료", msg)
        else:
            messagebox.showwarning("캐시 비우기 실패", msg)

    def on_close_to_tray(self):
        """Intercepts window close to minimize to system tray instead of exiting."""
        self.withdraw()
        if hasattr(self, "tray") and self.tray and self.tray.is_available():
            try:
                self.tray.notify(
                    "WebDAV Drive Mounter",
                    "프로그램이 시스템 트레이로 최소화되었습니다.\n드라이브 연결 상태가 계속 유지됩니다.",
                )
            except Exception:
                pass

    def restore_from_tray(self):
        """Restores window from system tray (thread-safe)."""
        self.after(0, self._do_restore)

    def _do_restore(self):
        """Actually brings the window back to foreground."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self.refresh_profiles_list()

    def unmount_all_drives(self):
        """Unmounts all WebDAV drives mounted by this application cleanly and safely."""
        # 1. CRITICAL: Disconnect Windows network drives FIRST while backend services are still running!
        # Only target drives managed by this application to avoid disrupting other user shares/NAS!
        target_drives = set()
        try:
            profiles = ConfigManager.get_profiles()
            for p in profiles:
                drive = p.get("drive_letter")
                if drive:
                    drive_clean = drive.upper().rstrip("\\")
                    if not drive_clean.endswith(":"):
                        drive_clean += ":"
                    target_drives.add(drive_clean)
        except Exception:
            pass

        # Include active streaming and gateway drives
        try:
            for d in list(StreamingMounter._active_mounts.keys()):
                target_drives.add(d.upper())
        except Exception:
            pass

        try:
            for k in list(MyBoxGatewayManager._instances.keys()):
                if ":" in k:
                    clean_k = k.upper().rstrip("\\")
                    target_drives.add(clean_k if clean_k.endswith(":") else clean_k + ":")
        except Exception:
            pass

        # Also include any local loopback bridges (127.0.0.1 or localhost)
        try:
            mounted = MountManager.get_mounted_drives()
            for d, target in mounted.items():
                if "127.0.0.1" in target or "localhost" in target or "DavWWWRoot" in target:
                    target_drives.add(d.upper())
        except Exception:
            pass

        for drive in target_drives:
            try:
                MountManager.force_unmount(drive)
            except Exception:
                pass

        # 2. Stop and terminate all fast streaming (rclone) processes AFTER Windows drives are disconnected
        StreamingMounter.unmount_all()

        # 3. Stop all local MYBOX gateways
        MyBoxGatewayManager.stop_all()

    def quit_completely(self):
        """Completely exits the app, stops the tray icon, and cleanly unmounts all drives."""
        # Unmount all drives first
        try:
            self.unmount_all_drives()
        except Exception:
            pass

        if hasattr(self, "tray") and self.tray:
            self.tray.stop()
        self.after(0, self._do_quit)

    def _do_quit(self):
        self.destroy()
        sys.exit(0)

    def change_cache_dir_ui(self):
        """Allows user to select a custom folder/drive (e.g. D:\\WebDAV_Cache) for VFS streaming cache."""
        from tkinter import filedialog
        current = ConfigManager.get_setting("custom_cache_dir", "")
        chosen = filedialog.askdirectory(
            parent=self,
            title="VFS 캐시 저장 폴더 선택 (D: 드라이브 등 지정 가능)",
            initialdir=current if current and os.path.exists(current) else None,
        )
        if chosen:
            chosen = os.path.abspath(chosen)
            try:
                os.makedirs(chosen, exist_ok=True)
            except Exception as e:
                messagebox.showerror("오류", f"선택한 폴더를 생성할 수 없습니다:\n{str(e)}")
                return
            ConfigManager.set_setting("custom_cache_dir", chosen)
            messagebox.showinfo(
                "캐시 경로 변경 완료",
                f"VFS 캐시 저장 경로가 성공적으로 변경되었습니다!\n\n경로: {chosen}\n\n※ 새로 마운트되는 드라이브부터 새 경로가 적용됩니다.",
            )

    def reset_cache_dir_ui(self):
        """Resets cache directory to default C: AppData folder."""
        ConfigManager.set_setting("custom_cache_dir", "")
        messagebox.showinfo(
            "캐시 경로 초기화 완료",
            "캐시 저장 경로가 Windows 기본 위치(C: AppData)로 초기화되었습니다.",
        )

    def _is_profile_active(self, p: Dict[str, Any]) -> bool:
        """Determines if a profile's drive is truly mounted and its backend service is alive."""
        try:
            drive = str(p.get("drive_letter", "Z:")).upper().rstrip("\\")
            if not drive.endswith(":"):
                drive += ":"
            st = p.get("storage_type", "webdav")

            # If rclone streaming process is actively running for this drive, it is mounted
            if StreamingMounter.is_streaming_active(drive):
                return True

            is_mounted_in_win = bool(MountManager.is_drive_mounted(drive))

            if st == "mybox":
                profile_id = str(p.get("id") or drive)
                gw_alive = bool(
                    MyBoxGatewayManager.is_gateway_running(profile_id)
                    or MyBoxGatewayManager.is_gateway_running(drive)
                )
                return bool(gw_alive or is_mounted_in_win)

            return is_mounted_in_win
        except Exception:
            return False

    def refresh_and_mount_all(self):
        """Refreshes profile list and auto-mounts all disconnected storage profiles in parallel."""
        self.refresh_profiles_list()
        self._update_mount_status_ui()
        threading.Thread(target=self._auto_mount_profiles, daemon=True).start()

    def _mount_single_profile(self, p: Dict[str, Any]) -> bool:
        """Mounts a single profile in background (used for fast parallel execution)."""
        drive = p.get("drive_letter", "Z:").upper().rstrip("\\")
        if not drive.endswith(":"):
            drive += ":"
        p_name = p.get("name", drive)
        storage_type = p.get("storage_type", "webdav")

        if self._is_profile_active(p):
            return True

        self.after(0, lambda d=drive, n=p_name: self.lbl_log.config(
            text=f"'{n}' ({d}) 드라이브 자동 연결 중..."
        ))

        ok = False
        try:
            if storage_type == "gdrive":
                token = p.get("gdrive_token", "")
                if token:
                    ok, _ = StreamingMounter.mount_gdrive(
                        drive_letter=drive,
                        profile_name=p.get("name", "Google Drive"),
                        token_json=token,
                        root_folder_id=p.get("gdrive_root_id", ""),
                    )
            elif storage_type == "mybox":
                token = p.get("mybox_token", "")
                if token:
                    profile_id = p.get("id", drive)
                    gw_ok, _, port = MyBoxGatewayManager.start_gateway(profile_id, token)
                    if gw_ok:
                        if p.get("streaming", True):
                            ok, _ = StreamingMounter.mount_streaming(
                                drive, p.get("name", "MYBOX"), "127.0.0.1", port, False, "/", "mybox", "token"
                            )
                        else:
                            ok, _ = MountManager.mount(drive, f"http://127.0.0.1:{port}/", "mybox", "token")
            else:
                host = p.get("host", "")
                if host:
                    if p.get("streaming", True):
                        ok, _ = StreamingMounter.mount_streaming(
                            drive_letter=drive,
                            profile_name=p.get("name", "WebDAV"),
                            host=host,
                            port=p.get("port", 443),
                            ssl=p.get("ssl", True),
                            path=p.get("path", "/"),
                            user=p.get("username", ""),
                            password=p.get("password", ""),
                        )
                        # Automatic Fallback: If streaming mount fails, fall back to Windows WebClient mount
                        if not ok:
                            url = MountManager.format_webdav_url(
                                host, p.get("port", 443), p.get("ssl", True), p.get("path", "/")
                            )
                            ok, _ = MountManager.mount(drive, url, p.get("username", ""), p.get("password", ""))
                    else:
                        url = MountManager.format_webdav_url(
                            host, p.get("port", 443), p.get("ssl", True), p.get("path", "/")
                        )
                        ok, _ = MountManager.mount(drive, url, p.get("username", ""), p.get("password", ""))
        except Exception:
            ok = False

        self.after(0, self.refresh_profiles_list)
        self.after(0, self._update_mount_status_ui)
        return ok

    def _cleanup_stale_mounts_on_startup(self):
        """Cleans up any broken or orphan network drive connections left over from previous unclean shutdowns."""
        try:
            profiles = ConfigManager.get_profiles()
            mounted_drives = MountManager.get_mounted_drives()
            for p in profiles:
                drive = p.get("drive_letter")
                if not drive:
                    continue
                # If backend service is NOT active, but drive letter exists in net use:
                # It's an orphan/broken drive from a previous crashed session! Force unmount it immediately.
                if not self._is_profile_active(p):
                    if drive in mounted_drives:
                        MountManager.force_unmount(drive)
        except Exception:
            pass

    def _auto_mount_profiles(self):
        """Auto-mounts registered drives concurrently at maximum speed."""
        import time
        time.sleep(0.05)  # Minimal tick to let UI paint first
        profiles = ConfigManager.get_profiles()
        if not profiles:
            return

        to_mount = [p for p in profiles if not self._is_profile_active(p)]
        if not to_mount:
            self.after(0, lambda: self.lbl_log.config(text="모든 스토리지 드라이브가 이미 연결되어 있습니다."))
            return

        self.after(0, lambda: self.lbl_log.config(
            text=f"스토리지 {len(to_mount)}개 초고속 동시 자동 연결 중..."
        ))

        # Mount all profiles concurrently in native background threads (no multiprocessing)
        results = []
        threads = []

        def _worker(prof):
            try:
                res = self._mount_single_profile(prof)
                results.append(res)
            except Exception:
                results.append(False)

        for p in to_mount:
            t = threading.Thread(target=_worker, args=(p,), daemon=True)
            threads.append(t)
            t.start()
            time.sleep(0.35)

        for t in threads:
            t.join(timeout=30)

        success_count = sum(1 for r in results if r)
        total = len(to_mount)
        if success_count > 0:
            self.after(0, lambda: self.lbl_log.config(
                text=f"✔ 스토리지 {success_count}/{total}개 자동 연결 완료 (초고속)"
            ))
        else:
            self.after(0, lambda: self.lbl_log.config(
                text="드라이브 자동 연결 완료"
            ))
        self.after(0, self.refresh_profiles_list)
        self.after(0, self._update_mount_status_ui)


    def _on_ssl_toggle(self):
        """Automatically switch default port when toggling SSL."""
        is_ssl = self.var_ssl.get()
        curr_port = self.entry_port.get().strip()
        if is_ssl and curr_port == "80":
            self.entry_port.delete(0, tk.END)
            self.entry_port.insert(0, "443")
        elif not is_ssl and curr_port == "443":
            self.entry_port.delete(0, tk.END)
            self.entry_port.insert(0, "80")

    def _on_storage_type_change(self):
        """Switches between WebDAV, Naver MYBOX, and Google Drive form fields."""
        st = self.var_storage_type.get()
        self.frame_webdav_fields.pack_forget()
        self.frame_mybox_fields.pack_forget()
        self.frame_gdrive_fields.pack_forget()

        if st == "mybox":
            self.frame_mybox_fields.pack(fill=tk.X, before=self.chk_streaming.master)
            if self.entry_name.get().strip() in ("내 WebDAV 저장소", "구글 드라이브", ""):
                self.entry_name.delete(0, tk.END)
                self.entry_name.insert(0, "네이버 MYBOX")
        elif st == "gdrive":
            self.frame_gdrive_fields.pack(fill=tk.X, before=self.chk_streaming.master)
            if self.entry_name.get().strip() in ("내 WebDAV 저장소", "네이버 MYBOX", ""):
                self.entry_name.delete(0, tk.END)
                self.entry_name.insert(0, "구글 드라이브")
        else:
            self.frame_webdav_fields.pack(fill=tk.X, before=self.chk_streaming.master)
            if self.entry_name.get().strip() in ("네이버 MYBOX", "구글 드라이브", ""):
                self.entry_name.delete(0, tk.END)
                self.entry_name.insert(0, "내 WebDAV 저장소")

    def _toggle_token_visibility(self):
        """Toggles masking on the MYBOX personal access token input."""
        visible = self.var_token_visible.get()
        if visible:
            self.entry_mybox_token.config(show="●")
            self.btn_toggle_token.config(text="👁 보기")
            self.var_token_visible.set(False)
        else:
            self.entry_mybox_token.config(show="")
            self.btn_toggle_token.config(text="🙈 숨김")
            self.var_token_visible.set(True)

    def _toggle_gdrive_token_visibility(self):
        """Toggles masking on Google Drive OAuth token input."""
        visible = self.var_gdrive_token_visible.get()
        if visible:
            self.entry_gdrive_token.config(show="●")
            self.btn_toggle_gdrive_token.config(text="👁 보기")
            self.var_gdrive_token_visible.set(False)
        else:
            self.entry_gdrive_token.config(show="")
            self.btn_toggle_gdrive_token.config(text="🙈 숨김")
            self.var_gdrive_token_visible.set(True)

    def _paste_gdrive_token(self):
        """Pastes clipboard text into Google Drive token field."""
        try:
            cb = self.clipboard_get().strip()
            if cb:
                self.entry_gdrive_token.delete(0, tk.END)
                self.entry_gdrive_token.insert(0, cb)
                self.lbl_log.config(text="클립보드에서 구글 토큰을 붙여넣었습니다.")
        except Exception:
            pass

    def _start_gdrive_oauth(self):
        """Launches rclone authorize drive in background and captures token."""
        self.btn_gdrive_login.config(state=tk.DISABLED, text="인증 브라우저 대기 중...")
        self.lbl_log.config(text="웹 브라우저가 열립니다. 구글 계정으로 로그인 후 액세스를 허용해 주세요...")

        def auth_worker():
            ok, msg, token = StreamingMounter.authorize_gdrive()

            def update_ui():
                self.btn_gdrive_login.config(state=tk.NORMAL, text="🚀 구글 계정 간편 로그인 (원클릭 자동 인증)")
                self.lbl_log.config(text=msg)
                if ok and token:
                    self.entry_gdrive_token.delete(0, tk.END)
                    self.entry_gdrive_token.insert(0, token)
                    curr_name = self.entry_name.get().strip()
                    if not curr_name or curr_name in ("내 WebDAV 저장소", "네이버 MYBOX"):
                        self.entry_name.delete(0, tk.END)
                        self.entry_name.insert(0, "구글 드라이브")
                    messagebox.showinfo(
                        "구글 드라이브 인증 성공",
                        "구글 드라이브 계정 인증이 완료되었습니다!\n이제 하단의 [연결 (드라이브 마운트)] 버튼을 클릭하세요.",
                    )
                elif not ok:
                    messagebox.showerror("인증 실패", msg)

            self.after(0, update_ui)

        threading.Thread(target=auth_worker, daemon=True).start()

    def _open_mybox_website(self):
        """Opens Naver MYBOX website in the default browser."""
        try:
            webbrowser.open("https://mybox.naver.com")
        except Exception:
            pass

    def _startup_check(self):
        """Background startup check for WebClient service."""
        is_running, _ = MountManager.check_webclient_service()
        if not is_running:
            self.lbl_log.config(
                text="알림: Windows WebClient 서비스가 꺼져 있습니다. 최적화 도구를 실행하세요."
            )

    def refresh_profiles_list(self):
        """Reloads profiles from config and rebuilds sidebar items."""
        # Clear sidebar list items
        for widget in self.profiles_inner.winfo_children():
            widget.destroy()

        self.profiles = ConfigManager.get_profiles()

        if not self.profiles:
            empty_lbl = tk.Label(
                self.profiles_inner,
                text="등록된 드라이브가 없습니다.\n'+ 새 드라이브 추가'를 눌러주세요.",
                font=self.FONT_LABEL,
                bg=self.BG_SIDEBAR,
                fg=self.TEXT_MUTED,
                pady=30,
                justify=tk.CENTER,
            )
            empty_lbl.pack(fill=tk.X)
            self.create_new_profile()
            return

        for p in self.profiles:
            p_id = str(p.get("id") or "")
            p_name = p.get("name", "이름 없음")
            p_drive = p.get("drive_letter", "Z:")
            p_storage = p.get("storage_type", "webdav")

            is_mounted = self._is_profile_active(p)

            # Profile Card in Sidebar
            is_selected = (p_id == self.current_profile_id)
            card_bg = "#ffffff" if is_selected else "#f8f9fa"
            hover_border = self.ACCENT_BLUE if is_selected else self.BORDER_LIGHT

            p_frame = tk.Frame(
                self.profiles_inner,
                bg=card_bg,
                bd=1,
                relief=tk.SOLID,
                padx=10,
                pady=10,
                cursor="hand2",
            )
            p_frame.configure(highlightbackground=hover_border, highlightthickness=1)
            p_frame.pack(fill=tk.X, pady=4)

            # Left Drive Badge
            drive_badge = tk.Label(
                p_frame,
                text=p_drive,
                font=self.FONT_BUTTON,
                bg=self.ACCENT_BLUE if is_mounted else "#e2e8f0",
                fg="#ffffff" if is_mounted else self.TEXT_MAIN,
                padx=6,
                pady=2,
                width=3,
            )
            drive_badge.pack(side=tk.LEFT, padx=(0, 8))

            # Center text
            text_frame = tk.Frame(p_frame, bg=card_bg)
            text_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

            if p_storage == "gdrive":
                prefix = "🔴 [G-Drive] "
            elif p_storage == "mybox":
                prefix = "🟢 [MYBOX] "
            else:
                prefix = "📁 "
            title_lbl = tk.Label(
                text_frame,
                text=f"{prefix}{p_name}",
                font=self.FONT_LABEL,
                bg=card_bg,
                fg=self.TEXT_MAIN,
                anchor="w",
            )
            title_lbl.pack(fill=tk.X)

            status_text = "● 연결됨" if is_mounted else "○ 연결 해제"
            status_color = self.ACCENT_GREEN if is_mounted else self.TEXT_MUTED
            sub_lbl = tk.Label(
                text_frame,
                text=status_text,
                font=self.FONT_SMALL,
                bg=card_bg,
                fg=status_color,
                anchor="w",
            )
            sub_lbl.pack(fill=tk.X)

            # Bind clicks and wheel scrolling
            for w in (p_frame, drive_badge, text_frame, title_lbl, sub_lbl):
                w.bind("<Button-1>", lambda e, pid=p_id: self.select_profile(pid))
                w.bind("<MouseWheel>", lambda e: self.profiles_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # Select first profile or keep selected
        if self.current_profile_id:
            matching = [p for p in self.profiles if p.get("id") == self.current_profile_id]
            if matching:
                self.select_profile(self.current_profile_id)
            else:
                self.select_profile(str(self.profiles[0].get("id", "")))
        elif self.profiles:
            self.select_profile(str(self.profiles[0].get("id", "")))

    def _refresh_drive_combobox(self, current_drive: Optional[str] = None):
        """Populates available drive letters, always including the profile's current drive."""
        available = MountManager.get_available_drives()
        if current_drive and current_drive not in available:
            available = [current_drive] + available
        self.combo_drive["values"] = available
        if current_drive and current_drive in available:
            self.combo_drive.set(current_drive)
        elif available:
            self.combo_drive.set(available[0])

    def select_profile(self, profile_id: str):
        """Loads selected profile into detail view."""
        self.current_profile_id = profile_id
        profile = next((p for p in self.profiles if p.get("id") == profile_id), None)
        if not profile:
            return

        storage_type = profile.get("storage_type", "webdav")
        self.var_storage_type.set(storage_type)
        if storage_type == "gdrive":
            default_title = "구글 드라이브"
        elif storage_type == "mybox":
            default_title = "네이버 MYBOX"
        else:
            default_title = "WebDAV 스토리지"
        self.card_name_label.config(text=profile.get("name", default_title))

        # Update fields
        self.entry_name.delete(0, tk.END)
        self.entry_name.insert(0, profile.get("name", ""))

        self.entry_mybox_token.delete(0, tk.END)
        self.entry_mybox_token.insert(0, profile.get("mybox_token", ""))

        self.entry_gdrive_token.delete(0, tk.END)
        self.entry_gdrive_token.insert(0, profile.get("gdrive_token", ""))

        self.entry_gdrive_root_id.delete(0, tk.END)
        self.entry_gdrive_root_id.insert(0, profile.get("gdrive_root_id", ""))

        self.entry_host.delete(0, tk.END)
        self.entry_host.insert(0, profile.get("host", ""))

        self.entry_port.delete(0, tk.END)
        self.entry_port.insert(0, str(profile.get("port", 443)))

        self.var_ssl.set(profile.get("ssl", True))

        self.entry_path.delete(0, tk.END)
        self.entry_path.insert(0, profile.get("path", "/"))

        self.entry_user.delete(0, tk.END)
        self.entry_user.insert(0, profile.get("username", ""))

        self.entry_pass.delete(0, tk.END)
        self.entry_pass.insert(0, profile.get("password", ""))

        p_drive = profile.get("drive_letter", "Z:")
        self._refresh_drive_combobox(p_drive)

        self.var_streaming.set(profile.get("streaming", True))

        self._on_storage_type_change()
        self._update_mount_status_ui()

    def create_new_profile(self):
        """Clears fields to set up a brand new profile."""
        self.current_profile_id = None
        self.var_storage_type.set("webdav")
        self.card_name_label.config(text="새 WebDAV 스토리지")

        self.entry_name.delete(0, tk.END)
        self.entry_name.insert(0, "내 WebDAV 저장소")

        self.entry_mybox_token.delete(0, tk.END)
        self.entry_gdrive_token.delete(0, tk.END)
        self.entry_gdrive_root_id.delete(0, tk.END)

        self.entry_host.delete(0, tk.END)
        self.entry_host.insert(0, "")

        self.entry_port.delete(0, tk.END)
        self.entry_port.insert(0, "443")

        self.var_ssl.set(True)

        self.entry_path.delete(0, tk.END)
        self.entry_path.insert(0, "/")

        self.entry_user.delete(0, tk.END)
        self.entry_user.insert(0, "")

        self.entry_pass.delete(0, tk.END)
        self.entry_pass.insert(0, "")

        self.var_streaming.set(True)

        self._refresh_drive_combobox()
        self._on_storage_type_change()
        self._update_mount_status_ui()

    def _get_form_data(self) -> Dict[str, Any]:
        """Extracts and validates values from the UI fields."""
        storage_type = self.var_storage_type.get()
        host = self.entry_host.get().strip()
        port_str = self.entry_port.get().strip()
        try:
            port = int(port_str) if port_str else 443
        except ValueError:
            port = 443

        if storage_type == "gdrive":
            default_name = "구글 드라이브"
        elif storage_type == "mybox":
            default_name = "네이버 MYBOX"
        else:
            default_name = "WebDAV 스토리지"
        name = self.entry_name.get().strip() or default_name

        return {
            "id": self.current_profile_id,
            "name": name,
            "storage_type": storage_type,
            "mybox_token": self.entry_mybox_token.get().strip(),
            "gdrive_token": self.entry_gdrive_token.get().strip(),
            "gdrive_root_id": self.entry_gdrive_root_id.get().strip(),
            "host": host,
            "port": port,
            "ssl": self.var_ssl.get(),
            "path": self.entry_path.get().strip() or "/",
            "username": self.entry_user.get().strip(),
            "password": self.entry_pass.get(),
            "drive_letter": self.combo_drive.get().strip() or "Z:",
            "streaming": self.var_streaming.get(),
        }

    def _update_mount_status_ui(self):
        """Updates mount/unmount button state and status badge."""
        drive = self.combo_drive.get().strip() or "Z:"
        if not drive.endswith(":"):
            drive += ":"
        curr_p = None
        if self.current_profile_id:
            raw_p = next((p for p in self.profiles if p.get("id") == self.current_profile_id), None)
            if raw_p:
                curr_p = dict(raw_p)
        if not curr_p:
            curr_p = self._get_form_data()
        curr_p["drive_letter"] = drive
        is_mounted = self._is_profile_active(curr_p)

        if is_mounted:
            self.card_status_badge.config(
                text=f"● {drive} 연결됨",
                bg=self.BADGE_CONNECTED,
                fg=self.ACCENT_GREEN,
            )
            self.btn_mount.config(
                text="⏹ 연결 해제",
                bg=self.ACCENT_RED,
                activebackground="#b91c1c",
                state=tk.NORMAL,
            )
            if hasattr(self, "btn_top_mount"):
                self.btn_top_mount.config(
                    text="⏹ 연결 해제",
                    bg=self.ACCENT_RED,
                    activebackground="#b91c1c",
                    state=tk.NORMAL,
                )
            self.btn_explorer.config(state=tk.NORMAL)
        else:
            self.card_status_badge.config(
                text="○ 연결 해제됨",
                bg=self.BADGE_DISCONNECTED,
                fg=self.ACCENT_RED,
            )
            self.btn_mount.config(
                text="▶ 연결 (드라이브 마운트)",
                bg=self.ACCENT_BLUE,
                activebackground=self.ACCENT_HOVER,
                state=tk.NORMAL,
            )
            if hasattr(self, "btn_top_mount"):
                self.btn_top_mount.config(
                    text="▶ 연결 (드라이브 마운트)",
                    bg=self.ACCENT_BLUE,
                    activebackground=self.ACCENT_HOVER,
                    state=tk.NORMAL,
                )
            self.btn_explorer.config(state=tk.DISABLED)

    def save_current_profile(self):
        """Saves current form data to profiles.json."""
        data = self._get_form_data()
        if data["storage_type"] == "gdrive":
            if not data["gdrive_token"]:
                messagebox.showwarning("입력 확인", "구글 계정 간편 로그인 또는 인증 토큰을 입력해 주세요.")
                return
        elif data["storage_type"] == "mybox":
            if not data["mybox_token"]:
                messagebox.showwarning("입력 확인", "네이버 MYBOX 개인 액세스 토큰(PAT)을 입력해 주세요.")
                return
        else:
            if not data["host"]:
                messagebox.showwarning("입력 확인", "WebDAV 주소(호스트)를 입력해 주세요.")
                return

        saved_id = ConfigManager.save_profile(data)
        self.current_profile_id = saved_id
        self.lbl_log.config(text=f"'{data['name']}' 프로필이 저장되었습니다.")
        self.refresh_profiles_list()

    def delete_current_profile(self):
        """Deletes currently selected profile."""
        if not self.current_profile_id:
            messagebox.showinfo("알림", "삭제할 저장된 프로필이 없습니다.")
            return

        p_name = self.entry_name.get()
        if messagebox.askyesno("삭제 확인", f"'{p_name}' 프로필을 정말 삭제하시겠습니까?"):
            ConfigManager.delete_profile(self.current_profile_id)
            self.current_profile_id = None
            self.refresh_profiles_list()
            self.lbl_log.config(text="프로필이 삭제되었습니다.")

    def test_connection(self):
        """Tests WebDAV, Naver MYBOX, or Google Drive connection asynchronously."""
        data = self._get_form_data()
        self.btn_test.config(state=tk.DISABLED, text="테스트 중...")

        if data["storage_type"] == "gdrive":
            token = data.get("gdrive_token", "")
            if not token:
                self.btn_test.config(state=tk.NORMAL, text="🔍 연결 테스트")
                messagebox.showwarning("확인", "구글 계정 간편 로그인 또는 인증 토큰을 먼저 입력해 주세요.")
                return

            self.lbl_log.config(text="구글 드라이브 토큰 유효성 및 연결 확인 중...")

            def gdrive_worker():
                ok, msg = StreamingMounter.test_gdrive_connection(
                    token_json=token,
                    root_folder_id=data.get("gdrive_root_id", ""),
                )
                self.after(0, lambda: self._on_test_done(ok, msg))

            threading.Thread(target=gdrive_worker, daemon=True).start()
        elif data["storage_type"] == "mybox":
            token = data["mybox_token"]
            if not token:
                self.btn_test.config(state=tk.NORMAL, text="🔍 연결 테스트")
                messagebox.showwarning("확인", "네이버 MYBOX 개인 액세스 토큰(PAT)을 먼저 입력해 주세요.")
                return

            self.lbl_log.config(text="네이버 MYBOX API 토큰 인증 확인 중...")

            def mybox_worker():
                client = MyBoxClient(token)
                ok, msg, count = client.test_connection()
                self.after(0, lambda: self._on_test_done(ok, msg))

            threading.Thread(target=mybox_worker, daemon=True).start()
        else:
            if not data["host"]:
                self.btn_test.config(state=tk.NORMAL, text="🔍 연결 테스트")
                messagebox.showwarning("확인", "서버 주소를 먼저 입력해 주세요.")
                return

            url = MountManager.format_webdav_url(
                data["host"], data["port"], data["ssl"], data["path"]
            )
            self.lbl_log.config(text=f"연결 테스트 중: {url}")

            def worker():
                ok, msg = MountManager.test_connection(url, data["username"], data["password"])
                self.after(0, lambda: self._on_test_done(ok, msg))

            threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ok: bool, msg: str):
        self.btn_test.config(state=tk.NORMAL, text="🔍 연결 테스트")
        self.lbl_log.config(text=msg)
        if ok:
            messagebox.showinfo("연결 테스트 성공", msg)
        else:
            messagebox.showerror("연결 테스트 실패", msg)

    def toggle_mount(self):
        """Mounts or unmounts the current drive asynchronously."""
        if getattr(self, "is_busy", False):
            return

        data = self._get_form_data()
        drive = data["drive_letter"] or "Z:"
        if not drive.endswith(":"):
            drive += ":"
        storage_type = data.get("storage_type", "webdav")

        # Determine user intent from the active button text to avoid desync
        btn_text = self.btn_mount.cget("text")
        should_unmount = ("해제" in btn_text) or ("⏹" in btn_text)

        if should_unmount:
            # ----------------- UNMOUNT ACTION -----------------
            self.is_busy = True
            self.btn_mount.config(state=tk.DISABLED, text="연결 해제 중...")
            if hasattr(self, "btn_top_mount"):
                self.btn_top_mount.config(state=tk.DISABLED, text="연결 해제 중...")
            self.lbl_log.config(text=f"{drive} 연결 해제 진행 중...")
            self.update_idletasks()

            def unmount_worker():
                ok = False
                msg = "연결을 해제할 수 없습니다."
                try:
                    StreamingMounter.unmount_streaming(drive)
                    ok, msg = MountManager.unmount(drive)
                    if storage_type == "mybox":
                        if self.current_profile_id:
                            MyBoxGatewayManager.stop_gateway(self.current_profile_id)
                        MyBoxGatewayManager.stop_gateway(drive)
                except Exception as e:
                    ok = False
                    msg = f"해제 중 오류 발생: {str(e)}"
                finally:
                    self.after(0, lambda: self._on_unmount_done(ok, msg, drive))

            threading.Thread(target=unmount_worker, daemon=True).start()
        else:
            # ----------------- MOUNT ACTION -----------------
            if storage_type == "gdrive":
                if not data["gdrive_token"]:
                    messagebox.showwarning("입력 확인", "구글 계정 간편 로그인 또는 인증 토큰을 입력해 주세요.")
                    return
            elif storage_type == "mybox":
                if not data["mybox_token"]:
                    messagebox.showwarning("입력 확인", "네이버 MYBOX 개인 액세스 토큰(PAT)을 입력해 주세요.")
                    return
            else:
                if not data["host"]:
                    messagebox.showwarning("입력 확인", "서버 주소(호스트)를 입력해 주세요.")
                    return

            # Silently save profile data without tearing down UI
            try:
                saved_id = ConfigManager.save_profile(data)
                self.current_profile_id = saved_id
                data["id"] = saved_id
            except Exception:
                pass

            self.is_busy = True
            self.btn_mount.config(state=tk.DISABLED, text="드라이브 연결 중...")
            if hasattr(self, "btn_top_mount"):
                self.btn_top_mount.config(state=tk.DISABLED, text="드라이브 연결 중...")
            use_streaming = data.get("streaming", True)
            p_name = data.get("name", drive)
            self.lbl_log.config(text=f"'{p_name}' ({drive}) 드라이브 연결 시작...")
            self.update_idletasks()

            def mount_worker():
                def progress(txt):
                    self.after(0, lambda t=txt: self.lbl_log.config(text=t))

                ok = False
                msg = f"{drive} 연결 실패"
                try:
                    if storage_type == "gdrive":
                        progress(f"{drive} 구글 드라이브 고속 연결 중...")
                        ok, msg = StreamingMounter.mount_gdrive(
                            drive_letter=drive,
                            profile_name=data["name"],
                            token_json=data["gdrive_token"],
                            root_folder_id=data.get("gdrive_root_id", ""),
                            progress_callback=progress,
                        )
                    elif storage_type == "mybox":
                        progress("네이버 MYBOX 게이트웨이 시작 중...")
                        profile_key = self.current_profile_id or drive
                        gw_ok, gw_msg, port = MyBoxGatewayManager.start_gateway(profile_key, data["mybox_token"])
                        if not gw_ok:
                            ok = False
                            msg = gw_msg
                        else:
                            if use_streaming:
                                progress(f"{drive} 초고속 스트리밍 마운트 중 (포트 {port})...")
                                ok, msg = StreamingMounter.mount_streaming(
                                    drive,
                                    data["name"],
                                    "127.0.0.1",
                                    port,
                                    False,
                                    "/",
                                    "mybox",
                                    "token",
                                    progress_callback=progress,
                                )
                            else:
                                progress(f"{drive} Windows WebDAV 드라이브 연결 중 (포트 {port})...")
                                url = f"http://127.0.0.1:{port}/"
                                ok, msg = MountManager.mount(drive, url, "mybox", "token")
                    else:
                        progress(f"{drive} WebDAV 드라이브 연결 중 ({data['host']})...")
                        if use_streaming:
                            ok, msg = StreamingMounter.mount_streaming(
                                drive,
                                data["name"],
                                data["host"],
                                data["port"],
                                data["ssl"],
                                data["path"],
                                data["username"],
                                data["password"],
                                progress_callback=progress,
                            )
                        else:
                            url = MountManager.format_webdav_url(data["host"], data["port"], data["ssl"], data["path"])
                            ok, msg = MountManager.mount(drive, url, data["username"], data["password"])
                except Exception as e:
                    ok = False
                    msg = f"마운트 중 예외 발생: {str(e)}"
                finally:
                    self.after(0, lambda: self._on_mount_done(ok, msg, drive))

            threading.Thread(target=mount_worker, daemon=True).start()

    def _on_mount_done(self, ok: bool, msg: str, drive: str):
        self.is_busy = False
        self.btn_mount.config(state=tk.NORMAL)
        if hasattr(self, "btn_top_mount"):
            self.btn_top_mount.config(state=tk.NORMAL)
        self.lbl_log.config(text=msg)
        self._update_mount_status_ui()
        self.refresh_profiles_list()

        if ok:
            # Offer to open explorer
            if messagebox.askyesno(
                "연결 완료",
                f"{drive} 드라이브로 성공적으로 연결되었습니다.\n파일 탐색기에서 지금 여시겠습니까?",
            ):
                MountManager.open_in_explorer(drive)
        else:
            messagebox.showerror("마운트 실패", msg)

    def _on_unmount_done(self, ok: bool, msg: str, drive: str):
        self.is_busy = False
        self.btn_mount.config(state=tk.NORMAL)
        if hasattr(self, "btn_top_mount"):
            self.btn_top_mount.config(state=tk.NORMAL)
        self.lbl_log.config(text=msg)
        self._update_mount_status_ui()
        self.refresh_profiles_list()

        if not ok:
            messagebox.showerror("해제 실패", msg)

    def open_explorer(self):
        """Opens Windows Explorer at the selected drive."""
        drive = self.combo_drive.get().strip() or "Z:"
        if not drive.endswith(":"):
            drive += ":"
        if not MountManager.open_in_explorer(drive):
            messagebox.showerror(
                "오류",
                f"{drive} 드라이브를 탐색기에서 열 수 없습니다.\n드라이브가 정상적으로 마운트되어 있는지 확인해 주세요.",
            )

    def open_optimizer_dialog(self):
        """Opens modal dialog for Windows WebDAV optimization."""
        OptimizerDialog(self)


class OptimizerDialog(tk.Toplevel):
    """Dialog to inspect and fix Windows WebClient & registry settings."""

    def __init__(self, parent: WebDAVApp):
        super().__init__(parent)
        self.title("Windows WebDAV 환경 최적화")
        self.geometry("540x440")
        self.configure(bg="#ffffff")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.check_status()

    def _build_ui(self):
        pad = 18
        tk.Label(
            self,
            text="⚡ Windows WebDAV 4GB 최적화 진단",
            font=WebDAVApp.FONT_TITLE,
            bg="#ffffff",
            fg=WebDAVApp.TEXT_MAIN,
        ).pack(anchor="w", padx=pad, pady=(pad, 6))

        tk.Label(
            self,
            text="Windows 기본 WebDAV의 50MB 파일 크기 제한을 4GB로 자동 해제하고,\nWebClient 서비스 상태 및 HTTP BasicAuth 연결을 점검합니다.",
            font=WebDAVApp.FONT_LABEL,
            bg="#ffffff",
            fg=WebDAVApp.TEXT_MUTED,
            justify=tk.LEFT,
        ).pack(anchor="w", padx=pad, pady=(0, 14))

        # Status frame
        self.status_box = tk.Frame(
            self,
            bg="#f8f9fa",
            padx=16,
            pady=16,
            bd=1,
            relief=tk.SOLID,
            highlightbackground=WebDAVApp.BORDER_LIGHT,
            highlightthickness=1,
        )
        self.status_box.pack(fill=tk.BOTH, expand=True, padx=pad, pady=6)

        # Item 1: WebClient Service
        self.lbl_service = tk.Label(
            self.status_box,
            text="• WebClient 서비스: 확인 중...",
            font=WebDAVApp.FONT_LABEL,
            bg="#f8f9fa",
            fg=WebDAVApp.TEXT_MAIN,
            anchor="w",
        )
        self.lbl_service.pack(fill=tk.X, pady=6)

        # Item 2: BasicAuthLevel
        self.lbl_auth = tk.Label(
            self.status_box,
            text="• BasicAuth 레지스트리: 확인 중...",
            font=WebDAVApp.FONT_LABEL,
            bg="#f8f9fa",
            fg=WebDAVApp.TEXT_MAIN,
            anchor="w",
        )
        self.lbl_auth.pack(fill=tk.X, pady=6)

        # Item 3: FileSizeLimitInBytes
        self.lbl_size = tk.Label(
            self.status_box,
            text="• 파일 크기 제한 레지스트리: 확인 중...",
            font=WebDAVApp.FONT_LABEL,
            bg="#f8f9fa",
            fg=WebDAVApp.TEXT_MAIN,
            anchor="w",
        )
        self.lbl_size.pack(fill=tk.X, pady=6)

        # Item 4: VFS Cache Status & Purge
        self.lbl_cache = tk.Label(
            self.status_box,
            text="• 로컬 VFS 디스크 캐시: 확인 중...",
            font=WebDAVApp.FONT_LABEL,
            bg="#f8f9fa",
            fg=WebDAVApp.TEXT_MAIN,
            anchor="w",
        )
        self.lbl_cache.pack(fill=tk.X, pady=4)

        self.lbl_cache_path = tk.Label(
            self.status_box,
            text="• 캐시 저장 경로: 확인 중...",
            font=WebDAVApp.FONT_SMALL,
            bg="#f8f9fa",
            fg=WebDAVApp.TEXT_MUTED,
            anchor="w",
            justify=tk.LEFT,
        )
        self.lbl_cache_path.pack(fill=tk.X, pady=(0, 6))

        # Admin notice
        self.lbl_notice = tk.Label(
            self.status_box,
            text="※ 업로드 캐시는 완료 후 1시간 내 자동 정제되며, [캐시 경로 변경]으로 D: 드라이브 지정을 할 수 있습니다.",
            font=WebDAVApp.FONT_SMALL,
            bg="#f8f9fa",
            fg="#d97706",
            anchor="w",
            justify=tk.LEFT,
        )
        self.lbl_notice.pack(fill=tk.X, pady=(6, 0))

        # Buttons
        btn_frame = tk.Frame(self, bg="#ffffff")
        btn_frame.pack(fill=tk.X, padx=pad, pady=pad)

        self.btn_apply = tk.Button(
            btn_frame,
            text="🚀 4GB 최적화 재적용",
            font=WebDAVApp.FONT_BUTTON,
            bg=WebDAVApp.ACCENT_BLUE,
            fg="#ffffff",
            activebackground=WebDAVApp.ACCENT_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            padx=8,
            pady=8,
            cursor="hand2",
            command=self.apply_optimization,
        )
        self.btn_apply.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_change_dir = tk.Button(
            btn_frame,
            text="📁 경로 변경",
            font=WebDAVApp.FONT_BUTTON,
            bg="#f1f5f9",
            fg=WebDAVApp.TEXT_MAIN,
            activebackground="#e2e8f0",
            activeforeground=WebDAVApp.TEXT_MAIN,
            bd=1,
            relief=tk.SOLID,
            highlightthickness=0,
            padx=8,
            pady=8,
            cursor="hand2",
            command=self.change_cache_dir,
        )
        self.btn_change_dir.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_purge_cache = tk.Button(
            btn_frame,
            text="🧹 캐시 비우기",
            font=WebDAVApp.FONT_BUTTON,
            bg="#059669",
            fg="#ffffff",
            activebackground="#047857",
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            padx=8,
            pady=8,
            cursor="hand2",
            command=self.purge_vfs_cache,
        )
        self.btn_purge_cache.pack(side=tk.LEFT, padx=(0, 4))

        close_btn = tk.Button(
            btn_frame,
            text="닫기",
            font=WebDAVApp.FONT_BUTTON,
            bg="#ffffff",
            fg=WebDAVApp.TEXT_MAIN,
            bd=1,
            relief=tk.SOLID,
            padx=12,
            pady=8,
            cursor="hand2",
            command=self.destroy,
        )
        close_btn.pack(side=tk.RIGHT)

    def check_status(self):
        """Inspects environment and updates labels."""
        # 1. WebClient Service
        svc_ok, svc_msg = MountManager.check_webclient_service()
        if svc_ok:
            self.lbl_service.config(
                text="✔ WebClient 서비스: 실행 중 (정상)", fg=WebDAVApp.ACCENT_GREEN
            )
        else:
            self.lbl_service.config(
                text=f"✖ WebClient 서비스: {svc_msg} (시작 필요)", fg=WebDAVApp.ACCENT_RED
            )

        # 2. Registry
        reg = MountManager.check_registry_settings()
        auth_level = reg.get("BasicAuthLevel")
        if reg.get("BasicAuthLevel_ok"):
            self.lbl_auth.config(
                text=f"✔ BasicAuth 설정: 레벨 {auth_level} (HTTP/HTTPS 모두 허용 - 정상)",
                fg=WebDAVApp.ACCENT_GREEN,
            )
        else:
            self.lbl_auth.config(
                text=f"✖ BasicAuth 설정: 레벨 {auth_level} (HTTP 비-SSL 차단됨 -> 2로 변경 권장)",
                fg=WebDAVApp.ACCENT_RED,
            )

        limit_bytes = reg.get("FileSizeLimitInBytes", 0)
        limit_mb = round((limit_bytes or 0) / (1024 * 1024))
        if reg.get("FileSizeLimit_ok"):
            self.lbl_size.config(
                text=f"✔ 파일 크기 제한: 약 {limit_mb}MB (4GB 최대 전송 적용됨 - 정상)",
                fg=WebDAVApp.ACCENT_GREEN,
            )
        else:
            self.lbl_size.config(
                text=f"✖ 파일 크기 제한: 약 {limit_mb}MB (기본 50MB 제한 -> 4GB로 확장 필요)",
                fg=WebDAVApp.ACCENT_RED,
            )

        # 3. VFS Cache Status & Path
        cache_dir = StreamingMounter.get_cache_dir()
        cache_bytes = 0
        file_cnt = 0
        if os.path.exists(cache_dir):
            for root, _, files in os.walk(cache_dir):
                for f in files:
                    try:
                        cache_bytes += os.path.getsize(os.path.join(root, f))
                        file_cnt += 1
                    except Exception:
                        pass
        cache_mb = cache_bytes / (1024 * 1024)
        max_age, max_size = StreamingMounter.get_cache_settings()
        self.lbl_cache.config(
            text=f"✔ VFS 캐시 사용량: {cache_mb:.1f}MB ({file_cnt}개 파일) [자동 정제: {max_age}, 최대: {max_size}]",
            fg=WebDAVApp.ACCENT_GREEN if cache_mb < 1000 else "#d97706",
        )
        custom_setting = ConfigManager.get_setting("custom_cache_dir", "")
        path_tag = f"(사용자 지정: {custom_setting})" if custom_setting else "(기본 C: 드라이브 AppData)"
        self.lbl_cache_path.config(
            text=f"📍 위치: {cache_dir}\n   {path_tag}"
        )

    def change_cache_dir(self):
        """Allows changing cache folder from OptimizerDialog."""
        fn = getattr(self.master, "change_cache_dir_ui", None)
        if callable(fn):
            fn()
            self.check_status()

    def purge_vfs_cache(self):
        """Clears local VFS cache files."""
        ok, msg = StreamingMounter.purge_cache()
        self.check_status()
        if ok:
            messagebox.showinfo("캐시 비우기 완료", msg)
        else:
            messagebox.showwarning("캐시 비우기 실패", msg)

    def apply_optimization(self):
        """Applies optimizations."""
        self.btn_apply.config(state=tk.DISABLED, text="적용 중...")

        def worker():
            # 1. Start WebClient service
            MountManager.ensure_webclient_service()
            # 2. Optimize registry
            reg_ok, reg_msg = MountManager.optimize_registry_settings()

            def done():
                self.btn_apply.config(
                    state=tk.NORMAL,
                    text="🚀 4GB 최적화 재적용",
                )
                self.check_status()
                if reg_ok:
                    messagebox.showinfo("최적화 완료", reg_msg)
                else:
                    messagebox.showwarning(
                        "권한 확인",
                        f"{reg_msg}\n\n레지스트리 수정을 위해 프로그램을 '관리자 권한으로 실행'해 주시기 바랍니다.",
                    )

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

