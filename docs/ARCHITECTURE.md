# filing-digest 아키텍처 (Architecture)

한국(DART)·미국(SEC EDGAR) 공시를 수집해 **인용 근거가 붙은(citation-grounded)** 이중 언어(KO/EN)
요약과 Q&A를 제공하는 서비스의 v0.1 아키텍처 문서.

---

## 1. 시스템 개요 (System Overview)

```
                 ┌─────────────────────────────┐
                 │        iOS App (SwiftUI)     │
                 │  iOS 17+, 서드파티 의존성 없음 │
                 └──────────────┬──────────────┘
                                │ HTTP (JSON, snake_case)
                                │ baseURL: http://127.0.0.1:8001
                                ▼
┌───────────────────────────────────────────────────────────────┐
│                  Backend (FastAPI, Python 3.11)                │
│                                                               │
│  GET  /health                     GET /companies?q=           │
│  GET  /companies/{id}/digest      POST /search  POST /answer  │
│                                    POST /ingest (stub)         │
│                                                               │
│  ┌─────────────┐   ┌──────────────┐   ┌───────────────────┐  │
│  │ PostgreSQL   │   │ Ingest (stub) │   │ Search / Answer    │  │
│  │ (real reads) │   │ job queue     │   │ (KURE-v1 + Solar)  │  │
│  └─────────────┘   └──────────────┘   └───────────────────┘  │
└───────┬──────────────────────┬────────────────────────────────┘
        │ SQLAlchemy 2.x       │  Phase 2: httpx
        │ (psycopg3)           ▼
        ▼               ┌─────────────────┐  ┌─────────────────┐
┌────────────────┐      │  DART Open API   │  │  SEC EDGAR API   │
│ PostgreSQL 16  │      │ (opendart.fss.   │  │ (data.sec.gov)   │
│ + pgvector     │      │  or.kr/api)      │  │  UA에 연락처 필수  │
│ (host 5433 ->  │      └─────────────────┘  └─────────────────┘
│  container     │
│  5432)         │
└────────────────┘
```

- **현재**: DART 실호출 연동됨(`backend/app/clients/dart.py`), `/companies`·
  `/companies/{id}/digest`·`/search`·`/answer`는 실제 DB(`companies`,
  `filings`, `filing_chunks`, `financials`)를 읽는다. SEC는 아직 스텁
  (`backend/app/clients/sec.py`, 메서드 전부 `NotImplementedError`)이다.
- **Phase 2**: SEC 실연동, 파싱/청킹 자동화, 벡터 인덱스 튜닝.

## 2. 모노레포 구조 (Monorepo Layout)

```
filing-digest/
├── backend/                 # FastAPI 백엔드 (Python 3.11)
│   ├── app/                 #   라우터, 스키마(pydantic), 설정, LLM 가드
│   ├── db/init.sql          #   DB 스키마 v0.1 (마이그레이션 도구 대신 단일 init 스크립트)
│   ├── tests/               #   pytest
│   ├── Dockerfile
│   └── requirements.txt
├── ios/                     # SwiftUI 클라이언트 (FilingDigest.xcodeproj)
├── docs/                    # 아키텍처·결정사항 문서 (본 문서)
├── docker-compose.yml       # 로컬 개발 스택 (db + backend)
└── README.md
```

## 3. 핵심 원칙 (Core Principles)

> **"숫자는 구조화 API에서만, LLM은 서술만, 모든 주장에 인용(citation)"**

1. **수치(`MetricCard.value`)는 DART/SEC 구조화 데이터에서만 온다.**
   LLM이 숫자를 생성·추정·보정하는 일은 없다. `financials` 테이블의 값도
   모두 `citation_id`로 실제 `Citation`(원본 공시)에 연결되어 이 파이프라인
   규약을 강제한다.
2. **LLM은 요약/서술(narrative)만 담당한다.**
   digest의 `summary_ko`/`summary_en`, `/answer`의 `answer`가 LLM의 영역이며,
   여기에도 숫자를 "지어내는" 것은 금지된다(`number_guard`/bare-digit floor로 강제).
   서술에 등장하는 수치는 구조화 데이터를 그대로 인용한다.
3. **모든 서술형 주장은 `citations[]`로 근거에 연결된다.**
   근거 없는 문장은 응답에 포함하지 않는다. (`Citation.url`이 원문 공시로의 링크)

## 4. 결정사항 기록 (Decision Log)

