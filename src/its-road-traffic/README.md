# ITS Road Traffic MCP Server & Web Dashboard

전국 고속도로·국도 **실시간 소통정보**와 **사고·돌발 알림**을 제공하는 MCP 서버이자 모바일/데스크탑 하이브리드 웹 앱(PWA)입니다.
데이터는 국토교통부 ITS 국가교통정보센터 OpenAPI(교통소통정보·돌발상황정보)를 사용합니다.

## 📋 Overview

| 구성 | 설명 |
|------|------|
| **웹 대시보드** (`data-go-mcp.its-road-traffic-web`) | 모바일에서는 고속도로/국도 목록 → 노선 상세, 데스크탑에서는 목록·지도·상세를 한 화면에 표시. 사고·돌발 발생 시 알림 |
| **MCP 서버** (`data-go-mcp.its-road-traffic`) | Claude 등 AI 도구에서 노선별 소통 현황과 돌발 정보를 조회 |

### 화면 구성

- **모바일 (< 1024px)**: 하단 탭 `고속도로 · 국도 · 돌발 · 알림`
  - 고속도로/국도 목록: 노선번호, 평균 속도, 원활/서행/정체 비율 막대, 진행 중 돌발 수, ★ 관심 노선
  - 노선 선택 → 상세: 방향별(상행/하행) 소통, 가장 느린 구간, 노선 지도, 해당 노선 돌발 목록
  - 돌발 탭: 지도 + 전국 돌발 피드 (유형 필터)
  - 알림 탭: 알림 권한, 푸시, 알림 받을 유형/도로/관심 노선 설정, 최근 알림
- **데스크탑 (≥ 1024px)**: `노선 목록 | 지도 | 노선 상세 + 돌발 피드` 3단 화면, 🔔 버튼으로 알림 설정
- 다크 모드, 홈 화면에 추가(PWA), 오프라인 앱 셸 캐시 지원

### 사고·돌발 알림 동작

```
ITS eventInfo ──(주기 폴링)──▶ TrafficService ──신규 돌발 감지──┬─▶ SSE(/api/stream) ─▶ 열린 탭: 토스트·배너·알림음·진동·시스템 알림
                                                                └─▶ Web Push(선택) ───▶ 앱을 닫아도 휴대폰 알림
```

- 서버 시작 시점에 이미 진행 중인 돌발은 기준선으로 보고 알리지 않습니다. 이후 **새로 등장한 돌발만** 알립니다.
- 같은 돌발이 목록에서 잠시 빠졌다가 다시 잡혀도 24시간 동안은 중복 알림하지 않습니다.
- 기본 알림 유형은 **교통사고·기타돌발·재난**이며, 기상·공사·기타도 켤 수 있습니다. "관심 노선(★)만" 옵션을 켜면 등록한 노선의 돌발만 알립니다.
- 알림을 누르면 해당 노선 상세로 이동하고 지도에서 돌발 위치를 보여줍니다.

## 🔑 API 키

ITS OpenAPI 키는 **공공데이터포털 키와 별개**입니다.

