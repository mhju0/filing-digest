# filing-digest

Bilingual(KO/EN) filings digest. DART(OpenDART) + SEC EDGAR를 수집해
citation-grounded RAG로 요약/Q&A를 제공한다. SwiftUI 씬클라이언트 +
FastAPI + PostgreSQL(pgvector) 백엔드.

## 핵심 원칙

**숫자는 구조화 filing API(DART/SEC financials)에서만 온다. LLM은 서술만
담당한다. 모든 주장에는 citation이 있어야 한다. 수치 환각은 절대 금지.**

## 프로젝트 상태 / 디자인

- v0.3.0으로 기능 완료된 포트폴리오 프로젝트이며 유지보수 모드다. 새 기능은
  명시적으로 요청된 경우에만 추가한다.
- iOS 디자인 정본은 **`docs/design/DESIGN.md`**와 `Theme.swift`의 "Ledger"
  에디토리얼 시스템이다. UI 변경은 기존 토큰과 컴포넌트를 따른다.

## 환경

- Python 3.11 (`requires-python == 3.11.*`). venv는 repo 루트 `.venv` —
  activate 후 `cd backend`.
- macOS. 테스트는 pytest (`backend/tests/`, `pytest.ini_options` →
  `testpaths = ["tests"]`).

## 스택 규칙

- `httpx`(async) 사용, `requests` 금지.
- `logging` 사용, `print()` 금지.
- XML 파싱은 `defusedxml`만 사용(XXE/billion-laughs 방지).
- **Alembic 절대 금지.** 새 DB 스키마의 단일 소스는 `backend/db/init.sql`이며
  `backend/app/db/models.py`와 항상 100% 정합을 유지한다. 기존 DB의 변경은
  백업(`pg_dump`) 후 `backend/db/migrations/`의 버전 SQL을 적용한다.
  `DROP DATABASE filing_digest`는 로컬 corpus를 삭제하므로 데이터 폐기가
  명시적 목적이고 백업이 확인된 경우 외에는 절대 실행하지 않는다.

## DB

- Postgres 식별자: DB/유저 `filing_digest`, 로컬 dev 비밀번호 `filing_digest_dev`
  (`backend/.env.example`의 `DATABASE_URL`에도 그대로 있는 로컬 기본값).
  드라이버는 psycopg3 (`postgresql+psycopg://` DSN).
- 임베딩: `vector(1024)` — KURE-v1(nlpai-lab/KURE-v1). 저장 시
  `normalize_embeddings=True` 고정, 거리 함수는 cosine(`<=>`) —
  벡터 인덱스를 만들 때 반드시 `vector_cosine_ops` operator class를 쓸 것.
- **로컬 DB는 Docker가 아니라 Homebrew `postgresql@16`(16.14, 포트 5432)**
  이다. 2026-07-25에 docker 볼륨 `filing-digest_pgdata`에서 이관했다
  (카운트 8/13/1191/86 일치 확인). 같은 클러스터에 mammacare의
  `mammacare_db`가 함께 있지만 DB와 역할이 분리되어 있어 서로 간섭하지
  않는다. `brew services`로 이미 상시 기동 중이라 **Docker Desktop을 켤
  필요가 없다.**
  - `backend/.env`의 `DATABASE_URL`은 `localhost:5432`.
  - 접속: `PGPASSWORD=filing_digest_dev
    /opt/homebrew/opt/postgresql@16/bin/psql -h localhost -p 5432
    -U filing_digest -d filing_digest`
  - pgvector 0.8.4는 **소스 빌드**로 설치했다. `brew install pgvector`는
    postgresql@17/@18용으로만 빌드되므로 @16에는 쓸 수 없다. 재설치가
    필요하면:
    `make install PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config`
    (macOS에서는 `vector.so`가 아니라 `vector.dylib`로 설치되는 게 정상).
  - `docker-compose.yml`과 `backend/Dockerfile`은 저장소에 그대로 둔다 —
    클론하는 사람에게는 재현 가능한 경로이고, README도 Docker 기준을
    유지한다. 이 파일의 내용은 **이 머신의 로컬 선택**이다.
  - 다른 프로젝트(mammacare, 영수증)의 MySQL 데이터가 docker 볼륨에
    남아 있으므로 **Docker Desktop 자체를 삭제하면 안 된다.**
