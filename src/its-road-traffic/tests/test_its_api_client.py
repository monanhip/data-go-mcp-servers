"""Tests for ITS Road Traffic API client."""

import json

import httpx
import pytest

from data_go_mcp.its_road_traffic.api_client import ItsApiError, ItsRoadTrafficAPIClient
from data_go_mcp.its_road_traffic.models import EventKind, RoadType


def make_client(handler):
    """MockTransport로 요청을 가로채는 클라이언트."""
    return ItsRoadTrafficAPIClient(api_key="test-key", transport=httpx.MockTransport(handler))


TRAFFIC_PAYLOAD = {
    "header": {"resultCode": 0, "resultMsg": "success", "totalCount": 2},
    "body": {
        "totalCount": 2,
        "items": [
            {
                "roadName": "경부고속도로",
                "roadDrcType": "0",
                "linkNo": "1",
                "linkId": "L1",
                "speed": "92",
                "travelTime": "30",
                "createdDate": "20260923174500",
            },
            {
                "roadName": "경부고속도로",
                "roadDrcType": "1",
                "linkNo": "2",
                "linkId": "L2",
                "speed": "35",
                "travelTime": "80",
                "createdDate": "20260923174500",
            },
        ],
    },
}

EVENT_PAYLOAD = {
    "header": {"resultCode": "0", "resultMsg": "success"},
    "body": {
        "items": {
            "type": "고속도로",
            "eventType": "교통사고",
            "roadName": "영동선",
            "coordX": "127.5",
            "coordY": "37.3",
            "startDate": "20260923170000",
            "message": "사고",
        }
    },
}


class TestItsRoadTrafficAPIClient:
    """API 클라이언트 테스트."""

    def test_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("ITS_API_KEY", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key is required"):
            ItsRoadTrafficAPIClient()

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.setenv("ITS_API_KEY", "env-key")
        assert ItsRoadTrafficAPIClient().api_key == "env-key"

    async def test_get_traffic_info_params_and_parsing(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json=TRAFFIC_PAYLOAD)

        async with make_client(handler) as client:
            result = await client.get_traffic_info(RoadType.EXPRESSWAY)

        assert seen["path"] == "/trafficInfo"
        params = seen["params"]
        assert params["apiKey"] == "test-key"
        assert params["type"] == "ex"
        assert params["drcType"] == "all"
        assert params["getType"] == "json"
        assert {"minX", "maxX", "minY", "maxY"} <= params.keys()
        assert result.total_count == 2
        assert [link.link_id for link in result.items] == ["L1", "L2"]
        assert all(link.road_type == "ex" for link in result.items)
        assert result.items[1].speed == 35.0

    async def test_custom_bbox(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.url.params)
            return httpx.Response(200, json=TRAFFIC_PAYLOAD)

        bbox = {"minX": 126.8, "maxX": 127.2, "minY": 37.4, "maxY": 37.7}
        async with make_client(handler) as client:
            await client.get_traffic_info("its", bbox=bbox)
        assert seen["type"] == "its"
        assert float(seen["minX"]) == 126.8
        assert float(seen["maxY"]) == 37.7

    async def test_get_events_single_item_dict(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.url.params)
            return httpx.Response(200, json=EVENT_PAYLOAD)

        async with make_client(handler) as client:
            result = await client.get_events(RoadType.ALL, EventKind.ACCIDENT)

        assert seen["eventType"] == "acc"
        assert seen["type"] == "all"
        assert len(result.items) == 1
        event = result.items[0]
        assert event.kind == "acc"
        assert event.road_type == "ex"
        assert event.coord_y == 37.3

    async def test_events_fill_road_type_when_missing(self):
        payload = {"header": {"resultCode": 0}, "body": {"items": [{"eventType": "공사", "roadName": "세종로"}]}}

        async with make_client(lambda r: httpx.Response(200, json=payload)) as client:
            result = await client.get_events(RoadType.NATIONAL)
        assert result.items[0].road_type == "its"

    async def test_nested_item_list(self):
        payload = {"header": {"resultCode": "00"}, "body": {"items": {"item": [{"roadName": "A", "speed": 50}]}}}
        async with make_client(lambda r: httpx.Response(200, json=payload)) as client:
            result = await client.get_traffic_info(RoadType.NATIONAL)
        assert result.items[0].road_name == "A"

    async def test_empty_body(self):
        payload = {"header": {"resultCode": 0}, "body": {"items": None}}
        async with make_client(lambda r: httpx.Response(200, json=payload)) as client:
            result = await client.get_events()
        assert result.items == []
        assert result.total_count == 0

    async def test_error_result_code(self):
        payload = {"header": {"resultCode": 30, "resultMsg": "SERVICE KEY IS NOT REGISTERED"}}
        async with make_client(lambda r: httpx.Response(200, json=payload)) as client:
            with pytest.raises(ItsApiError, match="SERVICE KEY IS NOT REGISTERED"):
                await client.get_events()

    async def test_non_json_response(self):
        async with make_client(lambda r: httpx.Response(200, text="<html>error</html>")) as client:
            with pytest.raises(ItsApiError, match="Non-JSON"):
                await client.get_events()

    async def test_http_error(self):
        async with make_client(lambda r: httpx.Response(500, text="oops")) as client:
            with pytest.raises(ItsApiError, match="HTTP error 500"):
                await client.get_traffic_info()

    async def test_connection_error_mentions_domestic_ip(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        async with make_client(handler) as client:
            with pytest.raises(ItsApiError, match="국내 IP"):
                await client.get_events()

    async def test_json_list_payload_is_rejected(self):
        async with make_client(lambda r: httpx.Response(200, content=json.dumps([1, 2]))) as client:
            with pytest.raises(ItsApiError, match="Unexpected response"):
                await client.get_events()
