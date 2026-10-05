"""System Tray Icon Manager for WebDAV Drive Mounter using pystray."""

import threading
from typing import Callable, Optional
try:
    from PIL import Image, ImageDraw  # type: ignore
    import pystray  # type: ignore
    TRAY_AVAILABLE = True
except ImportError:
    Image = None
    ImageDraw = None
    pystray = None
    TRAY_AVAILABLE = False


def create_tray_image(size=(64, 64), color="#1a73e8"):
    """Dynamically creates a clean drive icon image for the system tray."""
    if not TRAY_AVAILABLE or Image is None or ImageDraw is None:
        return None
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw rounded-rectangle hard drive body
    # Outer base
    draw.rounded_rectangle([6, 12, 58, 52], radius=8, fill=color, outline="#1557b0", width=2)
    # Drive activity LED
    draw.ellipse([46, 22, 52, 28], fill="#2cb67d")
    # Horizontal slot line
    draw.line([14, 38, 50, 38], fill="#ffffff", width=3)
    # Cloud / DAV symbol (simple stylized arrow or cloud)
    draw.polygon([(26, 24), (38, 24), (32, 16)], fill="#ffffff")

    return img


class TrayManager:
    """Manages the system tray icon and background presence."""

    def __init__(
        self,
        on_show: Callable[[], None],
        on_exit: Callable[[], None],
        on_open_explorer: Optional[Callable[[], None]] = None,
    ):
        self.on_show = on_show
        self.on_exit = on_exit
        self.on_open_explorer = on_open_explorer
        self.icon = None
        self._thread: Optional[threading.Thread] = None

    def is_available(self) -> bool:
        """Returns True if tray icon is supported and active."""
        return TRAY_AVAILABLE and self.icon is not None

    def start(self):
        """Starts the tray icon in a background thread."""
        if not TRAY_AVAILABLE or pystray is None:
            return
        try:
            image = create_tray_image()
            if image is None:
                return

            menu_items = [
                pystray.MenuItem("⚡ 창 열기", lambda icon, item: self.on_show(), default=True),
            ]

            if self.on_open_explorer is not None:
                on_explore = self.on_open_explorer
                menu_items.append(
                    pystray.MenuItem("📁 마운트된 드라이브 열기", lambda icon, item: on_explore())
                )

            menu_items.extend([
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("❌ 프로그램 완전 종료", lambda icon, item: self.on_exit()),
            ])

            menu = pystray.Menu(*menu_items)

            self.icon = pystray.Icon(
                name="WebDAVDriveMounter",
                icon=image,
                title="WebDAV Drive Mounter (백그라운드 실행 중)",
                menu=menu,
            )

            self._thread = threading.Thread(target=self.icon.run, daemon=True)
            self._thread.start()
        except Exception as e:
            print(f"[TrayManager] Failed to start tray icon: {e}")

    def notify(self, title: str, message: str):
        """Displays a Windows balloon notification if supported."""
        if self.icon and hasattr(self.icon, "notify"):
            try:
                self.icon.notify(message, title)
            except Exception:
                pass

    def stop(self):
        """Stops the tray icon."""
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
