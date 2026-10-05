# WebDAV Drive Mounter (RaiDrive 스타일 가상 드라이브 마운터)

RaiDrive처럼 WebDAV 원격 스토리지를 Windows 파일 탐색기에서 실제 가상 드라이브(예: `Z:`, `Y:`, `X:` 등)로 마운트하여 사용할 수 있는 파이썬 프로그램입니다.

외부 드라이버나 복잡한 C++ 설치 없이 Windows 자체 내장 WebDAV 클라이언트(`WebClient` 서비스 / `net use`)를 제어하며, Windows WebDAV의 50MB 용량 제한을 4GB로 자동 해제하는 최적화 도구를 내장하고 있습니다.

---

## 🌟 주요 기능

1. **RaiDrive 스타일의 깔끔한 백색(화이트 라이트) 모던 UI**
   - 여러 WebDAV 스토리지 연결 정보(서버 주소, 포트, SSL 여부, 계정 등)를 프로필로 저장/관리
   - 가독성이 뛰어난 맑은 고딕 일반체(`normal`) 얇은 폰트 적용
   - 사용 가능한 드라이브 문자(Z: ~ D:) 자동 감지 및 실시간 상태 표시
   - 원클릭 [연결(마운트)] / [연결 해제] / [탐색기 열기]

2. **컴퓨터 부팅 시 자동 실행 (Windows 시작 프로그램 등록)**
   - 상단 툴바의 **[부팅 시 자동실행]** 체크박스로 간편하게 활성화/비활성화
   - 부팅 시 백그라운드/트레이로 조용히 시작되며 등록된 스토리지를 자동으로 마운트

3. **시스템 트레이 상주 (X 닫기 시 백그라운드 유지)**
   - 창 오른쪽 상단의 [X] 버튼을 눌러도 종료되지 않고 작업 표시줄 오른쪽 시스템 트레이로 최소화
   - 마운트된 WebDAV 드라이브 연결이 끊김 없이 백그라운드에서 계속 유지
   - 트레이 아이콘을 더블클릭하여 창 복원, 우클릭 메뉴에서 [📁 탐색기 열기] 및 [❌ 완전 종료] 가능

4. **Windows WebDAV 대용량 4GB 제한 기본 자동 적용**
   - 별도 설정 없이 프로그램 실행 시 자동으로 Windows 기본 50MB 전송 제한을 4GB로 자동 해제
   - WebClient 서비스 및 HTTP BasicAuth 인증 자동 점검

5. **구글 드라이브 (Google Drive) 원클릭 연동 및 초고속 스트리밍 마운트**
   - 별도의 복잡한 GCP API 설정 없이 **[구글 계정 간편 로그인]** 버튼 하나로 웹 브라우저에서 안전하게 인증
   - 인증 토큰 자동 입력 및 WinFsp 가상 파일시스템 기반 초고속 청크 스트리밍 마운트
   - 4K 대용량 동영상도 다운로드 대기 없이 즉시 탐색기에서 재생 및 업로드/다운로드 지원

6. **네이버 MYBOX (Open API) 가상 드라이브 마운트 완벽 지원**
   - 네이버 공식 개인 액세스 토큰(PAT)을 활용하여 내장 로컬 WebDAV 게이트웨이 구동
   - 브라우저 접속 없이 Windows 파일 탐색기에서 실제 가상 드라이브(예: `N:`, `Z:` 등)로 원클릭 마운트
   - 대용량 영상 스트리밍 및 파일 다운로드/업로드/폴더 생성/삭제 완벽 지원

7. **CLI(명령줄) 및 GUI 동시 지원**
   - 그래픽 창(GUI)뿐 아니라 배치 파일이나 스크립트에서 자동 마운트할 수 있도록 CLI 명령어도 제공

---

## 🚀 실행 방법

### 1. 초고속 EXE 실행 (권장 - 0.2초 이내 즉각 실행)
빌드된 실행 파일이 `dist` 폴더에 준비되어 있습니다:

