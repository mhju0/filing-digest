# DART OpenAPI 응답 포맷 & 파서 설계 노트

DART OpenAPI(전자공시) 실호출로 확인한 응답 포맷과, 이를 우리 DB 스키마
(`companies` / `filings` / `filing_chunks` / `financials`)에 매핑하는 **파서 설계 초안**.
ARCHITECTURE.md 결정사항 **D3([Unknown] → 실측)**을 해소한다.

> **이 문서의 범위**: 포맷 확인 + 파서 설계까지. ingest/청킹/임베딩/DB 적재 **구현은
> Phase 2-3**이며 여기서 하지 않는다.

## 검증 태그 범례

- **[Verified]** — 실호출 응답으로 직접 확인함 (아래 "확인 방법" 참조).
- **[Inferred]** — DART 공식 문서/스키마 기반 추정. 실호출로는 미확인.
- **[Unknown]** — 아직 확인하지 못함. Phase 2 진입 전/중 수동 확인 필요.

### 확인 방법 (실호출 요약)

- **일시**: 2026-07-02, `httpx.Client`(동기)로 각 엔드포인트 1~2회씩 호출.
- **대상 기업**: 삼성전자 (stock_code `005930`, corp_code `00126380` — corpCode.xml에서 확인).
- **키 취급**: `DART_API_KEY` 환경변수에서만 읽고, 로그/URL/샘플에는 `crtfc_key=***`로
  마스킹. 임시 호출 스크립트는 repo 밖(scratch)에만 두고 트래킹하지 않음.
- **응답 본문에는 키가 포함되지 않음**(키는 요청 쿼리에만 존재) → 샘플 값은 그대로 기록.

공통 사항:

- **base URL**: `https://opendart.fss.or.kr/api` (`Settings.dart_base_url` 기본값과 일치). **[Verified]**
- **인증**: 모든 요청에 쿼리 파라미터 `crtfc_key` 필수. **[Verified]**
- **레이트리밋**: 분당 호출 제한 있음(초과 시 status `020`). 실호출은 엔드포인트당 1~2회로 제한함. **[Inferred]**

---

## 1) `corpCode.xml` — 기업 고유번호 매핑

기업명/종목코드 ↔ DART `corp_code`(8자리) 매핑 테이블. **다른 모든 호출의 선행 의존성**
(list.json / 재무제표는 `corp_code`를 요구).

### 요청

```
GET https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=***
```

| 파라미터 | 필수 | 타입 | 의미 |
|---|---|---|---|
| `crtfc_key` | 필수 | string | 인증키 (env에서만) |

- 파라미터는 인증키뿐. **[Verified]**

### 응답

- **Content-Type**: `application/x-msdownload;charset=UTF-8` — 확장자는 `.xml`이지만 **실제로는 ZIP 바이너리**. **[Verified]**
- ZIP 내부: 멤버 1개 `CORPCODE.xml` (UTF-8). **[Verified]**
- 구조: `<result><list>...</list>...</result>`, `<list>` 원소 = 기업 1건. **[Verified]**
- 실측 규모: `<list>` **118,396건**, 그중 `stock_code`가 비어있지 않은(상장) 건 **3,975건**. **[Verified]**

| 필드(태그) | 타입 | 의미 | 샘플값 |
|---|---|---|---|
| `corp_code` | string(8) | DART 고유번호 (join 키) | `00126380` |
| `corp_name` | string | 정식 국문명 | `삼성전자` |
| `corp_eng_name` | string | 영문명 | `SAMSUNG ELECTRONICS CO,.LTD` |
| `stock_code` | string(6) 또는 `' '` | 종목코드. **비상장은 공백 한 칸(`' '`)** | `005930` |
| `modify_date` | string(YYYYMMDD) | 최종 수정일 | `20251201` |

