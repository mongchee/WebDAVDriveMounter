"""Naver MYBOX Open API Client.

Interfaces with https://open-api.mybox.naver.com/v1 using
Personal Access Token (PAT, 개인 액세스 토큰).
"""

import json
import mimetypes
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


class MyBoxClient:
    """Client for Naver MYBOX Open API."""

    BASE_URL = "https://open-api.mybox.naver.com/v1"

    def __init__(self, token: str):
        self.token = token.strip()

    def _headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "WebDAV-Drive-Mounter/1.0",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def test_connection(self) -> Tuple[bool, str, int]:
        """Tests validity of personal access token and returns item count."""
        url = f"{self.BASE_URL}/drive/resources"
        try:
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            with urllib.request.urlopen(req, timeout=8) as res:
                if res.status in (200, 204):
                    data = json.loads(res.read().decode("utf-8", errors="ignore"))
                    items = self._extract_items(data)
                    return True, f"네이버 MYBOX 연결 성공! (루트 항목: {len(items)}개)", len(items)
                return False, f"예상치 못한 응답 코드: HTTP {res.status}", 0
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "인증 실패 (401 Unauthorized): 개인 액세스 토큰(PAT)이 올바르지 않거나 만료되었습니다.", 0
            err_body = e.read().decode("utf-8", errors="ignore")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("message") or err_json.get("code") or e.reason
                return False, f"MYBOX API 오류 (HTTP {e.code}): {msg}", 0
            except Exception:
                return False, f"MYBOX API 오류 (HTTP {e.code}): {e.reason}", 0
        except urllib.error.URLError as e:
            return False, f"인터넷 또는 서버 연결 불가: {e.reason}", 0
        except Exception as e:
            return False, f"연결 확인 중 오류 발생: {str(e)}", 0

    def _extract_items(self, data: Any) -> List[Dict[str, Any]]:
        """Extracts list of resource objects from various API response shapes."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "resources" in data and isinstance(data["resources"], list):
                return data["resources"]
            if "items" in data and isinstance(data["items"], list):
                return data["items"]
            if "data" in data and isinstance(data["data"], list):
                return data["data"]
            if "folders" in data or "files" in data:
                f_list = data.get("folders", []) or []
                files_list = data.get("files", []) or []
                return list(f_list) + list(files_list)
        return []

    def list_resources(self, folder_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists resources in root or specific folder."""
        if folder_id:
            url = f"{self.BASE_URL}/drive/folders/{urllib.parse.quote(folder_id)}/resources"
        else:
            url = f"{self.BASE_URL}/drive/resources"

        try:
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            with urllib.request.urlopen(req, timeout=12) as res:
                data = json.loads(res.read().decode("utf-8", errors="ignore"))
                items = self._extract_items(data)
                normalized = []
                for item in items:
                    norm = self._normalize_resource(item)
                    if norm:
                        normalized.append(norm)
                return normalized
        except Exception as e:
            print(f"[-] list_resources error for folder {folder_id}: {e}")
            return []

    def _normalize_resource(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalizes resource item dict to standard schema."""
        if not isinstance(raw, dict):
            return None

        res_id = str(raw.get("resourceId") or raw.get("fileId") or raw.get("folderId") or raw.get("id") or "")
        name = str(raw.get("name") or raw.get("resourceName") or raw.get("fileName") or raw.get("folderName") or "")
        if not name:
            return None

        # Determine if directory
        res_type = str(raw.get("resourceType") or raw.get("type") or "").lower()
        is_dir = res_type in ("folder", "directory", "dir") or raw.get("isDir") is True or "folderId" in raw and "fileId" not in raw

        size = int(raw.get("size") or raw.get("fileSize") or 0)

        # Parse or format timestamp
        mtime_raw = raw.get("modifiedAt") or raw.get("updateTime") or raw.get("updatedAt") or raw.get("createTime") or raw.get("createdAt")
        mtime = time.time()
        if isinstance(mtime_raw, (int, float)):
            # If timestamp in milliseconds
            if mtime_raw > 1e11:
                mtime = mtime_raw / 1000.0
            else:
                mtime = float(mtime_raw)
        elif isinstance(mtime_raw, str):
            try:
                import datetime
                dt = datetime.datetime.fromisoformat(mtime_raw.replace("Z", "+00:00"))
                mtime = dt.timestamp()
            except Exception:
                pass

        return {
            "id": res_id,
            "name": name,
            "is_dir": is_dir,
            "size": size,
            "mtime": mtime,
            "raw": raw,
        }

    def get_resource_detail(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Fetches metadata for a single resource."""
        url = f"{self.BASE_URL}/drive/resources/{urllib.parse.quote(resource_id)}"
        try:
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8", errors="ignore"))
                return self._normalize_resource(data)
        except Exception:
            return None

    _download_url_cache: Dict[str, Tuple[str, float]] = {}
    _cache_lock = threading.Lock()
    _CACHE_TTL = 540.0  # 9 minutes (Naver download URLs are valid for ~10 minutes)

    def get_download_url(self, file_id: str) -> Optional[str]:
        """Requests temporary download URL for a file with 9-minute caching.
        
        Caching prevents media players (e.g. PotPlayer) that issue dozens of
        HTTP Range Requests from exhausting Naver MYBOX API daily rate limits (PLAT-429).
        """
        now = time.time()
        with self._cache_lock:
            cached = self._download_url_cache.get(file_id)
            if cached and (now < cached[1]):
                return cached[0]

        url = f"{self.BASE_URL}/drive/files/{urllib.parse.quote(file_id)}/download"
        try:
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8", errors="ignore"))
                durl = data.get("downloadUrl") or data.get("url")
                if durl:
                    with self._cache_lock:
                        self._download_url_cache[file_id] = (durl, now + self._CACHE_TTL)
                return durl
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"[!] MYBOX Rate Limit Exceeded (HTTP 429 Too Many Requests) for file {file_id}")
                with self._cache_lock:
                    cached = self._download_url_cache.get(file_id)
                    if cached:
                        return cached[0]
            else:
                print(f"[-] get_download_url HTTP error {e.code} for {file_id}: {e}")
            return None
        except Exception as e:
            print(f"[-] get_download_url error for {file_id}: {e}")
            return None

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
        """Creates a new folder."""
        url = f"{self.BASE_URL}/drive/folders"
        # Naver MYBOX Open API strictly expects 'folderName', while some variants use 'name'.
        # We pass both to guarantee compatibility across all API revisions.
        payload = {
            "folderName": name,
            "name": name,
        }
        if parent_id and parent_id != "ROOT":
            payload["parentId"] = parent_id

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers=self._headers("application/json"), method="POST")
            with urllib.request.urlopen(req, timeout=12) as res:
                data = json.loads(res.read().decode("utf-8", errors="ignore"))
                new_id = data.get("resourceId") or data.get("folderId") or data.get("id")
                return True, "폴더 생성 성공", new_id
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"폴더 생성 실패 (HTTP {e.code}): {err_msg}", None
        except Exception as e:
            return False, f"폴더 생성 중 예외 발생: {str(e)}", None

    def move_resource(
        self,
        resource_id: str,
        parent_id: Optional[str] = None,
        new_name: Optional[str] = None,
        is_overwrite: bool = False,
    ) -> Tuple[bool, str]:
        """Moves or renames a resource (file or folder)."""
        url = f"{self.BASE_URL}/drive/resources/{urllib.parse.quote(resource_id)}/move"
        payload: Dict[str, Any] = {"isOverwrite": is_overwrite}
        if parent_id and parent_id != "ROOT":
            payload["parentId"] = parent_id
        if new_name:
            payload["name"] = new_name
            payload["folderName"] = new_name
            payload["fileName"] = new_name

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers=self._headers("application/json"), method="POST")
            with urllib.request.urlopen(req, timeout=15) as res:
                if res.status in (200, 201, 204):
                    return True, "이동/이름변경 완료"
                return True, f"이동/이름변경 완료 (HTTP {res.status})"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"이동/이름변경 실패 (HTTP {e.code}): {err_msg}"
        except Exception as e:
            return False, f"이동/이름변경 중 예외 발생: {str(e)}"

    def copy_resource(
        self,
        resource_id: str,
        parent_id: Optional[str] = None,
        new_name: Optional[str] = None,
        is_overwrite: bool = False,
    ) -> Tuple[bool, str]:
        """Copies a resource (file or folder)."""
        url = f"{self.BASE_URL}/drive/resources/{urllib.parse.quote(resource_id)}/copy"
        payload: Dict[str, Any] = {"isOverwrite": is_overwrite}
        if parent_id and parent_id != "ROOT":
            payload["parentId"] = parent_id
        if new_name:
            payload["name"] = new_name
            payload["folderName"] = new_name
            payload["fileName"] = new_name

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers=self._headers("application/json"), method="POST")
            with urllib.request.urlopen(req, timeout=15) as res:
                if res.status in (200, 201, 204):
                    return True, "복사 완료"
                return True, f"복사 완료 (HTTP {res.status})"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"복사 실패 (HTTP {e.code}): {err_msg}"
        except Exception as e:
            return False, f"복사 중 예외 발생: {str(e)}"

    def delete_resource(self, resource_id: str) -> Tuple[bool, str]:
        """Deletes a file or folder (moves to trash)."""
        url = f"{self.BASE_URL}/drive/resources/{urllib.parse.quote(resource_id)}"
        try:
            req = urllib.request.Request(url, headers=self._headers(), method="DELETE")
            with urllib.request.urlopen(req, timeout=10) as res:
                if res.status in (200, 204):
                    return True, "삭제 완료"
                return True, f"삭제 요청 완료 (HTTP {res.status})"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"삭제 실패 (HTTP {e.code}): {err_msg}"
        except Exception as e:
            return False, f"삭제 중 예외 발생: {str(e)}"

    def upload_file(
        self,
        file_name: str,
        file_data: bytes,
        parent_id: Optional[str] = None,
        is_overwrite: bool = True,
    ) -> Tuple[bool, str]:
        """Uploads a file using MYBOX 2-step upload API."""
        step1_url = f"{self.BASE_URL}/drive/files"
        file_size = len(file_data)
        payload = {
            "fileName": file_name,
            "fileSize": file_size,
            "isOverwrite": is_overwrite,
        }
        if parent_id:
            payload["parentId"] = parent_id

        # Step 1: Request uploadUrl
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                step1_url,
                data=body,
                headers=self._headers("application/json"),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as res:
                res_data = json.loads(res.read().decode("utf-8", errors="ignore"))
                upload_url = res_data.get("uploadUrl")
        except Exception as e:
            return False, f"업로드 URL 생성 실패: {str(e)}"

        if not upload_url:
            return False, "응답에서 uploadUrl을 찾을 수 없습니다."

        # Step 2: POST multipart/form-data with Filedata
        try:
            boundary = f"----WebKitFormBoundary{int(time.time() * 1000)}"
            c_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            safe_fname = file_name.replace('"', '\\"')

            headers = {
                "User-Agent": "WebDAV-Drive-Mounter/1.0",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            }

            pre_body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="Filedata"; filename="{safe_fname}"\r\n'
                f"Content-Type: {c_type}\r\n\r\n"
            ).encode("utf-8")
            post_body = f"\r\n--{boundary}--\r\n".encode("utf-8")

            full_body = pre_body + file_data + post_body

            # Dynamic timeout for large files (minimum 60s, +1s per 100KB)
            upload_timeout = max(60, int(file_size / (100 * 1024)) + 30)

            up_req = urllib.request.Request(upload_url, data=full_body, headers=headers, method="POST")
            with urllib.request.urlopen(up_req, timeout=upload_timeout) as up_res:
                if up_res.status in (200, 201, 204):
                    return True, "파일 업로드 완료"
                return True, f"업로드 완료 (HTTP {up_res.status})"
        except Exception as e:
            return False, f"파일 전송 실패: {str(e)}"
