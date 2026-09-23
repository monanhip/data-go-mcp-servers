"""Tests for the web dashboard API and Web Push notifier."""

import json
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from data_go_mcp.its_road_traffic import push as push_module
from data_go_mcp.its_road_traffic import web
from data_go_mcp.its_road_traffic.demo import DemoTrafficSource
from data_go_mcp.its_road_traffic.push import WebPushNotifier, notification_payload
from data_go_mcp.its_road_traffic.service import TrafficService


SUBSCRIPTION = {
    "endpoint": "https://push.example.com/abc",
    "keys": {"p256dh": "BPubKey", "auth": "authsecret"},
}


class DisabledNotifier:
    """pywebpush가 없는 환경을 흉내낸 알림기."""

    enabled = False
    public_key = None
    subscription_count = 0

    def subscribe(self, subscription, filters):
        raise AssertionError("should not be called")

    def unsubscribe(self, endpoint):
        return False


@pytest.fixture
def demo_service():
    return TrafficService(
        DemoTrafficSource(seed=7, new_event_probability=0.0), event_interval=3600, demo=True
    )


@pytest.fixture
def client(demo_service):
    app = web.create_app(demo_service, notifier=DisabledNotifier(), start_polling=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def notifier(tmp_path):
    if not push_module.PUSH_AVAILABLE:
        pytest.skip("pywebpush not installed")
    return WebPushNotifier(data_dir=tmp_path)


class TestPages:
    """정적 페이지 테스트."""

    def test_index_and_assets(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "전국 도로 소통정보" in res.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/app.css").status_code == 200
        sw = client.get("/sw.js")
        assert sw.status_code == 200
        assert "javascript" in sw.headers["content-type"]
        manifest = client.get("/manifest.webmanifest")
        assert manifest.json()["short_name"] == "도로소통"


class TestApi:
    """REST API 테스트."""

    def test_status(self, client):
        data = client.get("/api/status").json()
        assert data["demo"] is True
        assert data["push"]["enabled"] is False
        assert data["event_kinds"]["acc"] == "교통사고"

    def test_roads(self, client):
        data = client.get("/api/roads?type=ex").json()
        assert data["road_type"] == "ex"
        assert data["items"][0]["name"] == "경부고속도로"
        assert data["items"][0]["avg_speed"] is not None
        its = client.get("/api/roads?type=its").json()
        assert all(r["road_type"] == "its" for r in its["items"])

    def test_roads_invalid_type(self, client):
        assert client.get("/api/roads?type=xx").status_code == 400

    def test_road_detail(self, client):
        res = client.get(f"/api/roads/{quote('ex:경부고속도로')}")
        assert res.status_code == 200
        detail = res.json()
        assert detail["name"] == "경부고속도로"
        assert {d["direction"] for d in detail["directions"]} == {"상행", "하행"}
        assert client.get(f"/api/roads/{quote('ex:없는도로')}").status_code == 404

    def test_events_filters(self, client):
        all_events = client.get("/api/events").json()["items"]
        assert all_events
        cor = client.get("/api/events?kinds=cor").json()["items"]
        assert all(e["kind"] == "cor" for e in cor)
        ex = client.get("/api/events?type=ex").json()["items"]
        assert all(e["road_type"] == "ex" for e in ex)

    def test_demo_incident_creates_alert(self, client):
        client.get("/api/events")  # 기준선
        res = client.post("/api/demo/incident", json={"kind": "acc", "road_key": "ex:영동고속도로"})
        assert res.status_code == 200
        new_events = res.json()["new_events"]
        assert [e["road_key"] for e in new_events] == ["ex:영동고속도로"]
        alerts = client.get("/api/alerts").json()["items"]
        assert alerts[0]["id"] == new_events[0]["id"]

    def test_demo_incident_invalid_kind(self, client):
        assert client.post("/api/demo/incident", json={"kind": "zzz"}).status_code == 400

    def test_demo_incident_forbidden_outside_demo(self, demo_service):
        demo_service.demo = False
        app = web.create_app(demo_service, notifier=DisabledNotifier(), start_polling=False)
        with TestClient(app) as c:
            assert c.post("/api/demo/incident", json={}).status_code == 403

    def test_upstream_error_returns_502(self, demo_service):
        async def boom(**kwargs):
            raise RuntimeError("ITS down")

        demo_service.source.get_traffic_info = boom
        app = web.create_app(demo_service, notifier=DisabledNotifier(), start_polling=False)
        with TestClient(app) as c:
            res = c.get("/api/roads?type=ex")
            assert res.status_code == 502
            assert "ITS down" in res.json()["error"]

    def test_push_disabled(self, client):
        assert client.get("/api/push/config").json() == {"enabled": False, "public_key": None}
        res = client.post("/api/push/subscribe", json={"subscription": SUBSCRIPTION})
        assert res.status_code == 503
        assert client.post("/api/push/unsubscribe", json={}).status_code == 400

    def test_stream_route_registered(self, client):
        paths = {route.path for route in client.app.routes}
        assert "/api/stream" in paths


class TestBuildService:
    """환경변수 설정 테스트."""

    def test_demo_when_no_key(self, monkeypatch):
        monkeypatch.delenv("ITS_API_KEY", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("ITS_DEMO_MODE", raising=False)
        monkeypatch.setattr(web, "load_dotenv", lambda: None)
        service = web.build_service()
        assert service.demo is True

    def test_real_client_with_key(self, monkeypatch):
        monkeypatch.setenv("ITS_API_KEY", "key")
        monkeypatch.setenv("ITS_EVENT_POLL_SECONDS", "240")
        monkeypatch.delenv("ITS_TRAFFIC_TTL_SECONDS", raising=False)
        monkeypatch.delenv("ITS_DEMO_MODE", raising=False)
        monkeypatch.setattr(web, "load_dotenv", lambda: None)
        service = web.build_service()
        assert service.demo is False
        assert service.event_interval == 240
        assert service.traffic_ttl == 600


class TestWebPush:
    """Web Push 알림기 테스트."""

    EVENT = {
        "id": "abc123",
        "kind": "acc",
        "kind_label": "교통사고",
        "road_type": "ex",
        "road_key": "ex:경부고속도로",
        "road_name": "경부고속도로",
        "direction_label": "상행",
        "message": "추돌사고",
        "urgent": True,
    }

    def test_payload(self):
        payload = notification_payload(self.EVENT)
        assert payload["title"] == "[교통사고] 경부고속도로 상행"
        assert payload["body"] == "추돌사고"
        assert payload["tag"] == "its-abc123"
        assert "road=ex:경부고속도로" in payload["url"]

    def test_keys_are_generated_and_reused(self, tmp_path, notifier):
        assert notifier.enabled
        assert notifier.public_key and len(notifier.public_key) > 80
        again = WebPushNotifier(data_dir=tmp_path)
        assert again.public_key == notifier.public_key

    def test_subscribe_persists(self, tmp_path, notifier):
        notifier.subscribe(SUBSCRIPTION, {"kinds": ["acc"]})
        stored = json.loads((tmp_path / "push_subscriptions.json").read_text(encoding="utf-8"))
        assert stored[SUBSCRIPTION["endpoint"]]["filters"] == {"kinds": ["acc"]}
        assert WebPushNotifier(data_dir=tmp_path).subscription_count == 1
        assert notifier.unsubscribe(SUBSCRIPTION["endpoint"])
        assert not notifier.unsubscribe(SUBSCRIPTION["endpoint"])

    def test_invalid_subscription(self, notifier):
        with pytest.raises(ValueError):
            notifier.subscribe({"endpoint": "x"}, {})
        with pytest.raises(ValueError):
            notifier.subscribe({}, {})

    async def test_sends_only_matching_and_drops_expired(self, notifier, monkeypatch):
        other = {**SUBSCRIPTION, "endpoint": "https://push.example.com/other"}
        expired = {**SUBSCRIPTION, "endpoint": "https://push.example.com/gone"}
        notifier.subscribe(SUBSCRIPTION, {"kinds": ["acc"]})
        notifier.subscribe(other, {"kinds": ["cor"]})
        notifier.subscribe(expired, {})

        sent = []

        def fake_webpush(subscription_info, data, **kwargs):
            sent.append(subscription_info["endpoint"])
            if subscription_info["endpoint"].endswith("/gone"):
                raise push_module.WebPushException("gone", response=MagicMock(status_code=410))

        monkeypatch.setattr(push_module, "webpush", fake_webpush)
        await notifier(self.EVENT)
        assert sorted(sent) == sorted([SUBSCRIPTION["endpoint"], expired["endpoint"]])
        assert notifier.subscription_count == 2  # 만료된 구독 제거

    def test_push_endpoints(self, demo_service, notifier):
        app = web.create_app(demo_service, notifier=notifier, start_polling=False)
        with TestClient(app) as c:
            config = c.get("/api/push/config").json()
            assert config["enabled"] and config["public_key"] == notifier.public_key
            ok = c.post("/api/push/subscribe", json={"subscription": SUBSCRIPTION, "filters": {"kinds": ["acc"]}})
            assert ok.json() == {"ok": True}
            assert c.post("/api/push/subscribe", json={"subscription": {}}).status_code == 400
            assert c.post("/api/push/subscribe", content="not json").status_code == 400
            assert c.post("/api/push/unsubscribe", json={"endpoint": SUBSCRIPTION["endpoint"]}).json() == {"ok": True}