1. [ITS 국가교통정보센터 오픈데이터](https://www.its.go.kr/opendata/)에서 회원가입
2. `교통소통정보`, `돌발상황정보` API 사용 신청 → 인증키 발급
3. 환경변수 `ITS_API_KEY`(또는 `API_KEY`)에 설정

> ⚠️ ITS OpenAPI는 **국내 IP에서만 응답**합니다. 해외 서버/클라우드에서는 연결이 거부되므로 국내 서버·PC에서 실행하세요.
> 개발계정은 **하루 1,000건** 제한이 있어 기본 설정이 이에 맞춰져 있습니다 (돌발 3분 주기 480건 + 소통 10분 캐시 × 고속도로·국도 288건).

## 🚀 웹 대시보드 실행

```bash
# 설치 (Web Push 포함)
pip install "data-go-mcp.its-road-traffic[push]"

# 실제 데이터
export ITS_API_KEY="your-its-api-key"
data-go-mcp.its-road-traffic-web --port 8000

# API 키 없이 데모 데이터로 화면·알림 확인
data-go-mcp.its-road-traffic-web --demo --port 8000
```

브라우저에서 `http://localhost:8000` 을 엽니다. 데모 모드에서는 알림 탭의 **"사고 발생"** 버튼으로 알림을 시험할 수 있습니다.

### 휴대폰에서 사용하기

- 서비스 워커·시스템 알림·Web Push는 **HTTPS**(또는 `localhost`)에서만 동작합니다. 휴대폰에서 알림을 받으려면 리버스 프록시(nginx, Caddy 등)나 터널(Cloudflare Tunnel 등)로 HTTPS를 붙이세요. HTTP로 접속해도 목록·지도·화면 안 알림(토스트)은 동작합니다.
- **Android(Chrome)**: 알림 탭 → `알림 켜기` → `푸시 켜기`. 메뉴 → `홈 화면에 추가`로 앱처럼 설치할 수 있습니다.
- **iPhone(iOS 16.4+)**: Safari 공유 버튼 → `홈 화면에 추가` 후, 설치된 앱에서 알림/푸시를 켭니다 (iOS는 설치된 PWA에서만 Web Push 지원).
- 역방향 프록시 뒤에서 SSE가 끊기지 않도록 `/api/stream`은 버퍼링을 끄세요 (nginx: `proxy_buffering off;`).

### 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ITS_API_KEY` / `API_KEY` | - | ITS OpenAPI 인증키. 없으면 웹은 데모 모드로 실행 |
| `ITS_DEMO_MODE` | `false` | `1`이면 데모 데이터 사용 |
| `ITS_EVENT_POLL_SECONDS` | `180` (데모 `30`) | 돌발정보 폴링 주기(초) — 알림 지연시간 |
| `ITS_TRAFFIC_TTL_SECONDS` | `600` (데모 `60`) | 소통정보 캐시 유효시간(초) |
| `ITS_DATA_DIR` | `~/.its-road-traffic` | VAPID 키·푸시 구독 저장 위치 |
| `ITS_VAPID_PRIVATE_KEY` | 자동 생성 | Web Push VAPID 개인키 (지정하지 않으면 데이터 디렉토리에 생성) |
| `ITS_VAPID_SUBJECT` | `mailto:admin@example.com` | Web Push 발신자 연락처 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 웹 서버 주소 |

운영계정으로 호출 한도가 넉넉하다면 `ITS_EVENT_POLL_SECONDS=60`처럼 줄여 알림을 더 빨리 받을 수 있습니다.

### HTTP API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/roads?type=ex\|its\|all` | 노선별 소통 요약 |
| GET | `/api/roads/{road_key}` | 노선 상세 (예: `ex:경부고속도로`) |
| GET | `/api/events?type=&kinds=acc,ete&road=` | 진행 중 돌발 |
| GET | `/api/alerts` | 서버 시작 후 감지된 신규 돌발 (최근 50건) |
| GET | `/api/stream` | 실시간 이벤트 (SSE: `hello`, `incident`, `update`) |
| GET | `/api/status` | 서버 상태 |
| GET/POST | `/api/push/config`, `/api/push/subscribe`, `/api/push/unsubscribe` | Web Push 구독 |
| POST | `/api/demo/incident` | (데모 모드 전용) 가상 돌발 발생 |

## 🤖 MCP 서버

### Claude Desktop 설정

```json
{
  "mcpServers": {
    "its-road-traffic": {
      "command": "uvx",
      "args": ["data-go-mcp.its-road-traffic@latest"],
      "env": {
        "ITS_API_KEY": "your-its-api-key"
      }
    }
  }
}
```

API 키 없이 시험하려면 `"ITS_DEMO_MODE": "1"`을 설정하세요.

### 도구

| 도구 | 설명 | 예시 |
|------|------|------|
| `list_roads(road_type, only_congested)` | 고속도로(`ex`)/국도(`its`) 노선별 평균 속도·소통 등급·돌발 수 | `list_roads("ex", only_congested=True)` |
| `get_road_traffic(road_name, road_type)` | 노선 방향별 소통, 저속 구간, 진행 중 돌발 | `get_road_traffic("경부")` |
| `get_traffic_events(event_kind, road_type, road_name, limit)` | 전국 돌발 (acc 사고, cor 공사, wea 기상, ete 기타돌발, dis 재난) | `get_traffic_events("acc,ete", road_type="ex")` |

질문 예시: "지금 경부고속도로 상행 막혀?", "전국 고속도로에서 난 사고 알려줘", "영동고속도로 공사 구간 있어?"

## 📏 소통 등급 기준

| 도로 | 원활 | 서행 | 정체 |
|------|------|------|------|
| 고속도로 | 70km/h 이상 | 40~70km/h | 40km/h 미만 |
| 국도 | 40km/h 이상 | 20~40km/h | 20km/h 미만 |

노선 전체 등급은 평균 속도로 판정하되, 정체 구간이 30% 이상이면 정체로 표시합니다. 속도 0인 링크는 측정값 없음으로 보고 제외합니다.

## ⚠️ 알려진 제한

- ITS 소통정보에는 링크 좌표가 없어 지도에는 노선의 **대략적인 경로**(주요 노선 카탈로그)와 돌발 위치를 표시합니다. 구간 단위 색칠 지도를 원하면 국가표준노드링크 shapefile을 추가로 연동해야 합니다.
- 노선명은 `경부선`/`경부고속도로`, `일반국도 3호선`/`국도3호선` 등 표기 차이를 정규화하지만, 카탈로그에 없는 노선은 API의 도로명을 그대로 보여줍니다.
- 방향(`roadDrcType`) 값은 `up/down`, `상행/하행`만 변환하고 그 외 값은 원문 그대로 표시합니다.

## 🧪 개발

```bash
cd src/its-road-traffic
uv sync --dev --extra push
uv run pytest tests/ -v --cov=data_go_mcp.its_road_traffic
uv run data-go-mcp.its-road-traffic-web --demo
```

## 📄 License

Apache License 2.0
