"""Tests for road catalog and aggregation."""

from data_go_mcp.its_road_traffic.models import TrafficEvent, TrafficLink
from data_go_mcp.its_road_traffic.roads import (
    CATALOG,
    canonical_road,
    describe_event,
    direction_label,
    find_catalog_road,
    road_detail,
    summarize_roads,
)


def link(name, speed, road_type="ex", direction="상행", link_no="1", created="20260923174500"):
    """테스트용 링크."""
    return TrafficLink(
        roadName=name,
        roadDrcType=direction,
        linkNo=link_no,
        linkId=f"{name}-{direction}-{link_no}",
        speed=speed,
        travelTime=60,
        createdDate=created,
        road_type=road_type,
    )


def event(name, kind="교통사고", road_type="고속도로", start="20260923170000", road_no=None, x=127.0):
    """테스트용 돌발 (describe_event 결과)."""
    return describe_event(
        TrafficEvent.model_validate(
            {
                "type": road_type,
                "eventType": kind,
                "roadName": name,
                "roadNo": road_no,
                "startDate": start,
                "coordX": x,
                "coordY": 36.5,
                "roadDrcType": "하행",
                "message": f"{name} {kind}",
            }
        )
    )


class TestCatalog:
    """카탈로그 매칭 테스트."""

    def test_catalog_keys_unique(self):
        keys = [road.key for road in CATALOG]
        assert len(keys) == len(set(keys))
        assert all(len(road.path) >= 2 for road in CATALOG)

    def test_expressway_aliases(self):
        assert canonical_road("ex", "경부선") == ("경부고속도로", "1")
        assert canonical_road("ex", "경부 고속도로") == ("경부고속도로", "1")
        assert canonical_road("ex", "서울외곽순환고속도로")[0] == "수도권제1순환고속도로"
        assert canonical_road("ex", "영동") == ("영동고속도로", "50")

    def test_expressway_route_number_fallback(self):
        assert canonical_road("ex", "알수없는선", road_no="0050") == ("영동고속도로", "50")

    def test_national_road_patterns(self):
        assert canonical_road("its", "국도1호선") == ("국도1호선", "1")
        assert canonical_road("its", "일반국도 3호선") == ("국도3호선", "3")
        assert canonical_road("its", "38번국도") == ("국도38호선", "38")
        # 카탈로그에 없는 번호도 표준 이름으로 맞춘다
        assert canonical_road("its", "국도21호선") == ("국도21호선", "21")
        assert canonical_road("its", "세종대로", road_no="7") == ("국도7호선", "7")

    def test_unknown_road_keeps_name(self):
        assert canonical_road("its", "세종대로") == ("세종대로", None)
        assert canonical_road("its", "") == ("이름 미상", None)
        assert find_catalog_road("ex", "없는고속도로") is None

    def test_direction_label(self):
        assert direction_label("up") == "상행"
        assert direction_label("DOWN") == "하행"
        assert direction_label("하행") == "하행"
        assert direction_label(None) == "전체"
        assert direction_label("부산방향") == "부산방향"


class TestDescribeEvent:
    """돌발 정규화 테스트."""

    def test_canonical_name_and_urgent(self):
        data = describe_event(
            TrafficEvent.model_validate({"type": "고속도로", "eventType": "교통사고", "roadName": "경부선"})
        )
        assert data["road_name"] == "경부고속도로"
        assert data["road_key"] == "ex:경부고속도로"
        assert data["route_no"] == "1"
        assert data["urgent"] is True

    def test_construction_not_urgent(self):
        assert event("경부선", kind="공사")["urgent"] is False


class TestSummarizeRoads:
    """노선 요약 테스트."""

    def test_groups_aliases_and_computes_stats(self):
        links = [
            link("경부선", 100),
            link("경부고속도로", 50, link_no="2"),
            link("경부고속도로", 20, direction="하행", link_no="3"),
            link("경부고속도로", 0, link_no="4"),  # 측정값 없음
        ]
        roads = summarize_roads(links, road_type="ex")
        gyeongbu = next(r for r in roads if r["key"] == "ex:경부고속도로")
        assert gyeongbu["link_count"] == 4
        assert gyeongbu["measured_count"] == 3
        assert gyeongbu["avg_speed"] == round(170 / 3, 1)
        assert gyeongbu["min_speed"] == 20
        assert gyeongbu["grade_share"] == {"smooth": 33.3, "slow": 33.3, "jam": 33.3}
        # 정체 비율 30% 이상이면 정체
        assert gyeongbu["grade"] == "jam"
        assert gyeongbu["updated_at"] == "2026-09-23T17:45:00"

    def test_catalog_roads_included_and_sorted(self):
        roads = summarize_roads([], road_type="ex")
        assert roads[0]["name"] == "경부고속도로"
        numbers = [int(r["route_no"]) for r in roads]
        assert numbers == sorted(numbers)
        assert all(r["grade"] == "unknown" for r in roads)
        assert summarize_roads([], road_type="ex", include_catalog=False) == []

    def test_unknown_roads_sorted_after_catalog(self):
        roads = summarize_roads([link("세종대로", 30, road_type="its")], road_type="its")
        assert roads[-1]["name"] == "세종대로"
        assert roads[-1]["in_catalog"] is False

    def test_event_counts_and_event_only_roads(self):
        events = [
            event("경부선"),
            event("경부고속도로", kind="공사"),
            event("새고속도로", road_type="고속도로"),
        ]
        roads = summarize_roads([], events, road_type="ex")
        gyeongbu = next(r for r in roads if r["key"] == "ex:경부고속도로")
        assert gyeongbu["event_count"] == 2
        assert gyeongbu["urgent_count"] == 1
        assert any(r["key"] == "ex:새고속도로" and r["event_count"] == 1 for r in roads)

    def test_road_type_filter(self):
        links = [link("경부고속도로", 90), link("국도1호선", 50, road_type="its")]
        assert all(r["road_type"] == "its" for r in summarize_roads(links, road_type="its"))
        both = summarize_roads(links, include_catalog=False)
        assert {r["road_type"] for r in both} == {"ex", "its"}


class TestRoadDetail:
    """노선 상세 테스트."""

    def test_directions_and_slowest(self):
        links = [link("경부고속도로", speed, direction=d, link_no=str(i))
                 for i, (speed, d) in enumerate([(90, "상행"), (30, "상행"), (60, "상행"), (80, "하행")])]
        detail = road_detail("ex:경부고속도로", links, slowest_limit=2)
        assert detail["route_no"] == "1"
        assert len(detail["path"]) >= 2
        up = next(d for d in detail["directions"] if d["direction"] == "상행")
        assert up["link_count"] == 3
        assert [s["speed"] for s in up["slowest"]] == [30, 60]
        assert up["slowest"][0]["grade"] == "jam"

    def test_events_sorted_urgent_then_recent(self):
        events = [
            event("경부선", kind="공사", start="20260923175000", x=127.1),
            event("경부선", kind="교통사고", start="20260923160000", x=127.2),
            event("경부선", kind="교통사고", start="20260923170000", x=127.3),
        ]
        detail = road_detail("ex:경부고속도로", [], events)
        assert [e["kind"] for e in detail["events"]] == ["acc", "acc", "cor"]
        assert detail["events"][0]["started_at"] == "2026-09-23T17:00:00"

    def test_not_found(self):
        assert road_detail("ex:없는도로", []) is None
        assert road_detail("invalid", []) is None
        assert road_detail("xx:경부고속도로", []) is None

    def test_catalog_road_without_data(self):
        detail = road_detail("its:국도7호선", [])
        assert detail["directions"] == []
        assert detail["avg_speed"] is None
