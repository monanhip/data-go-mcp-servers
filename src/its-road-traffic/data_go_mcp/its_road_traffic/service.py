"""소통·돌발 데이터 캐시, 돌발 폴링, 신규 돌발 감지 및 알림 전파."""

import asyncio
import logging
import time
from .models import EventKind, EventResponse, RoadType, TrafficLink, TrafficResponse
from .roads import describe_cctv, describe_event, group_cctv, road_detail, summarize_roads
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Protocol, Set
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


class TrafficSource(Protocol):
    """소통·돌발 데이터 소스 (실제 API 클라이언트 또는 데모)."""

    async def get_traffic_info(self, road_type: RoadType = ...) -> TrafficResponse:  # noqa: D102
        ...

    async def get_events(  # noqa: D102
        self, road_type: RoadType = ..., event_kind: EventKind = ...
    ) -> EventResponse: ...

    async def close(self) -> None:  # noqa: D102
        ...


Notifier = Callable[[Dict[str, Any]], Awaitable[None]]


def event_matches(event: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> bool:
    """알림 필터 조건에 맞는 돌발인지 확인.

    filters:
        kinds: 알림 받을 돌발 유형 코드 목록 (비어 있으면 전체)
        road_types: 도로 유형 목록 (ex, its; 비어 있으면 전체)
        roads: 관심 노선 키 목록 (비어 있으면 전체)
    """
    if not filters:
        return True
    kinds = filters.get("kinds") or []
    road_types = filters.get("road_types") or []
    roads = filters.get("roads") or []
    if kinds and event.get("kind") not in kinds:
        return False
    if road_types and event.get("road_type") not in road_types:
        return False
    if roads and event.get("road_key") not in roads:
        return False
    return True


class TrafficService:
    """데이터 소스 앞단의 캐시 + 돌발 감시자."""

    def __init__(
        self,
        source: TrafficSource,
        event_interval: float = 180.0,
        traffic_ttl: float = 600.0,
        demo: bool = False,
        clock: Callable[[], float] = time.monotonic,
        seen_ttl: float = 24 * 3600.0,
        cctv_ttl: float = 1800.0,
    ):
        """서비스 초기화.

        Args:
            source: 데이터 소스
            event_interval: 돌발정보 폴링 주기(초)
            traffic_ttl: 소통정보 캐시 유효시간(초)
            demo: 데모 모드 여부
            clock: 단조 시계 (테스트용)
            seen_ttl: 사라진 돌발을 기억하는 시간(초). 같은 돌발이 다시 잡혀도 중복 알림하지 않는다.
            cctv_ttl: CCTV 목록 캐시 유효시간(초). 영상 URL 토큰이 만료되기 전에 갱신되도록 잡는다.
        """
        self.source = source
        self.event_interval = event_interval
        self.traffic_ttl = traffic_ttl
        self.demo = demo
        self.clock = clock
        self.seen_ttl = seen_ttl
        self.cctv_ttl = cctv_ttl

        self._links: Dict[str, List[TrafficLink]] = {}
        self._links_at: Dict[str, float] = {}
        self._links_updated: Dict[str, str] = {}
        self._traffic_locks: Dict[str, asyncio.Lock] = {}
        self._events: List[Dict[str, Any]] = []
        self._events_updated: Optional[str] = None
        self._events_at: Optional[float] = None
        self._event_lock = asyncio.Lock()
        self._seen: Dict[str, float] = {}
        self._baseline_done = False
        self._recent_alerts: List[Dict[str, Any]] = []
        self._cctv: Dict[str, List[Dict[str, Any]]] = {}
        self._cctv_at: Dict[str, float] = {}
        self._cctv_updated: Dict[str, str] = {}
        self._cctv_locks: Dict[str, asyncio.Lock] = {}
        self._cctv_hosts: Set[str] = set()

        self._subscribers: Set[asyncio.Queue] = set()
        self._notifiers: List[Notifier] = []
        self._poll_task: Optional[asyncio.Task] = None
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ 구독
    def subscribe(self) -> asyncio.Queue:
        """실시간 메시지 구독 큐를 만든다."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """구독 해제."""
        self._subscribers.discard(queue)

    def add_notifier(self, notifier: Notifier) -> None:
        """신규 돌발 발생 시 호출할 알림기(Web Push 등)를 등록."""
        self._notifiers.append(notifier)

    def _broadcast(self, message: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # 느린 클라이언트는 가장 오래된 메시지를 버린다
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    # ---------------------------------------------------------------- 소통정보
    async def get_links(self, road_type: str) -> List[TrafficLink]:
        """도로 유형별 링크 소통정보 (캐시)."""
        road_type = RoadType(road_type).value
        if road_type == "all":
            return await self.get_links("ex") + await self.get_links("its")
        lock = self._traffic_locks.setdefault(road_type, asyncio.Lock())
        async with lock:
            fetched_at = self._links_at.get(road_type)
            if fetched_at is not None and self.clock() - fetched_at < self.traffic_ttl:
                return self._links[road_type]
            try:
                response = await self.source.get_traffic_info(road_type=RoadType(road_type))
            except Exception as e:
                self.last_error = f"trafficInfo({road_type}): {e}"
                logger.warning("Failed to fetch traffic info: %s", e)
                if road_type in self._links:
                    # 직전 캐시가 있으면 그대로 보여준다
                    return self._links[road_type]
                raise
            self._links[road_type] = response.items
            self._links_at[road_type] = self.clock()
            self._links_updated[road_type] = datetime.now().isoformat(timespec="seconds")
            return response.items

    # ---------------------------------------------------------------- 돌발정보
    async def refresh_events(self) -> List[Dict[str, Any]]:
        """돌발정보를 새로 받아 신규 돌발을 감지하고 알린다.

        Returns:
            새로 감지된 돌발 목록 (첫 조회는 기준선이므로 빈 목록)
        """
        async with self._event_lock:
            try:
                response = await self.source.get_events(
                    road_type=RoadType.ALL, event_kind=EventKind.ALL
                )
            except Exception as e:
                self.last_error = f"eventInfo: {e}"
                logger.warning("Failed to fetch events: %s", e)
                raise
            now = self.clock()
            events = [describe_event(item) for item in response.items]
            new_events = [event for event in events if event["id"] not in self._seen]
            for event in events:
                self._seen[event["id"]] = now
            self._seen = {
                key: seen_at
                for key, seen_at in self._seen.items()
                if now - seen_at < self.seen_ttl
            }

            events.sort(key=lambda e: e.get("started_at") or "", reverse=True)
            self._events = events
            self._events_at = now
            self._events_updated = datetime.now().isoformat(timespec="seconds")

            if not self._baseline_done:
                # 서버 시작 시점에 이미 진행 중인 돌발은 알리지 않는다
                self._baseline_done = True
                new_events = []

        for event in new_events:
            self._recent_alerts.insert(0, event)
            self._broadcast({"type": "incident", "event": event})
            for notifier in self._notifiers:
                try:
                    await notifier(event)
                except Exception as e:  # 알림 실패가 폴링을 멈추게 하지 않는다
                    logger.warning("Notifier failed: %s", e)
        del self._recent_alerts[50:]
        self._broadcast(
            {"type": "update", "events_updated": self._events_updated, "count": len(self._events)}
        )
        self.last_error = None
        return new_events

    async def get_events(self) -> List[Dict[str, Any]]:
        """현재 진행 중인 돌발 목록 (폴링이 없으면 주기 단위로 새로 조회)."""
        stale = self._events_at is None or self.clock() - self._events_at >= self.event_interval
        if stale and (self._poll_task is None or self._poll_task.done()):
            await self.refresh_events()
        return self._events

    async def filtered_events(
        self,
        road_type: Optional[str] = None,
        kinds: Optional[Iterable[str]] = None,
        road_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """조건에 맞는 돌발 목록."""
        filters = {
            "road_types": [road_type] if road_type and road_type != "all" else [],
            "kinds": list(kinds or []),
            "roads": [road_key] if road_key else [],
        }
        return [event for event in await self.get_events() if event_matches(event, filters)]

    @property
    def recent_alerts(self) -> List[Dict[str, Any]]:
        """최근 감지된 신규 돌발 (최신순)."""
        return list(self._recent_alerts)

    # ------------------------------------------------------------------ CCTV
    @property
    def supports_cctv(self) -> bool:
        """데이터 소스가 CCTV를 제공하는지 여부."""
        return hasattr(self.source, "get_cctv")

    async def get_cctv(self, road_type: str) -> List[Dict[str, Any]]:
        """도로 유형별 CCTV 목록 (캐시)."""
        road_type = RoadType(road_type).value
        if road_type == "all":
            return await self.get_cctv("ex") + await self.get_cctv("its")
        if not self.supports_cctv:
            return []
        lock = self._cctv_locks.setdefault(road_type, asyncio.Lock())
        async with lock:
            fetched_at = self._cctv_at.get(road_type)
            if fetched_at is not None and self.clock() - fetched_at < self.cctv_ttl:
                return self._cctv[road_type]
            try:
                response = await self.source.get_cctv(road_type=RoadType(road_type))
            except Exception as e:
                self.last_error = f"cctvInfo({road_type}): {e}"
                logger.warning("Failed to fetch CCTV: %s", e)
                if road_type in self._cctv:
                    return self._cctv[road_type]
                raise
            cameras = [describe_cctv(item) for item in response.items]
            for camera in cameras:
                host = urlparse(camera["url"]).hostname
                if host:
                    self._cctv_hosts.add(host.lower())
            self._cctv[road_type] = cameras
            self._cctv_at[road_type] = self.clock()
            self._cctv_updated[road_type] = datetime.now().isoformat(timespec="seconds")
            return cameras

    async def cctv_groups(
        self, road_type: str, query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """노선별로 묶은 CCTV 목록. query로 노선명·지점명을 거른다."""
        cameras = await self.get_cctv(road_type)
        if query:
            q = query.replace(" ", "").lower()
            cameras = [
                c
                for c in cameras
                if q in c["name"].replace(" ", "").lower()
                or q in c["road_name"].replace(" ", "").lower()
                or q == (c.get("route_no") or "")
            ]
        return group_cctv(cameras)

    def is_allowed_stream(self, url: str) -> bool:
        """프록시 허용 여부: CCTV 목록에 나온 호스트의 http(s) URL만 허용한다."""
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in (
            self._cctv_hosts
        )

    def allow_stream_host(self, host: str) -> None:
        """리다이렉트 등으로 확인된 CCTV 영상 호스트를 허용 목록에 추가."""
        self._cctv_hosts.add(host.lower())

    # ------------------------------------------------------------------ 노선
    async def roads(self, road_type: str) -> List[Dict[str, Any]]:
        """노선 목록 요약."""
        links = await self.get_links(road_type)
        events = await self.get_events()
        return summarize_roads(links, events, road_type=None if road_type == "all" else road_type)

    async def road(self, key: str) -> Optional[Dict[str, Any]]:
        """노선 상세."""
        road_type = key.partition(":")[0]
        if road_type not in ("ex", "its"):
            return None
        links = await self.get_links(road_type)
        events = await self.get_events()
        detail = road_detail(key, links, events)
        if detail is not None:
            try:
                cameras = await self.get_cctv(road_type)
            except Exception:
                cameras = []  # CCTV 조회 실패가 노선 상세를 막지 않는다
            detail["cctv"] = next(
                (group["items"] for group in group_cctv(cameras) if group["key"] == key), []
            )
        return detail

    # ---------------------------------------------------------------- 폴링
    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.refresh_events()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # last_error에 기록됨. 다음 주기에 재시도
            await asyncio.sleep(self.event_interval)

    def start(self) -> None:
        """돌발 폴링 시작."""
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """폴링 중지 및 데이터 소스 정리."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        await self.source.close()

    def status(self) -> Dict[str, Any]:
        """상태 정보."""
        return {
            "demo": self.demo,
            "polling": self._poll_task is not None and not self._poll_task.done(),
            "event_interval": self.event_interval,
            "traffic_ttl": self.traffic_ttl,
            "events_updated": self._events_updated,
            "traffic_updated": dict(self._links_updated),
            "cctv_updated": dict(self._cctv_updated),
            "cctv_ttl": self.cctv_ttl,
            "event_count": len(self._events),
            "subscribers": len(self._subscribers),
            "last_error": self.last_error,
        }
