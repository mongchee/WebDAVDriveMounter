"""Local WebDAV Gateway for Naver MYBOX.

Provides a lightweight, zero-dependency WebDAV HTTP server on localhost
that translates WebDAV operations (PROPFIND, GET, PUT, MKCOL, DELETE)
into Naver MYBOX Open API calls.
"""

from datetime import datetime, timezone
from email.utils import formatdate
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Dict, List, Optional, Tuple

from mybox_client import MyBoxClient


class MyBoxWebDAVHandler(BaseHTTPRequestHandler):
    """Translates WebDAV methods to Naver MYBOX API calls."""

    client: MyBoxClient = None  # type: ignore
    # Path cache: {normalized_path: {"item": norm_item, "children": [norm_items], "time": timestamp}}
    cache: Dict[str, Dict] = {}
    cache_lock = threading.RLock()
    CACHE_TTL = 30.0  # seconds

    def log_message(self, format, *args):
        """Silences standard access logging for high-frequency WebDAV traffic."""
        pass

    @classmethod
    def _normalize_path(cls, path: str) -> str:
        """Normalizes URL path into standard /folder/file format."""
        path = urllib.parse.unquote(path).split("?")[0].rstrip("/")
        if not path:
            return "/"
        return path

    def _resolve_path(self, path: str) -> Optional[Dict]:
        """Resolves a WebDAV path to a MYBOX resource metadata dictionary.

        Returns None if not found, or root dict for '/'.
        """
        norm_path = self._normalize_path(path)
        if norm_path == "/":
            return {"id": "ROOT", "name": "/", "is_dir": True, "size": 0, "mtime": time.time()}

        now = time.time()
        with self.cache_lock:
            cached = self.cache.get(norm_path)
            if cached and (now - cached["time"] < self.CACHE_TTL):
                return cached.get("item")

        # Walk from root downwards
        parts = [p for p in norm_path.strip("/").split("/") if p]
        curr_folder_id = None
        curr_item: Optional[Dict] = None

        for idx, part in enumerate(parts):
            curr_path = "/" + "/".join(parts[: idx + 1])
            with self.cache_lock:
                cached_entry = self.cache.get(curr_path)
                if cached_entry and (now - cached_entry["time"] < self.CACHE_TTL):
                    curr_item = cached_entry.get("item")
                    if not curr_item:
                        return None
                    # If intermediate path segment is not a directory, cannot have children
                    if idx < len(parts) - 1 and not curr_item.get("is_dir"):
                        return None
                    curr_folder_id = curr_item["id"] if curr_item.get("is_dir") else None
                    continue

            # Need to fetch directory contents of curr_folder_id
            children = self.client.list_resources(curr_folder_id)
            found = False
            case_matched = None
            part_lower = part.lower()
            for ch in children:
                ch_path = ("/" if curr_folder_id is None else "/" + "/".join(parts[:idx]) + "/") + ch["name"]
                ch_path = "/" + ch_path.strip("/")
                with self.cache_lock:
                    self.cache[ch_path] = {"item": ch, "time": now}
                if ch["name"] == part:
                    curr_item = ch
                    curr_folder_id = ch["id"] if ch.get("is_dir") else None
                    found = True
                    break
                elif case_matched is None and ch["name"].lower() == part_lower:
                    case_matched = ch

            if not found and case_matched is not None:
                curr_item = case_matched
                curr_folder_id = curr_item["id"] if curr_item.get("is_dir") else None
                found = True

            if not found or not curr_item:
                return None

            # If intermediate path segment is not a directory, cannot have children
            if idx < len(parts) - 1 and not curr_item.get("is_dir"):
                return None

        return curr_item

    def _get_children(self, folder_id: Optional[str], folder_path: str) -> List[Dict]:
        """Lists child items of a folder with caching."""
        norm_path = self._normalize_path(folder_path)
        now = time.time()

        with self.cache_lock:
            cached = self.cache.get(norm_path)
            if cached and "children" in cached and (now - cached["time"] < self.CACHE_TTL):
                return cached["children"]

        real_folder_id = None if folder_id == "ROOT" else folder_id
        children = self.client.list_resources(real_folder_id)

        with self.cache_lock:
            cached_entry = self.cache.get(norm_path)
            if cached_entry:
                cached_entry["children"] = children
                cached_entry["time"] = now
            else:
                item = {"id": folder_id or "ROOT", "name": os.path.basename(norm_path) or "/", "is_dir": True, "size": 0, "mtime": now}
                self.cache[norm_path] = {"item": item, "children": children, "time": now}

            for ch in children:
                sub_path = (norm_path.rstrip("/") + "/" + ch["name"]).rstrip("/")
                if not sub_path:
                    sub_path = "/"
                self.cache[sub_path] = {"item": ch, "time": now}

        return children

    def _invalidate_cache(self, path: Optional[str] = None):
        """Clears or invalidates path cache."""
        with self.cache_lock:
            if path:
                norm = self._normalize_path(path)
                keys_to_del = [k for k in self.cache if k == norm or k.startswith(norm + "/")]
                for k in keys_to_del:
                    self.cache.pop(k, None)
                # Also invalidate parent folder children
                parent = os.path.dirname(norm).replace("\\", "/")
                if parent in self.cache:
                    self.cache[parent].pop("children", None)
            else:
                self.cache.clear()

    def do_OPTIONS(self):
        """Handles WebDAV OPTIONS request."""
        self.send_response(200)
        self.send_header("DAV", "1, 2")
        self.send_header("MS-Author-Via", "DAV")
        self.send_header(
            "Allow",
            "OPTIONS, GET, HEAD, POST, PUT, DELETE, PROPFIND, PROPPATCH, MKCOL, COPY, MOVE, LOCK, UNLOCK",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PROPFIND(self):
        """Handles WebDAV PROPFIND request."""
        norm_path = self._normalize_path(self.path)
        item = self._resolve_path(norm_path)

        if not item:
            self.send_error(404, "File Not Found")
            return

        depth = self.headers.get("Depth", "1").strip().lower()

        # Build items to report
        items_to_report = [(norm_path, item)]
        if depth == "1" and item["is_dir"]:
            children = self._get_children(item["id"], norm_path)
            for ch in children:
                ch_path = (norm_path.rstrip("/") + "/" + ch["name"]).rstrip("/")
                items_to_report.append((ch_path, ch))

        xml_parts = [
            '<?xml version="1.0" encoding="utf-8"?>\n',
            '<D:multistatus xmlns:D="DAV:">\n',
        ]

        for p, meta in items_to_report:
            raw_href = urllib.parse.quote(p)
            if meta["is_dir"] and not raw_href.endswith("/"):
                raw_href += "/"
            href = html.escape(raw_href)

            item_mtime = meta.get("mtime", time.time())
            mtime_str = formatdate(item_mtime, usegmt=True)
            iso_ctime = datetime.fromtimestamp(item_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            size_val = meta.get("size", 0)
            display_name = meta["name"]
            if display_name == "/":
                display_name = "MYBOX"
            escaped_display_name = html.escape(display_name)
            etag = f'"{int(item_mtime)}_{size_val}"'

            res_type = "<D:resourcetype><D:collection/></D:resourcetype>" if meta["is_dir"] else "<D:resourcetype/>"

            xml_parts.append("  <D:response>\n")
            xml_parts.append(f"    <D:href>{href}</D:href>\n")
            xml_parts.append("    <D:propstat>\n")
            xml_parts.append("      <D:prop>\n")
            xml_parts.append(f"        {res_type}\n")
            xml_parts.append(f"        <D:displayname>{escaped_display_name}</D:displayname>\n")
            xml_parts.append(f"        <D:getlastmodified>{mtime_str}</D:getlastmodified>\n")
            xml_parts.append(f"        <D:creationdate>{iso_ctime}</D:creationdate>\n")
            xml_parts.append(f"        <D:getetag>{etag}</D:getetag>\n")
            if not meta["is_dir"]:
                ctype = mimetypes.guess_type(meta["name"])[0] or "application/octet-stream"
                xml_parts.append(f"        <D:getcontentlength>{size_val}</D:getcontentlength>\n")
                xml_parts.append(f"        <D:getcontenttype>{ctype}</D:getcontenttype>\n")
            xml_parts.append("      </D:prop>\n")
            xml_parts.append("      <D:status>HTTP/1.1 200 OK</D:status>\n")
            xml_parts.append("    </D:propstat>\n")
            xml_parts.append("  </D:response>\n")

        xml_parts.append("</D:multistatus>\n")
        body = "".join(xml_parts).encode("utf-8")

        self.send_response(207, "Multi-Status")
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        """Handles WebDAV HEAD request."""
        self._handle_get_or_head(is_head=True)

    def do_GET(self):
        """Handles WebDAV GET request, supporting chunked streaming and HTTP Range."""
        self._handle_get_or_head(is_head=False)

    def _handle_get_or_head(self, is_head: bool):
        norm_path = self._normalize_path(self.path)
        item = self._resolve_path(norm_path)

        if not item:
            self.send_error(404, "File Not Found")
            return

        if item["is_dir"]:
            # HTML directory index for browser preview
            children = self._get_children(item["id"], norm_path)
            html = f"<html><head><title>Index of {norm_path}</title></head><body><h1>Index of {norm_path}</h1><ul>"
            for ch in children:
                suffix = "/" if ch["is_dir"] else ""
                html += f'<li><a href="{ch["name"]}{suffix}">{ch["name"]}{suffix}</a> ({ch["size"]} bytes)</li>'
            html += "</ul></body></html>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not is_head:
                self.wfile.write(body)
            return

        # Fetch download URL from MYBOX API
        download_url = self.client.get_download_url(item["id"])
        if not download_url:
            self.send_error(502, "Failed to retrieve download URL from MYBOX")
            return

        # Forward request to Naver download URL with Range support
        req_headers = {"User-Agent": "WebDAV-Drive-Mounter/1.0"}
        range_header = self.headers.get("Range")
        if range_header:
            req_headers["Range"] = range_header

        try:
            req = urllib.request.Request(download_url, headers=req_headers, method="GET")
            with urllib.request.urlopen(req, timeout=15) as remote_res:
                status_code = remote_res.status
                self.send_response(status_code)

                # Forward headers and track content length
                content_len = None
                for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified"):
                    val = remote_res.headers.get(h)
                    if val:
                        self.send_header(h, val)
                        if h == "Content-Length" and val.strip().isdigit():
                            content_len = int(val.strip())
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                if not is_head:
                    # Stream bytes in 64KB chunks for rapid start, smooth seek sync, and instant exit
                    chunk_size = 65536
                    bytes_remaining = content_len
                    first_chunk = True
                    while True:
                        if bytes_remaining is not None and bytes_remaining <= 0:
                            break
                        to_read = chunk_size if bytes_remaining is None else min(chunk_size, bytes_remaining)
                        chunk = remote_res.read(to_read)
                        if not chunk:
                            break
                        if bytes_remaining is not None:
                            bytes_remaining -= len(chunk)
                        self.wfile.write(chunk)
                        if first_chunk:
                            self.wfile.flush()
                            first_chunk = False
        except urllib.error.HTTPError as e:
            try:
                self.send_error(e.code, f"MYBOX Download Error: {e.reason}")
            except Exception:
                pass
        except (ConnectionError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError, socket.error):
            # Client closed or aborted connection (e.g. seeking timeline or closing media player)
            pass
        except Exception as e:
            try:
                self.send_error(500, f"Streaming Error: {str(e)}")
            except Exception:
                pass


    def do_MKCOL(self):
        """Creates a new folder in MYBOX."""
        norm_path = self._normalize_path(self.path)

        # Drain request body if sent by client
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            _ = self.rfile.read(content_length)

        # RFC 4918: If resource already exists, return 405 Method Not Allowed
        existing = self._resolve_path(norm_path)
        if existing:
            self.send_error(405, "Collection Already Exists")
            return

        parent_path = os.path.dirname(norm_path).replace("\\", "/")
        folder_name = os.path.basename(norm_path)

        parent_item = self._resolve_path(parent_path)
        if not parent_item or not parent_item["is_dir"]:
            self.send_error(409, "Parent directory does not exist")
            return

        parent_id = None if parent_item["id"] == "ROOT" else parent_item["id"]
        ok, msg, new_id = self.client.create_folder(folder_name, parent_id)
        if ok:
            # Prime path cache immediately so that subsequent PROPFIND sees the new folder instantly
            new_item = {
                "id": new_id or f"DIR_{int(time.time() * 1000)}",
                "name": folder_name,
                "is_dir": True,
                "size": 0,
                "mtime": time.time(),
            }
            with self.cache_lock:
                self.cache[norm_path] = {"item": new_item, "time": time.time()}
            self._invalidate_cache(parent_path)

            self.send_response(201, "Created")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(500, msg)

    def do_PROPPATCH(self):
        """Handles WebDAV PROPPATCH request (required by Windows Explorer)."""
        norm_path = self._normalize_path(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            _ = self.rfile.read(content_length)

        href = urllib.parse.quote(norm_path)
        body = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<D:multistatus xmlns:D="DAV:">\n'
            '  <D:response>\n'
            f'    <D:href>{href}</D:href>\n'
            '    <D:propstat>\n'
            '      <D:prop/>\n'
            '      <D:status>HTTP/1.1 200 OK</D:status>\n'
            '    </D:propstat>\n'
            '  </D:response>\n'
            '</D:multistatus>\n'
        ).encode("utf-8")

        self.send_response(207, "Multi-Status")
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_LOCK(self):
        """Handles WebDAV LOCK request (required by Windows Explorer and WebClient)."""
        norm_path = self._normalize_path(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            _ = self.rfile.read(content_length)

        token = f"opaquelocktoken:{uuid.uuid4()}"
        href = urllib.parse.quote(norm_path)
        body = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<D:prop xmlns:D="DAV:">\n'
            '  <D:lockdiscovery>\n'
            '    <D:activelock>\n'
            '      <D:locktype><D:write/></D:locktype>\n'
            '      <D:lockscope><D:exclusive/></D:lockscope>\n'
            '      <D:depth>0</D:depth>\n'
            '      <D:owner><D:href>WebDAV-Drive-Mounter</D:href></D:owner>\n'
            '      <D:timeout>Second-3600</D:timeout>\n'
            f'      <D:locktoken><D:href>{token}</D:href></D:locktoken>\n'
            f'      <D:lockroot><D:href>{href}</D:href></D:lockroot>\n'
            '    </D:activelock>\n'
            '  </D:lockdiscovery>\n'
            '</D:prop>\n'
        ).encode("utf-8")

        try:
            self.send_response(200, "OK")
            self.send_header("Content-Type", 'application/xml; charset="utf-8"')
            self.send_header("Lock-Token", f"<{token}>")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, BrokenPipeError, OSError):
            pass

    def do_UNLOCK(self):
        """Handles WebDAV UNLOCK request."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                _ = self.rfile.read(content_length)
            self.send_response(204, "No Content")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (ConnectionError, BrokenPipeError, OSError):
            pass

    def do_MOVE(self):
        """Handles WebDAV MOVE request (used when renaming or moving files/folders)."""
        dest_header = self.headers.get("Destination", "")
        if not dest_header:
            self.send_error(400, "Destination header missing")
            return

        dest_url = urllib.parse.urlparse(dest_header)
        dest_path = self._normalize_path(dest_url.path if dest_url.path else dest_header)
        src_path = self._normalize_path(self.path)

        src_item = self._resolve_path(src_path)
        if not src_item or src_item["id"] == "ROOT":
            self.send_error(404, "Source Not Found")
            return

        dest_parent_path = os.path.dirname(dest_path).replace("\\", "/")
        dest_name = os.path.basename(dest_path)
        dest_parent = self._resolve_path(dest_parent_path)

        if not dest_parent or not dest_parent["is_dir"]:
            self.send_error(409, "Destination parent does not exist")
            return

        dest_parent_id = None if dest_parent["id"] == "ROOT" else dest_parent["id"]

        ok, msg = self.client.move_resource(src_item["id"], parent_id=dest_parent_id, new_name=dest_name)
        if ok:
            self._invalidate_cache(src_path)
            self._invalidate_cache(dest_parent_path)
            # Update cache with new location
            moved_item = dict(src_item)
            moved_item["name"] = dest_name
            with self.cache_lock:
                self.cache[dest_path] = {"item": moved_item, "time": time.time()}

            self.send_response(201, "Created")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(500, msg)

    def do_COPY(self):
        """Handles WebDAV COPY request."""
        dest_header = self.headers.get("Destination", "")
        if not dest_header:
            self.send_error(400, "Destination header missing")
            return

        dest_url = urllib.parse.urlparse(dest_header)
        dest_path = self._normalize_path(dest_url.path if dest_url.path else dest_header)
        src_path = self._normalize_path(self.path)

        src_item = self._resolve_path(src_path)
        if not src_item or src_item["id"] == "ROOT":
            self.send_error(404, "Source Not Found")
            return

        dest_parent_path = os.path.dirname(dest_path).replace("\\", "/")
        dest_name = os.path.basename(dest_path)
        dest_parent = self._resolve_path(dest_parent_path)

        if not dest_parent or not dest_parent["is_dir"]:
            self.send_error(409, "Destination parent does not exist")
            return

        dest_parent_id = None if dest_parent["id"] == "ROOT" else dest_parent["id"]
        ok, msg = self.client.copy_resource(src_item["id"], parent_id=dest_parent_id, new_name=dest_name)
        if ok:
            self._invalidate_cache(dest_parent_path)
            self.send_response(201, "Created")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(500, msg)

    def do_DELETE(self):
        """Deletes a file or folder in MYBOX."""
        norm_path = self._normalize_path(self.path)
        item = self._resolve_path(norm_path)

        if not item or item["id"] == "ROOT":
            self.send_error(404, "Item not found")
            return

        ok, msg = self.client.delete_resource(item["id"])
        if ok:
            self._invalidate_cache(norm_path)
            self.send_response(204, "No Content")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(500, msg)

    def do_PUT(self):
        """Uploads a file to MYBOX."""
        norm_path = self._normalize_path(self.path)
        parent_path = os.path.dirname(norm_path).replace("\\", "/")
        file_name = os.path.basename(norm_path)

        parent_item = self._resolve_path(parent_path)
        if not parent_item or not parent_item["is_dir"]:
            self.send_error(409, "Parent directory does not exist")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            chunks = []
            remaining = content_length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            file_data = b"".join(chunks)
        else:
            file_data = b""

        parent_id = None if parent_item["id"] == "ROOT" else parent_item["id"]
        ok, msg = self.client.upload_file(file_name, file_data, parent_id=parent_id, is_overwrite=True)

        if ok:
            self._invalidate_cache(parent_path)
            self.send_response(201, "Created")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(500, msg)


class MyBoxGatewayManager:
    """Manages local WebDAV gateway server lifecycle."""

    # {profile_id or drive_letter: {"server": ThreadingHTTPServer, "port": int, "thread": Thread}}
    _instances: Dict[str, Dict] = {}
    _lock = threading.Lock()

    @classmethod
    def find_free_port(cls, start_port: int = 18080) -> int:
        """Finds an open localhost port."""
        for port in range(start_port, start_port + 50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        # Fallback to random system assigned port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @classmethod
    def start_gateway(cls, profile_id: str, pat_token: str) -> Tuple[bool, str, int]:
        """Starts a local WebDAV gateway server for the specified MYBOX token."""
        with cls._lock:
            if profile_id in cls._instances:
                port = cls._instances[profile_id]["port"]
                return True, "이미 실행 중입니다.", port

            client = MyBoxClient(pat_token)
            # Verify token connectivity
            ok, msg, _ = client.test_connection()
            if not ok:
                return False, f"MYBOX 토큰 인증 실패: {msg}", 0

            port = cls.find_free_port()

            # Create custom handler class bound to this client
            handler_class = type(
                "BoundMyBoxHandler",
                (MyBoxWebDAVHandler,),
                {
                    "client": client,
                    "cache": {},
                    "cache_lock": threading.RLock(),
                },
            )

            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), handler_class)
                server.daemon_threads = True

                t = threading.Thread(target=server.serve_forever, daemon=True)
                t.start()

                cls._instances[profile_id] = {
                    "server": server,
                    "port": port,
                    "thread": t,
                    "client": client,
                }
                return True, f"MYBOX 게이트웨이 시작됨 (포트 {port})", port
            except Exception as e:
                return False, f"로컬 게이트웨이 시작 실패: {str(e)}", 0

    @classmethod
    def stop_gateway(cls, profile_id: str) -> bool:
        """Stops the running gateway server for a profile or drive letter."""
        with cls._lock:
            inst = cls._instances.pop(profile_id, None)
            if not inst and profile_id:
                norm_key = profile_id.upper().rstrip("\\")
                for k in list(cls._instances.keys()):
                    if k.upper().rstrip("\\") == norm_key:
                        inst = cls._instances.pop(k, None)
                        break
            if not inst:
                return False
            server = inst.get("server")
            if server:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:
                    pass
            return True

    @classmethod
    def stop_all(cls):
        """Stops all running gateways."""
        with cls._lock:
            for pid, inst in list(cls._instances.items()):
                server = inst.get("server")
                if server:
                    try:
                        server.shutdown()
                        server.server_close()
                    except Exception:
                        pass
            cls._instances.clear()

    @classmethod
    def get_gateway_port(cls, profile_id: str) -> Optional[int]:
        with cls._lock:
            inst = cls._instances.get(profile_id)
            return inst["port"] if inst else None

    @classmethod
    def is_running(cls, profile_id: str) -> bool:
        if not profile_id:
            return False
        with cls._lock:
            return profile_id in cls._instances

    @classmethod
    def is_gateway_running(cls, profile_id: str) -> bool:
        """Checks if a gateway is running for the given profile or drive."""
        return cls.is_running(profile_id)


import atexit
atexit.register(MyBoxGatewayManager.stop_all)