**파서 주의점**:
- `stock_code`는 비상장일 때 빈 문자열이 아니라 **공백 `' '`** → `.strip()` 후 빈값이면 `NULL`. **[Verified]**
- `corp_eng_name`에 회사가 넣은 표기 그대로 오므로 콤마/점 등 오탈자 가능 (`CO,.LTD`). 정규화하지 말고 원문 보존. **[Verified]**
- 3.5MB ZIP 전체를 받아 파싱해야 함 → **주기적 스냅샷 후 로컬 매핑**으로 쓰고, 호출마다 받지 않는다. **[Inferred]**
- XXE/billion-laughs 방지를 위해 `defusedxml`로 파싱(스크립트에서 적용). **[Verified]**
- 시장 구분(KOSPI/KOSDAQ)은 **여기서 알 수 없음** — `list.json`의 `corp_cls`가 필요(§2). **[Verified]**

---

## 2) `list.json` — 공시 목록

`corp_code` + 기간으로 공시(filing) 목록 조회. → `filings` 테이블의 원천.

### 요청

```
GET .../list.json?crtfc_key=***&corp_code=00126380&bgn_de=20240101&end_de=20240630&page_no=1&page_count=10
```

| 파라미터 | 필수 | 타입 | 의미 |
|---|---|---|---|
| `crtfc_key` | 필수 | string | 인증키 |
| `corp_code` | 선택 | string(8) | 특정 기업만. 생략 시 전체 공시 |
| `bgn_de` / `end_de` | 선택 | YYYYMMDD | 접수일자 시작/종료 | 
| `pblntf_ty` | 선택 | string | 공시유형(대분류). `A`=정기공시 등 |
| `pblntf_detail_ty` | 선택 | string | 공시유형(상세) |
| `corp_cls` | 선택 | Y/K/N/E | 법인구분 필터 |
| `page_no` / `page_count` | 선택 | int | 페이지 번호 / 페이지당 건수(최대 100) |

- `corp_code` + `bgn_de`/`end_de` 조합으로 정상 응답 **[Verified]**.
- `pblntf_ty=A`(정기공시)로 삼성전자 사업보고서 1건만 필터되는 것 확인 **[Verified]**.
- 나머지 파라미터/상세유형 코드는 문서 기반 **[Inferred]**.

### 응답 (`status=000`)

최상위:

| 필드 | 타입 | 의미 | 샘플 |
|---|---|---|---|
| `status` | string | 결과 코드(§5) | `000` |
| `message` | string | 결과 메시지 | `정상` |
| `page_no` | **int** | 현재 페이지 | `1` |
| `page_count` | **int** | 페이지당 건수 | `10` |
| `total_count` | **int** | 전체 건수 | `114` |
| `total_page` | **int** | 전체 페이지 수 | `12` |
| `list` | array | 공시 원소 배열 | — |

> 페이징 필드는 **JSON number(int)** 로 옴(문자열 아님). **[Verified]**

`list[]` 원소:

| 필드 | 타입 | 의미 | 샘플 |
|---|---|---|---|
| `corp_code` | string(8) | 고유번호 | `00126380` |
| `corp_name` | string | 회사명 | `삼성전자` |
| `stock_code` | string(6) | 종목코드 | `005930` |
| `corp_cls` | string | 법인구분 Y/K/N/E | `Y` |
| `report_nm` | string | 보고서명. **뒤에 공백 패딩 있음** | `'지속가능경영보고서등관련사항(자율공시)              '` |
| `rcept_no` | string(14) | **접수번호(문서 join 키)** | `20240628800773` |
| `flr_nm` | string | 공시 제출인 | `삼성전자` |
| `rcept_dt` | string(YYYYMMDD) | 접수일자 | `20240628` |
| `rm` | string | 비고(플래그, §아래) | `유` / `''` / `정` |

**법인구분 `corp_cls`** (→ `companies.market`):

| 값 | 의미 | market 매핑 |
|---|---|---|
| `Y` | 유가증권시장 | `KOSPI` **[Verified: 삼성전자=Y]** |
| `K` | 코스닥 | `KOSDAQ` **[Inferred]** |
| `N` | 코넥스 | (KONEX; 우리 enum엔 없음) **[Inferred]** |
| `E` | 기타 | `null` **[Inferred]** |

