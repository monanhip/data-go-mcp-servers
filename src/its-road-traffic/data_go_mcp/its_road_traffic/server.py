"""MCP server for ITS Road Traffic (국가교통정보센터 전국 도로 소통·돌발 정보)."""

import os
import sys
from .models import EVENT_KIND_LABELS
from .service import TrafficService
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from typing import Any, Dict, List, Optional


# 환경변수 로드
load_dotenv()

# MCP 서버 인스턴스 생성
mcp = FastMCP("ITS Road Traffic")

_service: Optional[TrafficService] = None


def get_service() -> TrafficService:
    """요청 간에 캐시를 공유하도록 서비스 인스턴스를 재사용한다."""
    global _service
    if _service is None:
        if os.getenv("ITS_DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
            from .demo import DemoTrafficSource

            source: Any = DemoTrafficSource()
            demo = True
        else:
            from .api_client import ItsRoadTrafficAPIClient

            source = ItsRoadTrafficAPIClient()
            demo = False
        _service = TrafficService(
            source,
            event_interval=float(os.getenv("ITS_EVENT_POLL_SECONDS") or 60),
            traffic_ttl=float(os.getenv("ITS_TRAFFIC_TTL_SECONDS") or 300),
            cctv_ttl=float(os.getenv("ITS_CCTV_TTL_SECONDS") or 1800),
            demo=demo,
        )
    return _service


def _road_type_or_error(road_type: str) -> Optional[str]:
    value = (road_type or "").strip().lower()
    aliases = {
        "고속도로": "ex",
        "expressway": "ex",
        "국도": "its",
        "national": "its",
        "전체": "all",
    }
    value = aliases.get(value, value)
    return value if value in ("ex", "its", "all") else None


def _find_road_key(roads: List[Dict[str, Any]], road_name: str) -> Optional[str]:
    query = road_name.replace(" ", "")
    for road in roads:
        if road["name"].replace(" ", "") == query or road.get("route_no") == query:
            return road["key"]
    for road in roads:
        if query in road["name"].replace(" ", ""):
            return road["key"]
    return None


@mcp.tool()
async def list_roads(road_type: str = "ex", only_congested: bool = False) -> Dict[str, Any]:
    """전국 고속도로/국도 노선별 소통 요약을 조회합니다.

    List nationwide expressways or national roads with average speed, congestion grade
    and number of ongoing incidents per road.

    Args:
        road_type: 도로 유형 - "ex"(고속도로, default), "its"(국도), "all"(전체)
        only_congested: True면 서행·정체 노선만 반환 (Return only slow/jammed roads)

    Returns:
        Dictionary containing:
        - roads: 노선 목록 (name, route_no, avg_speed, grade_label, grade_share,
          event_count, urgent_count)
        - total_count: 노선 수

    Examples:
        - list_roads("ex") - 고속도로 노선별 소통 현황
        - list_roads("its", only_congested=True) - 정체 중인 국도
    """
    normalized = _road_type_or_error(road_type)
    if normalized is None:
        return {"error": f"Invalid road_type: {road_type}. Use ex, its or all", "roads": []}
    try:
        roads = await get_service().roads(normalized)
    except Exception as e:
        return {"error": str(e), "roads": []}
    roads = [road for road in roads if road["measured_count"] or road["event_count"]] or roads
    if only_congested:
        roads = [road for road in roads if road["grade"] in ("slow", "jam")]
    return {
        "roads": [
            {
                "key": road["key"],
                "name": road["name"],
                "road_type": road["road_type"],
                "route_no": road["route_no"],
                "avg_speed_kmh": road["avg_speed"],
                "grade": road["grade_label"],
                "grade_share_percent": road["grade_share"],
                "event_count": road["event_count"],
                "urgent_event_count": road["urgent_count"],
                "updated_at": road["updated_at"],
            }
            for road in roads
        ],
        "total_count": len(roads),
    }


@mcp.tool()
async def get_road_traffic(road_name: str, road_type: str = "ex") -> Dict[str, Any]:
    """특정 노선의 방향별 소통 상황과 돌발 정보를 조회합니다.

    Get per-direction traffic flow, slowest sections and ongoing incidents for a road.

    Args:
        road_name: 노선명 또는 노선번호 (e.g. "경부고속도로", "경부", "1", "국도1호선")
        road_type: 도로 유형 - "ex"(고속도로, default) 또는 "its"(국도)

    Returns:
        Dictionary containing:
        - name, route_no, avg_speed, grade_label
        - directions: 방향별 평균속도, 등급 비율, 저속 구간
        - events: 진행 중인 돌발 목록

    Examples:
        - get_road_traffic("경부고속도로")
        - get_road_traffic("영동")
        - get_road_traffic("국도3호선", road_type="its")
    """
    normalized = _road_type_or_error(road_type)
    if normalized not in ("ex", "its"):
        return {"error": f"Invalid road_type: {road_type}. Use ex or its"}
    service = get_service()
    try:
        roads = await service.roads(normalized)
        key = _find_road_key(roads, road_name)
        if key is None:
            return {
                "error": f"Road not found: {road_name}",
                "available_roads": [road["name"] for road in roads[:30]],
            }
        return await service.road(key) or {"error": f"Road not found: {road_name}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_traffic_events(
    event_kind: str = "all",
    road_type: str = "all",
    road_name: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """전국 도로의 진행 중인 사고·공사·돌발 상황을 조회합니다.

    Get ongoing traffic incidents (accidents, construction, weather, disasters) nationwide.

    Args:
        event_kind: 돌발 유형 - "all"(default), "acc"(교통사고), "cor"(공사), "wea"(기상),
            "ete"(기타돌발), "dis"(재난), "etc"(기타). 쉼표로 여러 개 지정 가능 (e.g. "acc,ete")
        road_type: 도로 유형 - "all"(default), "ex"(고속도로), "its"(국도)
        road_name: 노선명으로 거르기 (선택, 부분 일치)
        limit: 최대 반환 건수 (default: 50)

    Returns:
        Dictionary containing:
        - events: 돌발 목록 (kind_label, road_name, direction_label, message, lat, lon,
          started_at, ends_at)
        - total_count: 조건에 맞는 전체 건수
        - counts_by_kind: 유형별 건수

    Examples:
        - get_traffic_events("acc") - 전국 교통사고
        - get_traffic_events("acc,ete", road_type="ex") - 고속도로 사고·돌발
        - get_traffic_events(road_name="경부") - 경부고속도로 돌발
    """
    kinds = [k.strip().lower() for k in event_kind.split(",") if k.strip()]
    kinds = [] if "all" in kinds else kinds
    invalid = [k for k in kinds if k not in EVENT_KIND_LABELS]
    if invalid:
        return {"error": f"Invalid event_kind: {', '.join(invalid)}", "events": []}
    normalized = _road_type_or_error(road_type)
    if normalized is None:
        return {"error": f"Invalid road_type: {road_type}. Use ex, its or all", "events": []}
    try:
        events = await get_service().filtered_events(road_type=normalized, kinds=kinds)
    except Exception as e:
        return {"error": str(e), "events": []}
    if road_name:
        query = road_name.replace(" ", "")
        events = [e for e in events if query in (e["road_name"] or "").replace(" ", "")]
    counts: Dict[str, int] = {}
    for event in events:
        counts[event["kind_label"]] = counts.get(event["kind_label"], 0) + 1
    return {
        "events": events[: max(1, limit)],
        "total_count": len(events),
        "counts_by_kind": counts,
    }


@mcp.tool()
async def list_cctv(
    road_name: Optional[str] = None,
    road_type: str = "ex",
    limit: int = 30,
) -> Dict[str, Any]:
    """고속도로/국도 CCTV를 노선별로 묶어 조회합니다 (영상 URL 포함).

    List traffic CCTV cameras grouped by road, ordered along the road.

    Args:
        road_name: 노선명·노선번호·지점명으로 거르기 (선택, e.g. "경부", "1", "판교")
        road_type: 도로 유형 - "ex"(고속도로, default), "its"(국도), "all"(전체)
        limit: 노선별 최대 CCTV 수 (default: 30)

    Returns:
        Dictionary containing:
        - roads: 노선별 CCTV 묶음 (name, route_no, count, cameras[name, location, url,
          media(hls/video/image), lat, lon])
        - total_count: 전체 CCTV 수

    Note:
        영상 URL에는 만료되는 인증 토큰이 포함될 수 있어 오래된 URL은 재생되지 않을 수 있습니다.
        HLS(m3u8)는 Safari, VLC, 또는 hls.js로 재생할 수 있습니다.

    Examples:
        - list_cctv() - 고속도로 노선별 CCTV 개수와 목록
        - list_cctv("경부") - 경부고속도로 CCTV
        - list_cctv("국도3호선", road_type="its")
    """
    normalized = _road_type_or_error(road_type)
    if normalized is None:
        return {"error": f"Invalid road_type: {road_type}. Use ex, its or all", "roads": []}
    try:
        groups = await get_service().cctv_groups(normalized, road_name)
    except Exception as e:
        return {"error": str(e), "roads": []}
    keep = ("name", "location", "url", "media", "lat", "lon")
    return {
        "roads": [
            {
                "key": group["key"],
                "name": group["name"],
                "road_type": group["road_type"],
                "route_no": group["route_no"],
                "count": group["count"],
                "cameras": [{k: c[k] for k in keep} for c in group["items"][: max(1, limit)]],
            }
            for group in groups
        ],
        "total_count": sum(group["count"] for group in groups),
    }


def main():
    """메인 함수."""
    if not (os.getenv("ITS_API_KEY") or os.getenv("API_KEY") or os.getenv("ITS_DEMO_MODE")):
        # stdout은 MCP 프로토콜 채널이므로 경고는 stderr로 출력
        print("Warning: ITS_API_KEY environment variable is not set", file=sys.stderr)
        print(
            "Please set it to use the ITS traffic API (or ITS_DEMO_MODE=1 for demo data)",
            file=sys.stderr,
        )
        print("You can get an API key from: https://www.its.go.kr/opendata/", file=sys.stderr)

    # MCP 서버 실행
    mcp.run()


if __name__ == "__main__":
    main()
