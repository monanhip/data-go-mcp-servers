"""Tests for TrafficService (cache, polling, new incident detection)."""

import asyncio

import pytest

from data_go_mcp.its_road_traffic.demo import DemoTrafficSource, point_on_path
from data_go_mcp.its_road_traffic.models import (
    EventKind,
    EventResponse,
    RoadType,
    TrafficEvent,
    TrafficLink,
    TrafficResponse,
)
from data_go_mcp.its_road_traffic.service import TrafficService, event_matches


def raw_event(name="경부선", kind="교통사고", start="20260923170000", x=127.0):
    """테스트용 돌발 원본."""
    return {
        "type": "고속도로",
        "eventType": kind,
        "roadName": name,
        "startDate": start,
        "coordX": x,
        "coordY": 36.5,
        "message": "테스트",
    }


class FakeSource:
    """호출 횟수를 세는 가짜 데이터 소스."""

    def __init__(self):
        self.events = []
        self.traffic_calls = 0
        self.event_calls = 0
        self.fail_traffic = False
        self.closed = False

    async def get_traffic_info(self, road_type=RoadType.EXPRESSWAY, **kwargs):
        self.traffic_calls += 1
        if self.fail_traffic:
            raise RuntimeError("network down")
        return TrafficResponse(
            items=[TrafficLink(roadName="경부고속도로", speed=80, road_type=RoadType(road_type).value)]
        )

    async def get_events(self, road_type=RoadType.ALL, event_kind=EventKind.ALL, **kwargs):
        self.event_calls += 1
        return EventResponse(items=[TrafficEvent.model_validate(e) for e in self.events])

    async def close(self):
        self.closed = True