**비고 `rm`** 약어(관찰: `유`, `''`, `정`): `유`=유가증권신고서 관련, `정`=정정, `철`=철회,
`공`=공정위, `연`=연결 등. 정확한 전체 표는 **[Inferred]** — 파싱에 필수 아님(무시 가능).

**파서 주의점**:
- `report_nm`은 **우측 공백 패딩** → `title = report_nm.strip()`. **[Verified]**
- `rcept_no`가 `document.xml`·원문뷰어 URL의 join 키. `filings.url`은 뷰어 링크로 구성:
  `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}` **[Inferred]** (Citation.url 규약).
- `rcept_dt`(YYYYMMDD 문자열) → `filings.filed_at`(date) 파싱.
- 결과 없음/무자료는 status `013`(§5)로 옴 → 빈 `list`가 아니라 status로 판별. **[Inferred]**

---

## 3) `fnlttSinglAcntAll.json` — 단일회사 전체 재무제표

**★ 숫자의 단일 출처(single source of truth).** `financials` 테이블 및 digest `MetricCard.value`는
오직 여기서 온다(핵심 원칙: "숫자는 구조화 API에서만").

### 요청

```
GET .../fnlttSinglAcntAll.json?crtfc_key=***&corp_code=00126380&bsns_year=2023&reprt_code=11011&fs_div=CFS
```

| 파라미터 | 필수 | 타입 | 의미 |
|---|---|---|---|
| `crtfc_key` | 필수 | string | 인증키 |
| `corp_code` | 필수 | string(8) | 고유번호 |
| `bsns_year` | 필수 | string(YYYY) | 사업연도 |
| `reprt_code` | 필수 | string(5) | 보고서 코드(아래) |
| `fs_div` | 필수 | string | `CFS`=연결 / `OFS`=별도(개별) |

**`reprt_code`** (→ `financials.fiscal_quarter` 유도):

| 값 | 의미 | 분기 매핑 | 검증 |
|---|---|---|---|
| `11011` | 사업보고서(연간) | 연간 (quarter=null 또는 4) | **[Verified]** 원문 `<DOCUMENT-NAME ACODE="11011">사업보고서` |
| `11012` | 반기보고서 | H1 (Q2) | **[Inferred]** |
| `11013` | 1분기보고서 | Q1 | **[Inferred]** |
| `11014` | 3분기보고서 | Q3 | **[Inferred]** |

**`fs_div`**: `CFS`=연결재무제표(consolidated), `OFS`=별도/개별(separate).
CFS로 정상 응답 확인 **[Verified]**; OFS는 **[Inferred]**.

### 응답 (`status=000`, 삼성전자 2023 CFS 기준 `list` 176건)

`list[]` 원소:

| 필드 | 타입 | 의미 | 샘플값 |
|---|---|---|---|
| `rcept_no` | string(14) | 이 수치가 나온 공시 접수번호 → `filings` join | `20240312000736` |
| `reprt_code` | string | 보고서 코드 | `11011` |
| `bsns_year` | string(YYYY) | 사업연도 | `2023` |
| `corp_code` | string(8) | 고유번호 | `00126380` |
| `sj_div` | string | 재무제표 구분(아래) | `IS` |
| `sj_nm` | string | 재무제표명 | `손익계산서` |
| `account_id` | string | **표준 계정 ID(IFRS/DART 택소노미)** | `dart_OperatingIncomeLoss` |
| `account_nm` | string | 계정 국문명(회사별로 상이) | `영업이익` |
| `account_detail` | string | 상세(주로 `-`; SCE는 차원경로) | `-` |
| `thstrm_nm` | string | 당기명 | `제 55 기` |
| `thstrm_amount` | **string** | **당기 금액** | `6566976000000` |
| `frmtrm_nm` | string | 전기명 | `제 54 기` |
| `frmtrm_amount` | **string** | **전기 금액(YoY 계산용)** | `43376630000000` |
| `bfefrmtrm_nm` / `bfefrmtrm_amount` | string | 전전기명/금액 | — |
| `ord` | string(int) | 정렬 순서 | `24` |
| `currency` | string | 통화 | `KRW` |