- **초고속 폴더형 EXE (가장 빠름)**:
  - 경로: `dist/WebDAVDriveMounter/WebDAVDriveMounter.exe`
  - 더블클릭 즉시(0.2초 내) 창이 뜨며, 백신 검사나 압축 해제 딜레이가 전혀 없습니다.
  - `dist/바탕화면에_바로가기_만들기.bat`을 실행하면 바탕화면에 즉시 바로가기가 생성됩니다.
- **최적화 단일 파일 EXE (휴대용)**:
  - 경로: `dist/WebDAVDriveMounter_Single.exe`
  - 파일 하나로 이동할 수 있는 단일 실행 파일입니다.
- **원클릭 실행 스크립트**:
  - 루트 경로의 `run.bat`을 더블클릭하면 빌드된 최적화 EXE를 자동으로 감지하여 초고속으로 실행합니다.

---

### 2. Python 스크립트로 직접 실행

Python 3이 설치되어 있다면 다음 명령으로도 실행할 수 있습니다:

```bash
python main.py
```
또는
```bash
python main.py gui
```

### 3. EXE 재생성(빌드) 방법
언제든지 다음 명령어로 초고속 빌드를 다시 수행할 수 있습니다:
```bash
python build.py
```
*(onedir만 빌드: `python build.py onedir`, onefile만 빌드: `python build.py onefile`)*

---

### 4. CLI 명령줄 모드

- **사용 가능한 드라이브 및 현재 마운트 목록 확인**:
  ```bash
  python main.py list
  ```

- **WebDAV 드라이브 마운트**:
  ```bash
  python main.py mount Z: https://webdav.example.com/remote.php/webdav -u 계정명 -p 비밀번호
  ```

- **네이버 MYBOX 드라이브 마운트 (개인 액세스 토큰 활용)**:
  ```bash
  python main.py mount-mybox N: --token "YOUR_MYBOX_PERSONAL_ACCESS_TOKEN" --streaming
  ```

- **구글 드라이브 브라우저 인증 (OAuth 토큰 발급)**:
  ```bash
  python main.py authorize-gdrive
  ```

- **구글 드라이브 마운트**:
  ```bash
  python main.py mount-gdrive G: --token '{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'
  ```

- **드라이브 연결 해제**:
  ```bash
  python main.py unmount Z:
  ```

- **WebDAV 환경 최적화 (4GB 제한 해제 및 WebClient 시작)**:
  ```bash
  python main.py optimize
  ```
  *(레지스트리 수정이 포함되므로 관리자 권한 프롬프트에서 실행)*

---

## 💡 지원 스토리지 서비스별 설정 안내

| 서비스 | 주소(호스트) | 포트 | SSL(HTTPS) | 원격 경로 / 인증 방식 |
| :--- | :--- | :---: | :---: | :--- |
| **구글 드라이브 (Google Drive)** | *(원클릭 OAuth 자동 연동)* | - | ✔ | **[구글 계정 간편 로그인]** 원클릭 인증 |
| **네이버 MYBOX** | *(내장 게이트웨이 자동 연결)* | 자동 | - | **개인 액세스 토큰 (PAT)** 입력 |
| **Nextcloud / ownCloud** | `cloud.example.com` | `443` | ✔ | `/remote.php/dav/files/계정명/` |
| **Synology NAS** | `nas.example.com` | `5006` | ✔ | `/` 또는 `/공유폴더명` |
| **QNAP NAS** | `nas.example.com` | `5001` | ✔ | `/` 또는 `/공유폴더명` |
| **Alist** | `alist.example.com` | `5244` | 비활성(HTTP) 또는 활성 | `/dav` |
| **일반 Apache/Nginx WebDAV** | `example.com` | `443` | ✔ | `/webdav/` |

---

## ⚠️ 문제 해결 및 팁

1. **파일 복사 시 "파일 크기가 제한되어 있습니다" 오류**:
   - 상단 메뉴의 **[Windows WebDAV 최적화]** 버튼을 누르고 **[원클릭 최적화 적용]**을 실행하세요. (프로그램을 '관리자 권한'으로 실행 시 적용 가능)
2. **HTTP(비보안) WebDAV 서버 연결 오류**:
   - Windows는 기본적으로 HTTP 주소에서의 계정 로그인을 보안상 차단합니다. 위 최적화 도구를 적용하면 HTTP 환경에서도 정상 로그인할 수 있습니다.