| # | 결정 | 태그 | 근거 |
|---|------|------|------|
| D1 | **Alembic 대신 `backend/db/init.sql` 단일 스크립트 채택** | [Verified] | v0.1은 테이블 4개(companies, filings, filing_chunks, financials)뿐이고 운영 데이터가 없어 마이그레이션 이력 관리가 불필요. `docker-entrypoint-initdb.d`에 read-only 마운트하면 compose up만으로 재현 가능한 스키마가 보장된다. 스키마가 진화하기 시작하는 Phase 2에서 Alembic 도입을 재검토한다. |
| D2 | **pgvector embedding 차원 1024** | [Verified] | 임베딩 모델을 KURE-v1(nlpai-lab/KURE-v1)로 확정. dense 차원은 HuggingFace `config.json`의 `hidden_size=1024`(bge-m3 기반 XLM-RoBERTa) 및 `1_Pooling/config.json`의 `word_embedding_dimension=1024`로 교차 확인. `EMBEDDING_DIM` 환경변수(default 1024)와 `filing_chunks.embedding vector(1024)`에 반영. |
| D3 | **DART/SEC 실제 응답 포맷** | DART [Verified] / SEC [Unknown] | DART는 실호출로 확인 완료(`docs/dart-api-notes.md`). SEC는 `SecClient`가 아직 스텁(`NotImplementedError`)이라 미확인. `SEC_BASE_URL`/`SEC_USER_AGENT`만 설정으로 예약해 둔다. (SEC는 연락처 포함 User-Agent를 요구 - placeholder만 커밋) |
| D4 | **psycopg3 선택** (`postgresql+psycopg://` 드라이버) | [Verified] | psycopg2는 유지보수 모드, psycopg3(패키지명 `psycopg`)가 현행 권장 드라이버이며 SQLAlchemy 2.x가 `postgresql+psycopg` dialect로 공식 지원. `DATABASE_URL` 기본값과 docker-compose의 접속 문자열에 반영됨. |
| D5 | **iOS 17 타깃 + 서드파티 의존성 없음** | [Verified] | SwiftUI + URLSession + Codable(async/await)만으로 v0.1 API 소비가 충분하다. 의존성 0개는 빌드 재현성과 리뷰 범위를 최소화한다. 기본 baseURL은 `http://127.0.0.1:8001` (시뮬레이터 로컬 개발). |
| D6 | **초기 데이터 범위: 삼성전자 단일 기업/공시** | [Verified] | 삼성전자(dart, KOSPI, 005930) 2023년 사업보고서 하나만 적재된 상태로 시작. DART 실연동 경로를 최소 셋으로 검증한다. `/digest`·`/answer`는 이제 실제 DB(`financials`, `filing_chunks`)를 읽고, 모든 `MetricCard.value`는 `citation_id`로 실제 `Citation`(원본 공시)과 연결된다(핵심 원칙 강제). SEC/Apple 등 두 번째 소스는 Phase 2. |
| D7 | **API CONTRACT v0.1 고정** (아래 5절 전문) | [Verified] | backend·iOS가 병렬 개발되므로 계약을 먼저 동결. JSON 필드는 snake_case. |
| D8 | **벡터 인덱스(hnsw/ivfflat) 미생성** | [Verified] | 실데이터가 없어 인덱스 파라미터 튜닝이 불가능. init.sql에 Phase 2 TODO 주석으로만 남긴다. |
| D9 | **`filing_chunks`의 메타데이터 컬럼명은 `meta`** | [Verified] | `metadata`는 SQLAlchemy Declarative의 예약 속성명(`Base.metadata`)이라 충돌한다. 컬럼명 자체를 `meta`(jsonb)로 통일. |

## 5. API CONTRACT v0.1 (전문)

모든 컴포넌트(backend, iOS)가 그대로 따라야 하는 계약. JSON 필드는 snake_case.

