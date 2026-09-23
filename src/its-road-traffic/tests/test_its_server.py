"""Tests for ITS Road Traffic MCP server tools."""

import pytest

from data_go_mcp.its_road_traffic import server
from data_go_mcp.its_road_traffic.demo import DemoTrafficSource
from data_go_mcp.its_road_traffic.service import TrafficService


@pytest.fixture(autouse=True)
def demo_service(monkeypatch):
    service = TrafficService(DemoTrafficSource(seed=11, new_event_probability=0.0), demo=True)
    monkeypatch.setattr(server, "_service", service)
    return service


class TestListRoads:
    """list_roads 도구 테스트."""

    async def test_expressways(self):
        result = await server.list_roads("ex")
        assert result["total_count"] == len(result["roads"]) > 0
        first = result["roads"][0]
        assert first["name"] == "경부고속도로"
        assert first["avg_speed_kmh"] is not None
        assert set(first["grade_share_percent"]) == {"smooth", "slow", "jam"}

    async def test_korean_alias_and_congested_filter(self):
        result = await server.list_roads("국도", only_congested=True)
        assert all(r["road_type"] == "its" for r in result["roads"])
        assert all(r["grade"] in ("서행", "정체") for r in result["roads"])

    async def test_invalid_type(self):
        assert "error" in await server.list_roads("bad")


class TestGetRoadTraffic:
    """get_road_traffic 도구 테스트."""

    async def test_by_name_partial_and_number(self):
        for query in ("경부고속도로", "경부", "1"):
            result = await server.get_road_traffic(query)
            assert result["name"] == "경부고속도로", query
            assert result["directions"]

    async def test_national_road(self):
        result = await server.get_road_traffic("국도3호선", road_type="its")
        assert result["name"] == "국도3호선"

    async def test_not_found(self):
        result = await server.get_road_traffic("없는도로")
        assert "error" in result
        assert "경부고속도로" in result["available_roads"]

    async def test_invalid_type(self):
        assert "error" in await server.get_road_traffic("경부", road_type="all")


class TestGetTrafficEvents:
    """get_traffic_events 도구 테스트."""

    async def test_all(self):
        result = await server.get_traffic_events()
        assert result["total_count"] == len(result["events"]) > 0
        assert sum(result["counts_by_kind"].values()) == result["total_count"]

    async def test_kind_and_road_filters(self, demo_service):
        demo_service.source.add_event("acc", road_key="ex:영동고속도로")
        result = await server.get_traffic_events("acc,ete", road_type="ex", road_name="영동")
        assert result["events"]
        assert all(e["kind"] in ("acc", "ete") for e in result["events"])
        assert all("영동" in e["road_name"] for e in result["events"])

    async def test_limit(self):
        result = await server.get_traffic_events(limit=1)
        assert len(result["events"]) == 1

    async def test_invalid_kind(self):
        result = await server.get_traffic_events("zzz")
        assert "Invalid event_kind" in result["error"]

    async def test_upstream_error(self, demo_service):
        async def boom(**kwargs):
            raise RuntimeError("upstream failed")

        demo_service.source.get_events = boom
        result = await server.get_traffic_events()
        assert result == {"error": "upstream failed", "events": []}


class TestServiceFactory:
    """서비스 생성 테스트."""

    def test_demo_mode_env(self, monkeypatch):
        monkeypatch.setattr(server, "_service", None)
        monkeypatch.setenv("ITS_DEMO_MODE", "1")
        service = server.get_service()
        assert service.demo is True
        assert server.get_service() is service

    def test_requires_key_without_demo(self, monkeypatch):
        monkeypatch.setattr(server, "_service", None)
        monkeypatch.delenv("ITS_DEMO_MODE", raising=False)
        monkeypatch.delenv("ITS_API_KEY", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)
        with pytest.raises(ValueError):
            server.get_service()
