"""데모 데이터 소스.

ITS OpenAPI는 국내 IP·별도 인증키가 필요하므로, 키 없이도 화면과 알림을 확인할 수 있도록
카탈로그 노선을 따라 그럴듯한 소통·돌발 데이터를 만들어낸다. 응답 형식은 실제 API와 같다.
"""

import math
import random
import time
from .models import (
    EVENT_KIND_LABELS,
    CctvCamera,
    CctvResponse,
    EventKind,
    EventResponse,
    RoadType,
    TrafficEvent,
    TrafficLink,
    TrafficResponse,
)
from .roads import CATALOG, RoadInfo, path_length_km, point_on_path  # noqa: F401
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape


_DIRECTIONS = ("상행", "하행")

_MESSAGES = {
    "acc": [
        ("추돌사고", "{road} {dir} {km}km 지점 차량 {n}대 추돌사고, {lanes}차로 차단"),
        ("단독사고", "{road} {dir} {km}km 지점 차량 단독사고 처리 중, {lanes}차로 통제"),
        ("차량화재", "{road} {dir} {km}km 지점 차량 화재 발생, {lanes}차로 통제"),
    ],
    "ete": [
        ("낙하물", "{road} {dir} {km}km 지점 낙하물 처리 작업 중, {lanes}차로 부분 통제"),
        ("고장차량", "{road} {dir} {km}km 지점 고장차량 정차, 서행 바랍니다"),
        ("보행자", "{road} {dir} {km}km 지점 보행자 진입, 주의 운전 바랍니다"),
    ],
    "cor": [
        ("도로보수", "{road} {dir} {km}km 도로 보수공사, {lanes}차로 부분 차단"),
        ("포장공사", "{road} {dir} {km}km 포장 공사, 야간 차로 통제"),
    ],
    "wea": [
        ("안개", "{road} {dir} {km}km 부근 안개로 시정 불량, 감속 운행 바랍니다"),
        ("강풍", "{road} {dir} {km}km 부근 강풍 주의"),
    ],
    "dis": [
        ("침수", "{road} {dir} {km}km 부근 도로 침수 우려, 전면 통제"),
    ],
}

# 새 돌발이 생길 때의 유형 가중치
_NEW_EVENT_WEIGHTS = {"acc": 5, "ete": 4, "cor": 1, "wea": 1, "dis": 0.3}

_TYPE_LABEL = {"ex": "고속도로", "its": "국도"}


def _rush_factor(now: datetime) -> float:
    """출퇴근 시간대 속도 감소율."""
    hour = now.hour + now.minute / 60.0
    morning = math.exp(-(((hour - 8.0) / 1.2) ** 2))
    evening = math.exp(-(((hour - 18.5) / 1.5) ** 2))
    weekend = 0.5 if now.weekday() >= 5 else 1.0
    return 1.0 - 0.45 * max(morning, evening) * weekend