```
GET /health -> 200
  {"status": "ok", "version": "0.1.0"}

GET /companies?q=<string> -> 200 CompanySearchResponse
  CompanySearchResponse = {"items": [Company], "total": int}
  Company = {"id": str(uuid), "name": str, "name_en": str|null, "ticker": str|null,
             "market": "KOSPI"|"KOSDAQ"|"NYSE"|"NASDAQ"|null, "source": "dart"|"sec"}

GET /companies/{company_id}/digest?lang=ko|en (lang 생략 시 ko) -> 200 CompanyDigest, 미존재 id -> 404
  CompanyDigest = {"company_id": str, "company_name": str, "period": str (예 "2026Q1"),
                   "metrics": [MetricCard], "summary_ko": str, "summary_en": str,
                   "citations": [Citation], "generated_at": str(ISO8601)}
  MetricCard = {"key": "revenue"|"operating_income"|"net_income"|"eps"|"operating_margin",
                "label_ko": str, "label_en": str, "value": float|null, "unit": str,
                "yoy_delta_pct": float|null, "source": "dart"|"sec", "citation_id": str|null}
  Citation = {"id": str, "source": "dart"|"sec", "title": str, "url": str,
              "excerpt": str|null, "filed_at": str(ISO date)|null}

POST /search -> 200 SearchResponse
  request  SearchRequest  = {"query": str(1자 이상), "top_k": int(default 5, max 50), "company_id": str(uuid)|null}
  SearchResponse = {"items": [SearchHit], "total": int}
  SearchHit = {"chunk_id": str, "filing_id": str, "text": str, "score": float,
               "rcept_no": str|null, "section_title": str|null, "section_order": int|null,
               "part_index": int|null, "chunk_index": int}

POST /answer -> 200 AnswerResponse
  request  AnswerRequest  = {"query": str(1자 이상), "company_id": str(uuid), "period": str|null}
  AnswerResponse = {"answer": Answer|null, "figures": [Figure], "citations": [Citation],
                     "company_id": str, "narrative_status": "ok"|"blocked"|"no_results"}

POST /ingest -> 202 (stub -- no worker yet)
  request  IngestRequest  = {"company_id": str, "source": "dart"|"sec", "filing_types": [str]|null}
  response IngestResponse = {"job_id": str(uuid), "status": "queued"}
```

- 포트: backend **8001**, postgres **5433(host) -> 5432(container)**. iOS 기본 baseURL: `http://127.0.0.1:8001`
- ENV 변수 (pydantic-settings, `.env`): `DART_API_KEY`(secret, placeholder만 커밋),
  `DART_BASE_URL`(default `https://opendart.fss.or.kr/api`), `SEC_BASE_URL`(default `https://data.sec.gov`),
  `SEC_USER_AGENT`(SEC는 연락처 포함 UA 요구 - placeholder),
  `DATABASE_URL`(default `postgresql+psycopg://filing_digest:filing_digest_dev@localhost:5433/filing_digest`),
  `EMBEDDING_DIM`(default 1024)

## 6. DB 스키마 v0.1 요약

`backend/db/init.sql`과 SQLAlchemy 모델이 정확히 일치해야 한다. (`CREATE EXTENSION IF NOT EXISTS vector;`,
pg16 내장 `gen_random_uuid()` 사용)

- **companies**: id(uuid PK), name, name_en, ticker, market, source(`dart|sec` CHECK),
  dart_corp_code UNIQUE, sec_cik UNIQUE, created_at
- **filings**: id(uuid PK), company_id FK→companies ON DELETE CASCADE, source, filing_type,
  title, period, filed_at(date), url, created_at; `idx_filings_company` 인덱스
- **filing_chunks**: id(uuid PK), filing_id FK→filings ON DELETE CASCADE, chunk_index,
  content, embedding `vector(1024)` [Verified - D2], meta(jsonb, `metadata`는 예약어라 회피 - D9),
  created_at; UNIQUE(filing_id, chunk_index)
- **financials**: id(uuid PK), company_id FK→companies ON DELETE CASCADE,
  filing_id FK→filings ON DELETE SET NULL, fiscal_year, fiscal_quarter, period, metric,
  value numeric(24,4), unit, currency, source, created_at; UNIQUE(company_id, period, metric, source)
- 벡터 인덱스(hnsw/ivfflat)는 생성하지 않음 - Phase 2 TODO (D8)

## 7. Phase 2 TODO

- [ ] **DART/SEC 실연동**: httpx 기반 클라이언트, 응답 포맷 실측·확정(D3 해소), 레이트리밋·재시도 처리
- [ ] **파싱/청킹(parsing/chunking)**: 공시 원문(XBRL/HTML) 파싱 → `filing_chunks` 적재 파이프라인
- [ ] **임베딩 + RAG**: 임베딩 모델 확정(D2 해소, `EMBEDDING_DIM` 재조정 가능) → pgvector 유사도 검색 기반 근거 검색
- [ ] **LLM 요약 파이프라인**: 구조화 수치 + 검색된 청크만을 컨텍스트로 서술 생성, 인용 강제(핵심 원칙 유지)
- [ ] **벡터 인덱스 튜닝**: 실데이터 규모 확인 후 hnsw/ivfflat 인덱스 생성·파라미터 튜닝 (D8)
- [ ] (부수) Alembic 도입 재검토(D1), ingest 잡의 실제 비동기 처리(큐/워커)