**`sj_div` 5종**(모두 관찰됨) **[Verified]**: `BS`=재무상태표, `IS`=손익계산서,
`CIS`=포괄손익계산서, `CF`=현금흐름표, `SCE`=자본변동표.

### 금액 필드 포맷 (실측)

176행 × (thstrm/frmtrm/bfefrmtrm) 스캔 결과 **[Verified]**:

- **타입은 문자열**. 예: `"455905980000000"`.
- **콤마 없음** (thousand separator 없음). 발견 0건.
- **음수는 선행 `-`**: 예 `법인세비용(수익)` `thstrm_amount="-4480835000000"` (음수 77건).
- **빈값은 빈 문자열 `""`** (dash `"-"` 아님): 예 자본변동표 `기초자본`의 일부 컬럼 (9건).
- **단위 스케일링 없음**: 값은 **원(KRW) 절대금액**. (예: 총자산 `455905980000000` = 약 455.9조원.)
  → `financials.unit`은 통화 그대로(`KRW`), 별도 ×1000/×1e6 없음.
- `account_detail`은 일반 계정에서 `-`. **자본변동표(SCE)** 는 `|` 구분 XBRL 차원 경로
  (예: `자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|...`). **[Verified]**

**금액 파서 규약** (→ `numeric(24,4)`):
```
raw == "" (또는 None)        -> 값 없음 → 스킵(행 미적재)
raw.startswith("-")          -> 음수
콤마/공백 없음               -> Decimal(raw) 그대로
```

### 계정 매핑 (account_id → MetricCard.key) — 실측 [Verified]

`account_nm`은 회사마다 다름(삼성은 매출을 **`영업수익`**으로 표기) → **`account_id`(표준 택소노미)로
매핑**하고 `account_nm`은 라벨로만 사용.

| MetricCard.key | account_id | account_nm(삼성) | sj_div | 2023 thstrm | 비고 |
|---|---|---|---|---|---|
| `revenue` | `ifrs-full_Revenue` | 영업수익 | IS | 258,935,494,000,000 | 회사에 따라 `매출액` 라벨 |
| `operating_income` | `dart_OperatingIncomeLoss` | 영업이익 | IS | 6,566,976,000,000 | **DART 확장(`dart_`) 택소노미** (영업이익은 K-특화 개념) |
| `net_income` | `ifrs-full_ProfitLoss` | 당기순이익(손실) | IS | 15,487,100,000,000 | IS/CIS/CF **3곳에 동일값 중복** → sj_div=IS 하나만 채택 |
| `eps` | `ifrs-full_BasicEarningsLossPerShare` | 기본주당이익 | IS | 2,131 | **주당 원(per-share)** — 절대금액 아님 |
| `operating_margin` | (없음, **파생**) | — | — | op_income / revenue | API에 없음 → 두 API 값의 산술 계산 |

- **중복 주의**: `ifrs-full_ProfitLoss`는 IS·CIS·CF 세 곳에 동일값으로 나옴. `financials`의
  `UNIQUE(company_id, period, metric, source)` 충족을 위해 **`sj_div`를 결정적으로 하나 선택**(IS 우선). **[Verified]**
- 지배주주 귀속 순이익 `ifrs-full_ProfitLossAttributableToOwnersOfParent`(14,473,401,000,000)도 있음
  → net_income을 "총" vs "지배주주"로 정의할지 결정 필요. 기본은 총(`ifrs-full_ProfitLoss`). **[Verified 값 확인]**
- `operating_margin`은 LLM이 아니라 **구조화 값끼리의 산술**이므로 핵심 원칙 위배 아님(숫자 지어내기 아님).

---

## 4) `document.xml` — 공시 원문 (임베딩 소스)

`filing_chunks.content`의 원천. `rcept_no`로 원문 문서를 받는다.

### 요청

```
GET .../document.xml?crtfc_key=***&rcept_no=20240312000736
```

