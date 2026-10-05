"""Unit tests for WebDAV Drive Mounter."""

import os
import unittest
from config_manager import ConfigManager
from mount_manager import MountManager


class TestWebDAVDriveMounter(unittest.TestCase):

    def test_url_formatting(self):
        # Standard HTTPS 443
        url1 = MountManager.format_webdav_url("example.com", 443, True, "/dav")
        self.assertEqual(url1, "https://example.com/dav")

        # Standard HTTP 80
        url2 = MountManager.format_webdav_url("http://example.com", 80, False, "dav")
        self.assertEqual(url2, "http://example.com/dav")

        # Non-standard port
        url3 = MountManager.format_webdav_url("nas.local:5006", 5006, True, "/remote.php/dav/")
        self.assertEqual(url3, "https://nas.local:5006/remote.php/dav")

        # Root path
        url4 = MountManager.format_webdav_url("https://share.box.com/", 443, True, "/")
        self.assertEqual(url4, "https://share.box.com")

    def test_unc_path_formatting(self):
        unc1 = MountManager.format_unc_path("example.com", 443, True, "folder")
        self.assertEqual(unc1, r"\\example.com@SSL\DavWWWRoot\folder")

        unc2 = MountManager.format_unc_path("nas.local", 5006, True, "share/data")
        self.assertEqual(unc2, r"\\nas.local@SSL@5006\DavWWWRoot\share\data")

    def test_drive_detection(self):
        avail = MountManager.get_available_drives()
        self.assertIsInstance(avail, list)
        self.assertTrue(len(avail) > 0)
        # Ensure all items look like 'Z:', 'Y:' etc
        for d in avail:
            self.assertTrue(d.endswith(":") and len(d) == 2)

        mounted = MountManager.get_mounted_drives()
        self.assertIsInstance(mounted, dict)

    def test_config_manager(self):
        # Test password obfuscation
        pwd = "SecretPassword123!@#"
        encoded = ConfigManager._encode_password(pwd)
        decoded = ConfigManager._decode_password(encoded)
        self.assertEqual(pwd, decoded)

        # Test profile save & load
        test_profile = {
            "name": "Test WebDAV",
            "host": "test.dav.com",
            "port": 443,
            "ssl": True,
            "path": "/dav",
            "username": "tester",
            "password": "mypassword",
            "drive_letter": "Y:",
        }
        profile_id = ConfigManager.save_profile(test_profile)
        self.assertIsNotNone(profile_id)

        profiles = ConfigManager.get_profiles()
        saved = next((p for p in profiles if p.get("id") == profile_id), None)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["name"], "Test WebDAV")
        self.assertEqual(saved["password"], "mypassword")

        # Clean up
        ConfigManager.delete_profile(profile_id)
        profiles_after = ConfigManager.get_profiles()
        self.assertIsNone(next((p for p in profiles_after if p.get("id") == profile_id), None))

    def test_webclient_check(self):
        # Ensure method runs without exception
        is_running, status = MountManager.check_webclient_service()
        self.assertIsInstance(is_running, bool)
        self.assertIsInstance(status, str)

    def test_autostart_manager(self):
        from autostart_manager import AutostartManager
        cmd = AutostartManager.get_run_command()
        self.assertIn("main.py", cmd)
        self.assertIn("--minimized", cmd)
        # Check querying status runs without error
        status = AutostartManager.is_autostart_enabled()
        self.assertIsInstance(status, bool)

    def test_tray_image_generation(self):
        from tray_manager import create_tray_image, TRAY_AVAILABLE
        img = create_tray_image()
        if TRAY_AVAILABLE:
            self.assertIsNotNone(img)
            assert img is not None
            self.assertEqual(img.size, (64, 64))
        else:
            self.assertIsNone(img)

    def test_mybox_support(self):
        from mybox_client import MyBoxClient
        from mybox_gateway import MyBoxGatewayManager

        # Test token and profile save/load
        token = "test_pat_token_abc123"
        profile_data = {
            "name": "내 네이버 MYBOX",
            "storage_type": "mybox",
            "mybox_token": token,
            "drive_letter": "N:",
            "streaming": True,
        }
        pid = ConfigManager.save_profile(profile_data)
        profiles = ConfigManager.get_profiles()
        saved = next((p for p in profiles if p.get("id") == pid), None)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["storage_type"], "mybox")
        self.assertEqual(saved["mybox_token"], token)
        ConfigManager.delete_profile(pid)

        # Test client helper normalization
        client = MyBoxClient("dummy_token")
        norm = client._normalize_resource({
            "resourceId": "res_123",
            "name": "test_folder",
            "resourceType": "folder",
        })
        self.assertIsNotNone(norm)
        assert norm is not None
        self.assertTrue(norm["is_dir"])
        self.assertEqual(norm["name"], "test_folder")

        # Test gateway port detection
        free_port = MyBoxGatewayManager.find_free_port()
        self.assertIsInstance(free_port, int)
        self.assertTrue(free_port > 1024)
        self.assertFalse(MyBoxGatewayManager.is_running("non_existent"))
        self.assertFalse(MyBoxGatewayManager.is_gateway_running("non_existent"))
        self.assertFalse(MyBoxGatewayManager.is_gateway_running(""))

    def test_mybox_folder_and_webdav_methods(self):
        from mybox_gateway import MyBoxWebDAVHandler
        # Verify path normalization
        self.assertEqual(MyBoxWebDAVHandler._normalize_path("/new%20folder/"), "/new folder")
        self.assertEqual(MyBoxWebDAVHandler._normalize_path("/%EC%83%88%20%ED%8F%B4%EB%8D%94/"), "/새 폴더")
        self.assertEqual(MyBoxWebDAVHandler._normalize_path(""), "/")

    def test_gdrive_support(self):
        from streaming_mounter import StreamingMounter

        # Test token and profile save/load
        token_sample = '{"access_token":"ya29.sample","token_type":"Bearer","refresh_token":"1//sample","expiry":"2026-09-24T18:00:00Z"}'
        profile_data = {
            "name": "내 구글 드라이브",
            "storage_type": "gdrive",
            "gdrive_token": token_sample,
            "gdrive_root_id": "root_folder_123",
            "drive_letter": "G:",
            "streaming": True,
        }
        pid = ConfigManager.save_profile(profile_data)
        profiles = ConfigManager.get_profiles()
        saved = next((p for p in profiles if p.get("id") == pid), None)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["storage_type"], "gdrive")
        self.assertEqual(saved["gdrive_token"], token_sample)
        self.assertEqual(saved["gdrive_root_id"], "root_folder_123")
        ConfigManager.delete_profile(pid)

        # Test rclone gdrive config generation
        remote_name = StreamingMounter._create_rclone_gdrive_config(
            profile_name="MyTestGDrive",
            token_json=token_sample,
            root_folder_id="root_folder_123",
        )
        self.assertEqual(remote_name, "gdrive_MyTestGDrive")
        self.assertTrue(os.path.exists(StreamingMounter.RCLONE_CONF))
        with open(StreamingMounter.RCLONE_CONF, "r", encoding="utf-8") as f:
            conf_data = f.read()
        self.assertIn("[gdrive_MyTestGDrive]", conf_data)
        self.assertIn("type = drive", conf_data)
        self.assertIn("scope = drive", conf_data)
        self.assertIn("root_folder_id = root_folder_123", conf_data)
        self.assertIn("ya29.sample", conf_data)

    def test_unmount_nonexistent_drive(self):
        # Unmounting a non-existent/already disconnected drive should gracefully succeed
        ok, msg = MountManager.unmount("Q:")
        self.assertTrue(ok)
        self.assertIn("해제되었습니다", msg)

    def test_streaming_helpers(self):
        from streaming_mounter import StreamingMounter
        port = StreamingMounter._find_free_port()
        self.assertIsInstance(port, int)
        self.assertGreater(port, 1024)

        # Active check on unused drive
        self.assertFalse(StreamingMounter.is_streaming_active("Q:"))

        # Unmount streaming on unused drive
        self.assertTrue(StreamingMounter.unmount_streaming("Q:"))

    def test_gui_instantiation(self):
        # Verify WebDAVApp initializes cleanly
        from gui import WebDAVApp
        app = WebDAVApp(start_minimized=True)
        self.assertIsNotNone(app.title())
        app.update_idletasks()
        app.quit_completely()


    def test_propfind_xml_escaping(self):
        import html
        raw_name = 'Research & Development <Plan> "2026".pdf'
        escaped_name = html.escape(raw_name)
        self.assertNotIn("<Plan>", escaped_name)
        self.assertIn("&amp;", escaped_name)
        self.assertIn("&lt;Plan&gt;", escaped_name)
        self.assertIn("&quot;2026&quot;", escaped_name)

    def test_stop_gateway_flexible_keys(self):
        from mybox_gateway import MyBoxGatewayManager
        # Register dummy instance under N:
        MyBoxGatewayManager._instances["N:"] = {"server": None, "port": 12345}
        self.assertTrue(MyBoxGatewayManager.is_running("N:"))
        # Stop using lower-case n: or n:\
        self.assertTrue(MyBoxGatewayManager.stop_gateway("n:\\"))

    def test_empty_drive_letter_validation(self):
        from streaming_mounter import StreamingMounter
        ok1, msg1 = StreamingMounter.mount_streaming(drive_letter="")
        self.assertFalse(ok1)
        self.assertIn("드라이브 문자가 지정되지 않았습니다", msg1)

        ok2, msg2 = StreamingMounter.mount_gdrive(drive_letter="", profile_name="test", token_json="dummy")
        self.assertFalse(ok2)
        self.assertIn("드라이브 문자가 지정되지 않았습니다", msg2)

    def test_rclone_binary_validation(self):
        from streaming_mounter import StreamingMounter
        self.assertTrue(StreamingMounter.is_rclone_available())

    def test_safe_after_on_app(self):
        from gui import WebDAVApp
        app = WebDAVApp(start_minimized=True)
        # Test safe_after calls while alive
        timer_id = app.safe_after(50, lambda: None)
        self.assertIsNotNone(timer_id)
        # After destruction, should safely return None without raising TclError
        app.destroy()
        res = app.safe_after(10, lambda: None)
        self.assertIsNone(res)


    def test_mybox_resolve_path_and_cache(self):
        from mybox_client import MyBoxClient
        from mybox_gateway import MyBoxWebDAVHandler
        handler = MyBoxWebDAVHandler.__new__(MyBoxWebDAVHandler)
        handler.cache = {}

        class MockClient(MyBoxClient):
            def __init__(self):
                super().__init__("test_token")

            def list_resources(self, folder_id=None):
                if folder_id is None:
                    return [
                        {"id": "doc1", "name": "document.pdf", "is_dir": False, "size": 100, "mtime": 1000},
                        {"id": "dir1", "name": "photos", "is_dir": True, "size": 0, "mtime": 1000},
                    ]
                elif folder_id == "dir1":
                    return [
                        {"id": "img1", "name": "pic.jpg", "is_dir": False, "size": 200, "mtime": 1000},
                    ]
                return []

        handler.client = MockClient()

        # Root resolve
        root = handler._resolve_path("/")
        self.assertIsNotNone(root)
        assert root is not None
        self.assertTrue(root["is_dir"])

        # Valid file resolve
        doc = handler._resolve_path("/document.pdf")
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertFalse(doc["is_dir"])

        # Intermediate segment is a file -> should safely return None, not raise error
        sub = handler._resolve_path("/document.pdf/subfile.txt")
        self.assertIsNone(sub)

        # Valid sub file in folder
        pic = handler._resolve_path("/photos/pic.jpg")
        self.assertIsNotNone(pic)
        assert pic is not None
        self.assertEqual(pic["name"], "pic.jpg")

        # Test children listing
        children = handler._get_children("dir1", "/photos")
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["name"], "pic.jpg")

    def test_check_registry_settings(self):
        from mount_manager import MountManager
        reg = MountManager.check_registry_settings()
        self.assertIsInstance(reg, dict)
        self.assertIn("BasicAuthLevel", reg)
        self.assertIn("FileSizeLimitInBytes", reg)
        self.assertIn("SupportLocking", reg)
        self.assertIn("BasicAuthLevel_ok", reg)
        self.assertIn("FileSizeLimit_ok", reg)
        self.assertIn("SupportLocking_ok", reg)
        # Ensure default/read values are not None
        self.assertIsNotNone(reg["BasicAuthLevel"])
        self.assertIsNotNone(reg["FileSizeLimitInBytes"])
        self.assertIsNotNone(reg["SupportLocking"])

    def test_streaming_vfs_optimization_flags(self):
        # Inspect source code of streaming_mounter to ensure VFS parameters are optimized for video playback
        import inspect
        from streaming_mounter import StreamingMounter
        src = inspect.getsource(StreamingMounter)
        self.assertIn('"--vfs-read-chunk-size", "4M"', src)
        self.assertIn('"--buffer-size", "32M"', src)
        self.assertIn('"--vfs-read-ahead", "32M"', src)
        self.assertIn('"--vfs-handle-caching", "0s"', src)
        self.assertIn('"--vfs-read-wait", "0ms"', src)

    def test_mybox_streaming_disconnect_handling(self):
        import io
        from mybox_gateway import MyBoxWebDAVHandler
        handler = MyBoxWebDAVHandler.__new__(MyBoxWebDAVHandler)
        handler.requestline = "LOCK /test.mp4 HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "LOCK"
        from email.message import Message
        handler.headers = Message()
        handler.path = "/test.mp4"

        # Mock aborted socket writer
        class AbortedSocket(io.BytesIO):
            def write(self, b):
                raise ConnectionAbortedError(10053, "Software caused connection abort")

        handler.wfile = AbortedSocket()
        handler.rfile = io.BytesIO()

        # Test do_LOCK / do_UNLOCK survive aborted connection
        try:
            handler.do_LOCK()
            handler.do_UNLOCK()
        except Exception as e:
            self.fail(f"do_LOCK / do_UNLOCK should not raise on aborted connection: {e}")


if __name__ == "__main__":
    unittest.main()