- `embedding_offline_first`(기본 true): 모델이 로컬 캐시에 있으면 HF Hub
  네트워크 체크를 건너뛴다(startup ~9s→~5s, HF 요청 33→0 실측). 캐시가
  없는 클린 환경에서는 자동으로 온라인 경로를 탄다.
- `embedding_warmup_enabled`(기본 true): startup 시 KURE-v1을 미리
  로드한다. 테스트/CI에서 false로 두면 모델 로드 없이 앱이 기동된다
  (startup 22ms 실측).

## 실행 / 포트

- **API 포트 소유권**: host `8000`은 옆 프로젝트 mammacare 전용(native
  uvicorn + Vite proxy target 고정, 이동 불가), host `8001`은
  filing-digest 전용으로 영구 고정한다. 로컬 uvicorn은 `--port 8001`을
  반드시 명시하고(`--port` 생략 시 기본 8000으로 떠서 mammacare와 충돌 →
  `Address already in use`), `docker-compose.yml`은 `"8001:8000"`
  (host만 8001, 컨테이너 내부는 8000 유지). `Dockerfile`의 EXPOSE/CMD
  8000은 컨테이너 내부 포트라 건드리지 않는다. 포트 점유 확인은
  `lsof -nP -iTCP:8001 -sTCP:LISTEN`.
- **개발 정본 = host uvicorn 하나뿐. Docker는 쓰지 않는다.** DB는 이미
  상시 기동 중인 Homebrew postgresql@16이므로 띄울 게 없다:
  `cd backend && ../.venv/bin/python -m uvicorn app.main:app --reload --port 8001`
- `docker compose up`을 실행하면 baked-in backend 컨테이너가 host
  uvicorn과 포트를 동시에 잡아 stale/warmup 실패 응답을 낸다(컨테이너엔
  KURE-v1 캐시가 없어 `/health`가 빈 응답). `docker compose config -q`는
  데몬 없이도 동작하므로 검증 목록은 그대로 쓸 수 있다.
- 실기기(iPhone) 테스트 시에만 `--host 0.0.0.0`을 붙인다. 그 경우 같은
  Wi-Fi의 다른 기기도 인증 없이 API에 접근할 수 있고, macOS 방화벽이
  venv Python의 수신 연결을 기본 차단하므로 별도 허용이 필요하다.

## 시크릿

- DART API 키는 `backend/.env`(gitignored) 또는 환경변수로만 공급한다.
  코드·로그에 시크릿을 남기지 않는다.
- httpx 요청 로그의 `crtfc_key` 마스킹 필터가
  `backend/app/logging_config.py`에 있다 — 우회하지 말 것.

## git 규칙

- git diff / status / log 등 read-only git 명령은 자기 변경 검증에 사용 가능.
- `.serena/`, `.claude/`, `CLAUDE.md`는 로컬 도구 설정이며 gitignored다.

## 검증 규칙

- (global보다 엄격) global은 read-only 조사에만 `file:line` 근거를 요구하지만,
  이 저장소에서는 코드 동작에 관한 **모든** 결론에 `file:line` 근거를 단다 —
  구현·수정 작업의 결론도 예외 없다.
- 테스트 PASSED만으로 실연동 스텝을 완료로 치지 않는다 — 라이브
  실측값을 사람이 확인한 뒤에 커밋한다.

## 순수 함수 분리

파싱/매핑 로직은 네트워크·DB 없이 단위테스트 가능한 순수 함수로 만든다
(기존 패턴: `persist.py`, `chunking.py`, `kure.py`).

## 작업 후 보고 양식

- 변경 파일
- 영향 범위
- 확인한 것
- 주의 사항

## 언어

설명은 한국어로, 코드·파일명·커밋 메시지는 영어로 작성한다.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `mhju0/filing-digest`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five canonical triage labels defined for this repository. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository using root `CONTEXT.md` and `docs/adr/`. See `docs/agents/domain.md`.