| 파라미터 | 필수 | 타입 | 의미 |
|---|---|---|---|
| `crtfc_key` | 필수 | string | 인증키 |
| `rcept_no` | 필수 | string(14) | list.json에서 얻은 접수번호 |

### 응답 — **포맷이 문서마다 다름(★핵심 주의)**

Content-Type `application/x-msdownload` = **ZIP 바이너리**. ZIP 내부는 공시 종류에 따라 **두 포맷**:

**(A) 정기보고서(사업보고서 등) — DART DSD 커스텀 XML** **[Verified]**
- rcept_no `20240312000736`(삼성 2023 사업보고서): ZIP 596KB → 압축해제 시 메인 XML **6.15MB**.
- ZIP 멤버 3개: `20240312000736.xml`(본문) + `..._00760.xml`, `..._00761.xml`(첨부).
- 루트: `<DOCUMENT xsi:noNamespaceSchemaLocation="dart4.xsd">` — 스키마 `dart4.xsd` 기반. **UTF-8, meta charset 선언 없음.**
- 태그 빈도(실측): `TD`(24454) `TE`(23547) `P`(17065) `TR`(9072) `TABLE`(1907)
  `SPAN`(1094) `TU`(558) `TITLE`(135) `SECTION`(53) `LIBRARY`(18) `DOCUMENT`(2) `PGBRK`(230).
- 구조 계층:
  - `<DOCUMENT-NAME ACODE="11011">사업보고서` — ACODE = reprt_code.
  - `<COMPANY-NAME AREGCIK="00126380">삼성전자주식회사` — AREGCIK = corp_code.
  - `<SECTION-1 AID=".." ACLASS="MANDATORY">` → `<SECTION-2>` (중첩 섹션).
  - `<TITLE ATOC="Y" AASSOCNOTE="D-0-1-1-0" ATOCID="4">1. 회사의 개요</TITLE>` — **목차 연결 헤더(청크 경계로 최적)**.
  - `<P><SPAN USERMARK="F-14 B">…</SPAN> 서술텍스트…</P>` — **서술 본문(임베딩 대상)**.
  - 표: `<TABLE ACLASS="EXTRACTION|NORMAL">`, `<TU AUNIT="PERIODFROM" AUNITVALUE="20230101">` — 셀에 기계판독 속성.
  - 본문 텍스트(태그 제거) 약 **63.2만자** (원문 566만자).

**(B) 자율/수시공시 — xforms HTML** **[Verified]**
- rcept_no `20240628800773`(지속가능경영보고서 자율공시): ZIP 2.4KB, 멤버 1개.
- 루트 `<html>`, `<meta ... charset=euc-kr>`, `.xforms` 인라인 CSS, `<table>/<td>` 위주.

### 인코딩 함정 (★) **[Verified]**

- (B)는 `<meta charset=euc-kr>`로 **선언**하지만 **실제 바이트는 UTF-8** (예: `\xeb\x8f\x8b\xec\x9b\x80\xec\xb2\xb4` = `돋움체`). **선언 무시하고 UTF-8 우선 디코드**, 실패 시 euc-kr/cp949 폴백.
- (A)는 charset 선언 자체가 없고 UTF-8.
- → 파서는 **바이트로 인코딩 감지**(선언을 신뢰하지 않음).

### 청킹 설계 메모 (→ `filing_chunks`)

- **루트 스니핑**으로 포맷 분기: `<DOCUMENT`(DSD) vs `<html`(xforms). **[Verified]**
- DSD(A): `<SECTION-*>`/`<TITLE ATOC="Y">`를 **청크 경계**로, `<P>`/`<SPAN>` 텍스트를 본문으로 추출.
  `<TITLE>`을 청크 헤더/메타로 보존. `<PGBRK>`는 무시.
- **표는 산문 임베딩에서 제외**. 특히 `ACLASS="EXTRACTION"` 표는 XBRL 추출 대상(=재무 API의 원천)이므로
  숫자는 §3에서 가져오고, 표를 프로즈로 임베딩하지 않음(원칙: 숫자는 구조화 API에서만). 필요 시 표는 별도 직렬화.