class DemoTrafficSource:
    """실제 API 클라이언트와 같은 인터페이스를 갖는 데모 데이터 소스."""

    def __init__(
        self,
        seed: Optional[int] = None,
        new_event_probability: float = 0.35,
        clock=time.time,
    ):
        """데모 데이터 소스 초기화.

        Args:
            seed: 난수 시드 (테스트용)
            new_event_probability: 돌발 조회 때마다 새 돌발이 생길 확률
            clock: 현재 시각(epoch 초)을 돌려주는 함수 (테스트용)
        """
        self.random = random.Random(seed)
        self.new_event_probability = new_event_probability
        self.clock = clock
        self._counter = 0
        self._events: List[Dict] = []
        # 노선별 상습 정체 지점
        self._hotspots: Dict[str, List[float]] = {
            road.key: [self.random.random() for _ in range(self.random.randint(1, 2))]
            for road in CATALOG
        }
        for kind in ("cor", "cor", "acc", "ete", "wea", "acc"):
            self._events.append(self._make_event(kind, age_minutes=self.random.randint(5, 90)))

    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료."""
        await self.close()

    async def close(self) -> None:
        """정리할 자원이 없다."""

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock())

    def _make_event(
        self,
        kind: str,
        age_minutes: int = 0,
        road: Optional[RoadInfo] = None,
        direction: Optional[str] = None,
    ) -> Dict:
        rng = self.random
        if road is None:
            # 사고·돌발은 고속도로에서 더 자주 생기게
            pool = [r for r in CATALOG if r.road_type == "ex"] * 2 + [
                r for r in CATALOG if r.road_type == "its"
            ]
            road = rng.choice(pool)
        direction = direction or rng.choice(_DIRECTIONS)
        fraction = rng.uniform(0.05, 0.95)
        lat, lon = point_on_path(road.path, fraction)
        detail, template = rng.choice(_MESSAGES[kind])
        km = round(path_length_km(road.path) * fraction, 1)
        duration = {"acc": 60, "ete": 30, "cor": 600, "wea": 180, "dis": 240}[kind]
        # 처음부터 끝난 돌발이 만들어지지 않도록 지속시간 안으로 제한
        started = self._now() - timedelta(minutes=min(age_minutes, int(duration * 0.7)))
        self._counter += 1
        return {
            "type": _TYPE_LABEL[road.road_type],
            "eventType": EVENT_KIND_LABELS[kind],
            "eventDetailType": detail,
            "startDate": started.strftime("%Y%m%d%H%M%S"),
            "endDate": (started + timedelta(minutes=duration)).strftime("%Y%m%d%H%M%S"),
            "coordX": round(lon, 6),
            "coordY": round(lat, 6),
            "linkId": f"DEMO{self._counter:06d}",
            "roadName": road.name,
            "roadNo": road.route_no,
            "roadDrcType": direction,
            "lanesBlockType": "1" if kind in ("acc", "ete", "cor") else "0",
            "lanesBlocked": str(rng.randint(1, 2)) if kind != "wea" else "",
            "message": template.format(
                road=road.name, dir=direction, km=km, n=rng.randint(2, 4), lanes=rng.randint(1, 2)
            ),
            "_fraction": fraction,
            "_road_key": road.key,
        }

    def add_event(
        self,
        kind: str = "acc",
        road_key: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> TrafficEvent:
        """돌발을 즉시 추가 (알림 테스트용)."""
        road = next((r for r in CATALOG if r.key == road_key), None) if road_key else None
        raw = self._make_event(kind, road=road, direction=direction)
        self._events.append(raw)
        return TrafficEvent.model_validate(raw)

    def _advance_events(self) -> None:
        now = self._now()
        self._events = [
            event
            for event in self._events
            if datetime.strptime(event["endDate"], "%Y%m%d%H%M%S") > now
        ]
        if self.random.random() < self.new_event_probability:
            kinds, weights = zip(*_NEW_EVENT_WEIGHTS.items())
            self._events.append(self._make_event(self.random.choices(kinds, weights)[0]))

    async def get_events(
        self,
        road_type: RoadType = RoadType.ALL,
        event_kind: EventKind = EventKind.ALL,
        bbox: Optional[Dict[str, float]] = None,
    ) -> EventResponse:
        """돌발상황 (데모)."""
        road_type = RoadType(road_type)
        event_kind = EventKind(event_kind)
        self._advance_events()
        items = []
        for raw in self._events:
            event = TrafficEvent.model_validate(raw)
            if road_type != RoadType.ALL and event.road_type != road_type.value:
                continue
            if event_kind != EventKind.ALL and event.kind != event_kind.value:
                continue
            items.append(event)
        return EventResponse(items=items, total_count=len(items))

    def _blocking_events(self) -> Dict[Tuple[str, str], List[float]]:
        blocking: Dict[Tuple[str, str], List[float]] = {}
        for raw in self._events:
            if raw["eventType"] in ("교통사고", "기타돌발", "재난"):
                blocking.setdefault((raw["_road_key"], raw["roadDrcType"]), []).append(
                    raw["_fraction"]
                )
        return blocking

    async def get_traffic_info(
        self,
        road_type: RoadType = RoadType.EXPRESSWAY,
        direction: str = "all",
        bbox: Optional[Dict[str, float]] = None,
    ) -> TrafficResponse:
        """링크 소통정보 (데모)."""
        road_type = RoadType(road_type)
        now = self._now()
        rush = _rush_factor(now)
        bucket = int(self.clock() // 300)  # 5분마다 값이 바뀐다
        blocking = self._blocking_events()
        created = now.strftime("%Y%m%d%H%M%S")
        items = []
        for road in CATALOG:
            if road_type != RoadType.ALL and road.road_type != road_type.value:
                continue
            free_speed = 100.0 if road.road_type == "ex" else 65.0
            count = max(8, int(path_length_km(road.path) / 12))
            for d_index, drc in enumerate(_DIRECTIONS):
                for i in range(count):
                    fraction = (i + 0.5) / count
                    noise = random.Random(f"{road.key}|{drc}|{i}|{bucket}").uniform(-8, 8)
                    speed = free_speed * rush + noise
                    for spot in self._hotspots[road.key]:
                        # 상습 정체 지점 근처는 출퇴근 시간에 크게 느려진다
                        speed *= 1.0 - (1.0 - rush) * 1.4 * math.exp(
                            -(((fraction - spot) / 0.06) ** 2)
                        )
                    for spot in blocking.get((road.key, drc), []):
                        # 사고 지점 뒤(상류)로 정체가 생긴다
                        distance = fraction - spot if d_index == 0 else spot - fraction
                        if -0.02 <= distance <= 0.12:
                            speed *= 0.25 + distance * 3
                    speed = max(3.0, round(speed, 1))
                    link_km = path_length_km(road.path) / count
                    items.append(
                        TrafficLink(
                            roadName=road.name,
                            roadDrcType=drc,
                            linkNo=str(i + 1),
                            linkId=f"{road.road_type.upper()}{road.route_no.zfill(3)}{d_index}{i:04d}",
                            speed=speed,
                            travelTime=round(link_km / speed * 3600, 0),
                            createdDate=created,
                            road_type=road.road_type,
                        )
                    )
        return TrafficResponse(items=items, total_count=len(items))

    # ------------------------------------------------------------ CCTV
    def _build_cameras(self) -> Dict[str, Dict]:
        cameras: Dict[str, Dict] = {}
        for road in CATALOG:
            label = road.aliases[0] if road.road_type == "ex" and road.aliases else road.name
            length = path_length_km(road.path)
            count = max(3, int(length / 25))
            for i in range(count):
                fraction = (i + 0.5) / count
                lat, lon = point_on_path(road.path, fraction)
                slug = f"{road.road_type}-{road.route_no}-{i}"
                cameras[slug] = {
                    "cctvname": f"[{label}] {round(length * fraction)}km 지점",
                    "cctvurl": f"api/demo/cctv/{slug}.svg",
                    "coordx": round(lon, 6),
                    "coordy": round(lat, 6),
                    "cctvformat": "IMAGE",
                    "cctvtype": 3,
                    "cctvresolution": "640x360",
                    "_road_type": road.road_type,
                }
        # 노선을 알 수 없는 CCTV (대괄호 없는 이름)
        for i, (name, lat, lon) in enumerate(
            [("세종대로 광화문", 37.5716, 126.9769), ("부산 서면교차로", 35.1577, 129.0597)]
        ):
            cameras[f"its-x-{i}"] = {
                "cctvname": name,
                "cctvurl": f"api/demo/cctv/its-x-{i}.svg",
                "coordx": lon,
                "coordy": lat,
                "cctvformat": "IMAGE",
                "_road_type": "its",
            }
        return cameras

    @property
    def cameras(self) -> Dict[str, Dict]:
        """데모 CCTV (slug → 원본)."""
        if not hasattr(self, "_cameras"):
            self._cameras = self._build_cameras()
        return self._cameras

    async def get_cctv(
        self,
        road_type: RoadType = RoadType.EXPRESSWAY,
        cctv_type: Optional[str] = None,
        bbox: Optional[Dict[str, float]] = None,
    ) -> CctvResponse:
        """CCTV 목록 (데모). 영상은 :func:`render_cctv_svg`로 만든 정지영상이다."""
        road_type = RoadType(road_type)
        items = []
        for raw in self.cameras.values():
            if road_type != RoadType.ALL and raw["_road_type"] != road_type.value:
                continue
            camera = CctvCamera.model_validate(raw)
            camera.road_type = raw["_road_type"]
            items.append(camera)
        return CctvResponse(items=items, total_count=len(items))

    def render_cctv(self, slug: str) -> Optional[str]:
        """데모 CCTV 화면 SVG. 없는 slug면 None."""
        raw = self.cameras.get(slug)
        if raw is None:
            return None
        return render_cctv_svg(raw["cctvname"], self._now(), seed=slug)


def render_cctv_svg(name: str, now: datetime, seed: str = "") -> str:
    """도로 CCTV처럼 보이는 데모 화면 (SVG). 5초마다 차량 위치가 바뀐다."""
    rng = random.Random(f"{seed}|{int(now.timestamp() // 5)}")
    cars = []
    for lane in range(4):
        for _ in range(rng.randint(1, 4)):
            depth = rng.uniform(0.05, 1.0)  # 0: 멀리, 1: 가까이
            y = 150 + depth * 200
            half = 40 + depth * 280
            lane_w = 2 * half / 4
            x = 320 - half + lane_w * (lane + 0.5) + rng.uniform(-6, 6) * depth
            w, h = 14 + 48 * depth, 9 + 28 * depth
            color = rng.choice(["#e2e8f0", "#94a3b8", "#1e293b", "#dc2626", "#2563eb", "#f8fafc"])
            cars.append(
                f'<rect x="{x - w / 2:.1f}" y="{y - h:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'rx="{3 * depth + 1:.1f}" fill="{color}" stroke="#0f172a" stroke-width="1"/>'
            )
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360" width="640" height="360">
<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#475569"/><stop offset="1" stop-color="#94a3b8"/></linearGradient></defs>
<rect width="640" height="150" fill="url(#sky)"/>
<rect y="140" width="640" height="220" fill="#3f6212"/>
<polygon points="280,150 360,150 640,360 0,360" fill="#334155"/>
<g stroke="#facc15" stroke-width="3" stroke-dasharray="18 16"><line x1="300" y1="150" x2="160" y2="360"/><line x1="320" y1="150" x2="320" y2="360"/><line x1="340" y1="150" x2="480" y2="360"/></g>
{"".join(cars)}
<rect x="0" y="0" width="640" height="30" fill="#000" opacity=".55"/>
<text x="10" y="21" font-family="sans-serif" font-size="16" fill="#fff">{escape(name)}</text>
<text x="630" y="21" font-family="monospace" font-size="15" fill="#fff" text-anchor="end">{stamp}</text>
<text x="630" y="350" font-family="sans-serif" font-size="13" fill="#fde047" text-anchor="end">DEMO</text>
</svg>"""