class FakeClock:
    """조작 가능한 단조 시계."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def source():
    return FakeSource()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service(source, clock):
    return TrafficService(source, event_interval=60, traffic_ttl=300, clock=clock)


class TestEventMatches:
    """알림 필터 테스트."""

    EVENT = {"kind": "acc", "road_type": "ex", "road_key": "ex:경부고속도로"}

    def test_empty_filters_match_all(self):
        assert event_matches(self.EVENT, None)
        assert event_matches(self.EVENT, {})
        assert event_matches(self.EVENT, {"kinds": [], "road_types": [], "roads": []})

    def test_each_filter(self):
        assert event_matches(self.EVENT, {"kinds": ["acc", "ete"]})
        assert not event_matches(self.EVENT, {"kinds": ["cor"]})
        assert not event_matches(self.EVENT, {"road_types": ["its"]})
        assert event_matches(self.EVENT, {"roads": ["ex:경부고속도로"]})
        assert not event_matches(self.EVENT, {"roads": ["ex:영동고속도로"]})


class TestNewEventDetection:
    """신규 돌발 감지 테스트."""

    async def test_first_poll_is_baseline(self, service, source):
        source.events = [raw_event()]
        assert await service.refresh_events() == []
        assert len(await service.get_events()) == 1

    async def test_new_event_is_broadcast_and_notified(self, service, source):
        notified = []

        async def notifier(event):
            notified.append(event)

        service.add_notifier(notifier)
        queue = service.subscribe()
        source.events = [raw_event()]
        await service.refresh_events()
        # 기준선 이후에는 update 메시지만
        assert queue.get_nowait()["type"] == "update"

        source.events.append(raw_event(name="영동선", start="20260923171000"))
        new = await service.refresh_events()
        assert [e["road_name"] for e in new] == ["영동고속도로"]
        assert notified == new
        message = queue.get_nowait()
        assert message["type"] == "incident"
        assert message["event"]["road_key"] == "ex:영동고속도로"
        assert queue.get_nowait()["type"] == "update"
        assert service.recent_alerts[0]["road_name"] == "영동고속도로"

    async def test_same_event_is_not_reported_twice(self, service, source, clock):
        source.events = []
        await service.refresh_events()
        source.events = [raw_event()]
        assert len(await service.refresh_events()) == 1
        assert await service.refresh_events() == []
        # 잠시 사라졌다가 다시 잡혀도 중복 알림하지 않는다
        source.events = []
        await service.refresh_events()
        clock.now += 600
        source.events = [raw_event()]
        assert await service.refresh_events() == []

    async def test_seen_ttl_expiry(self, source, clock):
        service = TrafficService(source, clock=clock, seen_ttl=100)
        await service.refresh_events()
        source.events = [raw_event()]
        assert len(await service.refresh_events()) == 1
        source.events = []
        clock.now += 200
        await service.refresh_events()
        source.events = [raw_event()]
        assert len(await service.refresh_events()) == 1

    async def test_failing_notifier_does_not_break_polling(self, service, source):
        async def broken(event):
            raise RuntimeError("push failed")

        service.add_notifier(broken)
        await service.refresh_events()
        source.events = [raw_event()]
        assert len(await service.refresh_events()) == 1

    async def test_full_queue_drops_oldest(self, service, source):
        queue = service.subscribe()
        for _ in range(105):
            await service.refresh_events()
        assert queue.qsize() == 100
        service.unsubscribe(queue)
        assert service.status()["subscribers"] == 0

    async def test_filtered_events(self, service, source):
        source.events = [
            raw_event(),
            raw_event(name="국도1호선", kind="공사", x=126.9) | {"type": "국도"},
        ]
        assert len(await service.filtered_events()) == 2
        assert [e["kind"] for e in await service.filtered_events(road_type="its")] == ["cor"]
        assert [e["kind"] for e in await service.filtered_events(kinds=["acc"])] == ["acc"]
        assert len(await service.filtered_events(road_key="ex:경부고속도로")) == 1


class TestCaching:
    """캐시 테스트."""

    async def test_traffic_cache_ttl(self, service, source, clock):
        await service.get_links("ex")
        await service.get_links("ex")
        assert source.traffic_calls == 1
        clock.now += 301
        await service.get_links("ex")
        assert source.traffic_calls == 2

    async def test_all_fetches_both_types(self, service, source):
        links = await service.get_links("all")
        assert {link.road_type for link in links} == {"ex", "its"}
        assert source.traffic_calls == 2

    async def test_stale_cache_served_on_error(self, service, source, clock):
        await service.get_links("ex")
        source.fail_traffic = True
        clock.now += 301
        links = await service.get_links("ex")
        assert len(links) == 1
        assert "network down" in service.last_error

    async def test_error_without_cache_raises(self, service, source):
        source.fail_traffic = True
        with pytest.raises(RuntimeError):
            await service.get_links("its")

    async def test_events_refetched_after_interval(self, service, source, clock):
        await service.get_events()
        await service.get_events()
        assert source.event_calls == 1
        clock.now += 61
        await service.get_events()
        assert source.event_calls == 2

    async def test_roads_and_road(self, service, source):
        source.events = [raw_event()]
        roads = await service.roads("ex")
        gyeongbu = next(r for r in roads if r["key"] == "ex:경부고속도로")
        assert gyeongbu["avg_speed"] == 80
        assert gyeongbu["event_count"] == 1
        detail = await service.road("ex:경부고속도로")
        assert detail["events"][0]["kind"] == "acc"
        assert await service.road("bad-key") is None


class TestPolling:
    """폴링 테스트."""

    async def test_start_and_stop(self, source):
        service = TrafficService(source, event_interval=0.01)
        service.start()
        await asyncio.sleep(0.05)
        assert service.status()["polling"] is True
        assert source.event_calls >= 2
        await service.stop()
        assert source.closed
        assert service.status()["polling"] is False


class TestDemoSource:
    """데모 데이터 소스 테스트."""

    async def test_demo_traffic_and_events(self):
        demo = DemoTrafficSource(seed=42, new_event_probability=0.0)
        ex = await demo.get_traffic_info(RoadType.EXPRESSWAY)
        its = await demo.get_traffic_info(RoadType.NATIONAL)
        assert ex.items and all(link.road_type == "ex" for link in ex.items)
        assert its.items and all(link.road_type == "its" for link in its.items)
        assert all(link.speed >= 3 for link in ex.items)
        events = await demo.get_events()
        assert events.items
        assert all(33 <= e.coord_y <= 39 and 124 <= e.coord_x <= 132 for e in events.items)

    async def test_add_event_and_filter(self):
        demo = DemoTrafficSource(seed=1, new_event_probability=0.0)
        added = demo.add_event("acc", road_key="ex:경부고속도로")
        assert added.road_name == "경부고속도로"
        accidents = await demo.get_events(event_kind=EventKind.ACCIDENT)
        assert any(e.event_id == added.event_id for e in accidents.items)
        assert all(e.kind == "acc" for e in accidents.items)
        national = await demo.get_events(road_type=RoadType.NATIONAL)
        assert all(e.road_type == "its" for e in national.items)

    async def test_events_expire(self):
        now = [1_800_000_000.0]
        demo = DemoTrafficSource(seed=3, new_event_probability=0.0, clock=lambda: now[0])
        assert (await demo.get_events()).items
        now[0] += 24 * 3600
        assert (await demo.get_events()).items == []

    def test_point_on_path(self):
        path = ((37.0, 127.0), (36.0, 127.0))
        assert point_on_path(path, 0) == (37.0, 127.0)
        assert point_on_path(path, 1) == (36.0, 127.0)
        lat, lon = point_on_path(path, 0.5)
        assert abs(lat - 36.5) < 1e-9 and lon == 127.0