- 첨부 XML(`_NNNNN.xml`)은 본문 외 부속 — Phase 2에서 포함 여부 결정. **[Verified 존재]**
- 6MB급 본문 → **섹션 단위 스트리밍/청킹** 필요(메모리·토큰 예산). **[Inferred]**
- xforms HTML(B)은 표준 HTML 파서로 텍스트 추출.
- XXE/billion-laughs 방지: `defusedxml`(또는 안전 옵션 HTML 파서) 사용. **[Verified 방침]**

---

## 5) status / 에러 코드 표

`status`(list.json/재무제표 등 JSON) 또는 ZIP 엔드포인트의 오류 XML `<result><status>`에 담긴다.

| status | 의미 | 처리 |
|---|---|---|
| `000` | 정상 | 진행 **[Verified]** |
| `010` | 등록되지 않은 키 | 설정 오류 → 중단 **[Inferred]** |
| `011` | 사용할 수 없는 키(활성화 필요) | 중단 **[Inferred]** |
| `012` | 접근할 수 없는 IP | 중단 **[Inferred]** |
| `013` | 조회된 데이터 없음(무자료) | 정상 스킵(빈 결과) **[Inferred]** |
| `014` | 파일 없음 | 스킵 **[Inferred]** |
| `020` | **요청 제한 초과(레이트리밋)** | 백오프 후 재시도 **[Inferred]** |
| `021` | 조회 가능 회사 수 초과(최대 100) | 배치 축소 **[Inferred]** |
| `100` | 부적절한 파라미터 값 | 요청 수정 **[Inferred]** |
| `101` | 부적절한 접근 | 중단 **[Inferred]** |
| `800` | 시스템 점검 | 재시도 **[Inferred]** |
| `900` | 정의되지 않은 오류 | 재시도/로그 **[Inferred]** |
| `901` | 개인정보 보유기간 만료 계정 | 중단 **[Inferred]** |

- 실측은 `000`만 확인 **[Verified]**; 나머지는 문서 기반 **[Inferred]**.
- **ZIP 엔드포인트(corpCode/document)의 오류 판별**: 응답 앞 2바이트가 `PK`면 ZIP,
  아니면 `<result><status>` XML(오류) → **바이트 시그니처로 분기**. **[Inferred]**

---

## 6) DB 스키마 매핑 초안 (파서 → 컬럼)

`backend/db/init.sql` / `backend/app/db/models.py` 기준. 각 매핑 옆 태그는 소스 필드 확인 여부.

### `companies` (원천: corpCode.xml + list.json)

| 컬럼 | 소스 | 비고 |
|---|---|---|
| `dart_corp_code` (UNIQUE) | corpCode `corp_code` | join 키 **[Verified]** |
| `name` | corpCode `corp_name` | **[Verified]** |
| `name_en` | corpCode `corp_eng_name` | 원문 보존 **[Verified]** |
| `ticker` | corpCode `stock_code` | `.strip()`, 공백→null **[Verified]** |
| `market` | list.json `corp_cls` 매핑 | Y→KOSPI 등 (corpCode엔 없음) **[Verified: Y]** |
| `source` | 상수 `'dart'` | — |
| `sec_cik` | (SEC 전용) | null |

### `filings` (원천: list.json)

| 컬럼 | 소스 | 비고 |
|---|---|---|
| `company_id` | corp_code → companies FK | **[Verified]** |
| `source` | `'dart'` | — |
| `filing_type` | `report_nm`(strip) 또는 `pblntf_ty` 분류 | 분류 규칙 Phase 2 **[Inferred]** |
| `title` | `report_nm`.strip() | **공백패딩 제거 필수** **[Verified]** |
| `period` | `bsns_year`+`reprt_code` 유도 | 정기보고서 한정 **[Inferred]** |
| `filed_at` | `rcept_dt`(YYYYMMDD)→date | **[Verified]** |
| `url` | `dsaf001/main.do?rcpNo={rcept_no}` | 뷰어 링크(Citation.url) **[Inferred]** |

> `rcept_no`는 컬럼이 아니지만 document.xml·financials 조인의 자연키 → `meta`/조회에 활용.

