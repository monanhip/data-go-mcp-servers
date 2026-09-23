"""API client for ITS 국가교통정보센터 OpenAPI (교통소통정보·돌발상황정보).

- 교통소통정보: https://openapi.its.go.kr:9443/trafficInfo
- 돌발상황정보: https://openapi.its.go.kr:9443/eventInfo
- CCTV 정보: https://openapi.its.go.kr:9443/cctvInfo

주의:
- ITS OpenAPI 키는 공공데이터포털 키와 별도로 https://www.its.go.kr/opendata/ 에서 발급받는다.
- 국내 IP에서만 응답한다.
- 개발계정은 하루 1,000건 제한이 있으므로 호출 결과를 캐시해서 써야 한다.
"""

import httpx
import json
import os
from .models import (
    KOREA_BBOX,
    CctvCamera,
    CctvResponse,
    CctvType,
    EventKind,
    EventResponse,
    RoadType,
    TrafficEvent,
    TrafficLink,
    TrafficResponse,
)
from typing import Any, Dict, List, Optional


class ItsApiError(Exception):
    """ITS API 오류."""


# 성공으로 취급하는 resultCode 값
_SUCCESS_CODES = {None, "", "0", "00", 0}


class ItsRoadTrafficAPIClient:
    """ITS 국가교통정보센터 OpenAPI 클라이언트."""

    BASE_URL = "https://openapi.its.go.kr:9443"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        """API 클라이언트 초기화.

        Args:
            api_key: ITS OpenAPI 인증키. None이면 ``ITS_API_KEY``, ``API_KEY`` 환경변수 순으로 사용
            base_url: API 기본 URL (테스트용)
            timeout: 요청 타임아웃(초). 전국 소통정보는 응답이 크므로 넉넉히 잡는다.
            transport: httpx 전송 계층 (테스트용)
        """
        self.api_key = api_key or os.getenv("ITS_API_KEY") or os.getenv("API_KEY")
        if not self.api_key:
            raise ValueError(
                "API key is required. Set ITS_API_KEY (or API_KEY) environment variable "
                "or pass api_key parameter. Get a key at https://www.its.go.kr/opendata/"
            )
        self.base_url = (base_url or os.getenv("ITS_API_BASE_URL") or self.BASE_URL).rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료."""
        await self.close()

    async def close(self) -> None:
        """HTTP 클라이언트 종료."""
        await self.client.aclose()

    async def _request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """API 요청 후 JSON 응답을 반환."""
        query = {"apiKey": self.api_key, "getType": "json", **KOREA_BBOX, **params}
        url = f"{self.base_url}/{endpoint}"
        try:
            response = await self.client.get(url, params=query)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ItsApiError(f"HTTP error {e.response.status_code} from {endpoint}") from e
        except httpx.HTTPError as e:
            raise ItsApiError(
                f"Request to {endpoint} failed: {e!r}. ITS OpenAPI는 국내 IP에서만 응답합니다."
            ) from e

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            snippet = response.text[:200].replace("\n", " ")
            raise ItsApiError(f"Non-JSON response from {endpoint}: {snippet}") from e

        if not isinstance(payload, dict):
            raise ItsApiError(f"Unexpected response from {endpoint}: {type(payload).__name__}")

        response_part = payload.get("response")
        header = (
            payload.get("header")
            or (response_part.get("header") if isinstance(response_part, dict) else None)
            or {}
        )
        code = header.get("resultCode")
        if code not in _SUCCESS_CODES:
            raise ItsApiError(f"ITS API error {code}: {header.get('resultMsg', 'unknown error')}")
        return payload

    @staticmethod
    def _extract_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """응답에서 item 목록을 꺼낸다. items가 단건이면 dict로 오기도 한다.

        - 소통·돌발: ``{"header": ..., "body": {"items": [...]}}``
        - CCTV: ``{"response": {"data": [...], "datacount": N}}``
        """
        body = payload.get("body")
        response_part = payload.get("response")
        if isinstance(body, dict):
            items: Any = body.get("items")
        elif isinstance(response_part, dict):
            items = response_part.get("data")
        else:
            items = payload.get("items", payload.get("data"))
        if isinstance(items, dict):
            # {"items": {"item": [...]}} 형태 대응
            items = items.get("item", items)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def _total_count(payload: Dict[str, Any], default: int) -> int:
        for section in (payload.get("body"), payload.get("header"), payload.get("response")):
            if not isinstance(section, dict):
                continue
            for key in ("totalCount", "datacount"):
                if section.get(key) is not None:
                    try:
                        return int(section[key])
                    except (TypeError, ValueError):
                        pass
        return default

    async def get_traffic_info(
        self,
        road_type: RoadType = RoadType.EXPRESSWAY,
        direction: str = "all",
        bbox: Optional[Dict[str, float]] = None,
    ) -> TrafficResponse:
        """교통소통정보(링크별 속도·통행시간)를 조회.

        Args:
            road_type: 도로 유형 (ex: 고속도로, its: 국도, all: 전체)
            direction: 방향 (all, up, down)
            bbox: 조회 영역 {minX, maxX, minY, maxY}. 기본값은 전국

        Returns:
            TrafficResponse
        """
        road_type = RoadType(road_type)
        params: Dict[str, Any] = {"type": road_type.value, "drcType": direction}
        if bbox:
            params.update(bbox)
        payload = await self._request("trafficInfo", params)
        rows = self._extract_items(payload)
        items = []
        for row in rows:
            link = TrafficLink.model_validate(row)
            if road_type != RoadType.ALL:
                link.road_type = road_type.value
            items.append(link)
        return TrafficResponse(items=items, total_count=self._total_count(payload, len(items)))

    async def get_events(
        self,
        road_type: RoadType = RoadType.ALL,
        event_kind: EventKind = EventKind.ALL,
        bbox: Optional[Dict[str, float]] = None,
    ) -> EventResponse:
        """돌발상황정보(사고·공사·기상·재난 등)를 조회.

        Args:
            road_type: 도로 유형 (ex, its, all)
            event_kind: 돌발 유형 (acc, cor, wea, ete, dis, etc, all)
            bbox: 조회 영역. 기본값은 전국

        Returns:
            EventResponse
        """
        road_type = RoadType(road_type)
        event_kind = EventKind(event_kind)
        params: Dict[str, Any] = {"type": road_type.value, "eventType": event_kind.value}
        if bbox:
            params.update(bbox)
        payload = await self._request("eventInfo", params)
        rows = self._extract_items(payload)
        items = []
        for row in rows:
            if road_type != RoadType.ALL and not row.get("type"):
                row = {**row, "type": road_type.value}
            items.append(TrafficEvent.model_validate(row))
        return EventResponse(items=items, total_count=self._total_count(payload, len(items)))

    async def get_cctv(
        self,
        road_type: RoadType = RoadType.EXPRESSWAY,
        cctv_type: Optional[str] = None,
        bbox: Optional[Dict[str, float]] = None,
    ) -> CctvResponse:
        """CCTV 목록(위치·영상 URL)을 조회.

        Args:
            road_type: 도로 유형 (ex: 고속도로, its: 국도)
            cctv_type: 영상 형식 코드. 기본값은 ``ITS_CCTV_TYPE`` 환경변수, 없으면 4
                (실시간 스트리밍 HLS, HTTPS). 1: HLS(HTTP), 2: 동영상, 3: 정지영상, 5: 동영상(HTTPS)
            bbox: 조회 영역. 기본값은 전국

        Returns:
            CctvResponse. 영상 URL에는 만료되는 인증 토큰이 포함될 수 있다.
        """
        road_type = RoadType(road_type)
        if road_type == RoadType.ALL:
            raise ValueError("CCTV는 road_type ex 또는 its로 조회해야 합니다")
        cctv_type = CctvType(
            str(cctv_type or os.getenv("ITS_CCTV_TYPE") or CctvType.STREAM_HTTPS.value)
        )
        params: Dict[str, Any] = {"type": road_type.value, "cctvType": cctv_type.value}
        if bbox:
            params.update(bbox)
        payload = await self._request("cctvInfo", params)
        items = []
        for row in self._extract_items(payload):
            camera = CctvCamera.model_validate(row)
            camera.road_type = road_type.value
            if camera.url:
                items.append(camera)
        return CctvResponse(items=items, total_count=self._total_count(payload, len(items)))