### `filing_chunks` (원천: document.xml)

| 컬럼 | 소스 | 비고 |
|---|---|---|
| `filing_id` | rcept_no → filings FK | **[Verified]** |
| `chunk_index` | 순번(0..) | 섹션 순서 |
| `content` | DSD `<P>`/`<SPAN>` 텍스트(섹션별) | 표 제외 **[Verified 구조]** |
| `embedding vector(1024)` | (Phase 3 KURE-v1) | 여기서 미생성 |
| `meta jsonb` | `{rcept_no, section_title(<TITLE>), atoc_id, ...}` | 인용 근거 보존 **[Verified 소스]** |

### `financials` (원천: fnlttSinglAcntAll.json — ★숫자의 단일 출처)

| 컬럼 | 소스 | 비고 |
|---|---|---|
| `company_id` | `corp_code` → companies FK | **[Verified]** |
| `filing_id` | `rcept_no` → filings FK(nullable) | 행마다 rcept_no 포함 **[Verified]** |
| `fiscal_year` | `bsns_year`(int) | **[Verified]** |
| `fiscal_quarter` | `reprt_code` 유도 | 11011→null/4 등 **[Verified: 11011]** |
| `period` | `bsns_year`(+분기) 정규화 문자열 | digest의 `"2026Q1"` 규약과 정렬 **[Inferred]** |
| `metric` | `account_id`→MetricCard.key 매핑 | §3 표 **[Verified]** |
| `value numeric(24,4)` | `thstrm_amount` 파싱 | `""`→스킵, `-`음수, 콤마없음 **[Verified]** |
| `unit` | `currency`(스케일링 없음, 원 절대값) | `KRW` **[Verified]** |
| `currency` | `currency` | `KRW` **[Verified]** |
| `source` | `'dart'` | — |

- **YoY(`MetricCard.yoy_delta_pct`)**: 한 번의 호출로 `thstrm`(당기)+`frmtrm`(전기)를 모두 받으므로,
  `frmtrm_amount`를 전년 period 행으로 함께 적재하거나 digest 단계에서 두 값으로 계산. **[Verified 데이터]**
- **중복 제거**: `ifrs-full_ProfitLoss`(IS/CIS/CF 중복) → sj_div=IS 하나만 (UNIQUE 제약 대응). **[Verified]**

---

## 7) 다음 단계(Phase 2-3) & 수동 확인 필요

- **[구현 예정 Phase 2]** `DartClient`(현 스텁, `backend/app/clients/dart.py`) 실제 httpx 구현:
  `corpCode.xml`(ZIP/XML) · `list.json` · `fnlttSinglAcntAll.json` · `document.xml`.
  ZIP 시그니처 분기, status 코드 처리, 레이트리밋 백오프.
- **[구현 예정 Phase 2]** 파싱/청킹: DSD vs xforms HTML 분기, 섹션 청킹, 표 제외.
- **[구현 예정 Phase 3]** KURE-v1 임베딩 → `filing_chunks.embedding`, RAG, LLM 요약(인용 강제).
- **수동 확인 필요 항목**:
  - 레이트리밋 실제 임계(분당 N회)·`020` 트리거 조건 — 실측 안 함(호출 최소화). **[Unknown]**
  - `reprt_code` 11012/11013/11014, `fs_div=OFS`, `corp_cls` K/N/E 실응답 — **[Inferred]**.
  - status `010`~`901` 실제 문자열/발생 조건 — `000`만 확인. **[Inferred]**
  - EPS(`ifrs-full_BasicEarningsLossPerShare`) 통화/스케일: 값 `2131`·currency `KRW` 확인했으나
    분기보고서에서 누적/단분기 구분 등은 **[Unknown]**.
  - `document.xml` 첨부(`_NNNNN.xml`) 포함 정책 및 대용량(수 MB) 스트리밍 처리 — **[Inferred]**.
  - `filing_type` 분류 규칙(`report_nm`/`pblntf_detail_ty` → 우리 타입) — **[Inferred]**.
