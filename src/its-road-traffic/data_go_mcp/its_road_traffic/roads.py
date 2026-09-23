"""노선 카탈로그와 링크 단위 소통정보 → 노선 단위 요약 집계."""

import re
from .models import (
    CongestionGrade,
    TrafficEvent,
    TrafficLink,
    congestion_grade,
    parse_its_datetime,
    road_key,
)
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple


# 알림 대상이 되는 긴급 돌발 유형
URGENT_KINDS = frozenset({"acc", "ete", "dis"})


@dataclass(frozen=True)
class RoadInfo:
    """카탈로그 노선 정보."""

    road_type: str
    name: str
    route_no: str
    aliases: Tuple[str, ...] = ()
    # 대략적인 노선 경로 (위도, 경도). 데모 데이터 생성과 지도 이동에 쓴다.
    path: Tuple[Tuple[float, float], ...] = field(default=(), compare=False)

    @property
    def key(self) -> str:
        """노선 키."""
        return road_key(self.road_type, self.name)


# 주요 고속도로
EXPRESSWAYS: Tuple[RoadInfo, ...] = (
    RoadInfo(
        "ex",
        "경부고속도로",
        "1",
        ("경부선",),
        (
            (37.47, 127.03),
            (36.80, 127.15),
            (36.35, 127.43),
            (36.12, 128.10),
            (35.90, 128.60),
            (35.26, 129.09),
        ),
    ),
    RoadInfo(
        "ex",
        "남해고속도로",
        "10",
        ("남해선", "남해제1지선", "남해제2지선"),
        ((34.80, 126.60), (34.97, 127.50), (35.18, 128.10), (35.25, 128.62), (35.20, 129.00)),
    ),
    RoadInfo(
        "ex",
        "광주대구고속도로",
        "12",
        ("광주대구선", "88올림픽고속도로"),
        ((35.20, 126.90), (35.32, 127.00), (35.52, 127.72), (35.85, 128.45)),
    ),
    RoadInfo(
        "ex",
        "서해안고속도로",
        "15",
        ("서해안선",),
        ((37.44, 126.89), (36.95, 126.87), (36.78, 126.47), (35.95, 126.80), (34.85, 126.45)),
    ),
    RoadInfo(
        "ex",
        "호남고속도로",
        "25",
        ("호남선", "논산천안고속도로", "논산천안선"),
        ((36.80, 127.15), (36.20, 127.10), (35.85, 127.10), (35.20, 126.85), (34.95, 127.50)),
    ),
    RoadInfo(
        "ex",
        "당진영덕고속도로",
        "30",
        ("당진영덕선", "당진대전고속도로", "상주영천고속도로"),
        ((36.90, 126.65), (36.45, 127.10), (36.60, 127.45), (36.40, 128.15), (36.40, 129.35)),
    ),
    RoadInfo(
        "ex",
        "중부고속도로",
        "35",
        ("중부선", "통영대전고속도로", "통영대전선", "대전통영고속도로"),
        ((37.53, 127.20), (37.25, 127.45), (36.85, 127.45), (36.40, 127.45), (34.85, 128.43)),
    ),
    RoadInfo(
        "ex",
        "평택제천고속도로",
        "40",
        ("평택제천선",),
        ((36.98, 126.85), (37.00, 127.25), (37.00, 127.85), (37.13, 128.18)),
    ),
    RoadInfo(
        "ex",
        "중부내륙고속도로",
        "45",
        ("중부내륙선",),
        ((37.50, 127.50), (37.30, 127.63), (36.97, 127.85), (36.40, 128.15), (35.25, 128.55)),
    ),
    RoadInfo(
        "ex",
        "영동고속도로",
        "50",
        ("영동선",),
        (
            (37.45, 126.70),
            (37.27, 127.00),
            (37.25, 127.20),
            (37.30, 127.63),
            (37.35, 127.93),
            (37.75, 128.85),
        ),
    ),
    RoadInfo(
        "ex",
        "중앙고속도로",
        "55",
        ("중앙선",),
        ((37.85, 127.73), (37.35, 127.93), (37.10, 128.20), (36.57, 128.70), (35.95, 128.55)),
    ),
    RoadInfo(
        "ex",
        "서울양양고속도로",
        "60",
        ("서울양양선", "서울춘천고속도로"),
        ((37.60, 127.17), (37.85, 127.73), (37.95, 128.20), (38.05, 128.62)),
    ),
    RoadInfo(
        "ex",
        "동해고속도로",
        "65",
        ("동해선",),
        ((35.20, 129.15), (35.55, 129.25), (36.00, 129.35), (37.40, 129.15), (38.20, 128.58)),
    ),
    RoadInfo(
        "ex",
        "수도권제1순환고속도로",
        "100",
        ("수도권제1순환선", "서울외곽순환고속도로", "서울외곽순환선"),
        (
            (37.40, 126.95),
            (37.45, 127.12),
            (37.60, 127.20),
            (37.70, 127.05),
            (37.65, 126.85),
            (37.50, 126.80),
            (37.40, 126.95),
        ),
    ),
    RoadInfo(
        "ex",
        "경인고속도로",
        "120",
        ("경인선",),
        ((37.48, 126.64), (37.50, 126.76), (37.51, 126.87)),
    ),
)

# 주요 일반국도
NATIONAL_ROADS: Tuple[RoadInfo, ...] = (
    RoadInfo(
        "its",
        "국도1호선",
        "1",
        (),
        (
            (34.80, 126.40),
            (35.15, 126.85),
            (35.82, 127.12),
            (36.35, 127.38),
            (36.80, 127.12),
            (37.28, 127.00),
            (37.57, 126.98),
            (37.75, 126.78),
        ),
    ),
    RoadInfo(
        "its",
        "국도2호선",
        "2",
        (),
        ((34.80, 126.40), (34.95, 127.50), (35.18, 128.10), (35.10, 129.03)),
    ),
    RoadInfo(
        "its",
        "국도3호선",
        "3",
        (),
        (
            (35.00, 128.05),
            (35.18, 128.08),
            (36.12, 128.10),
            (36.97, 127.93),
            (37.28, 127.44),
            (37.60, 127.13),
            (37.90, 127.06),
        ),
    ),
    RoadInfo(
        "its",
        "국도4호선",
        "4",
        (),
        ((36.00, 126.71), (36.35, 127.38), (36.12, 128.10), (35.87, 128.60), (35.84, 129.21)),
    ),
    RoadInfo(
        "its",
        "국도5호선",
        "5",
        (),
        (
            (35.23, 128.68),
            (35.87, 128.60),
            (36.57, 128.73),
            (37.14, 128.21),
            (37.34, 127.92),
            (37.88, 127.73),
            (38.10, 127.70),
        ),
    ),
    RoadInfo(
        "its",
        "국도6호선",
        "6",
        (),
        (
            (37.57, 126.98),
            (37.52, 127.30),
            (37.49, 127.49),
            (37.49, 127.98),
            (37.37, 128.40),
            (37.75, 128.87),
        ),
    ),
    RoadInfo(
        "its",
        "국도7호선",
        "7",
        (),
        (
            (35.10, 129.03),
            (35.55, 129.32),
            (36.03, 129.37),
            (36.80, 129.42),
            (37.45, 129.16),
            (38.20, 128.59),
        ),
    ),
    RoadInfo(
        "its",
        "국도17호선",
        "17",
        (),
        (
            (34.75, 127.66),
            (35.20, 127.45),
            (35.82, 127.15),
            (36.35, 127.38),
            (36.63, 127.49),
            (36.98, 127.93),
            (37.24, 127.21),
        ),
    ),
    RoadInfo(
        "its",
        "국도38호선",
        "38",
        (),
        (
            (37.00, 126.60),
            (36.99, 127.09),
            (37.01, 127.28),
            (37.14, 128.21),
            (37.37, 128.76),
            (37.45, 129.16),
        ),
    ),
    RoadInfo(
        "its",
        "국도77호선",
        "77",
        (),
        (
            (37.55, 126.60),
            (36.80, 126.40),
            (35.97, 126.70),
            (34.80, 126.38),
            (34.75, 127.66),
            (35.10, 129.03),
        ),
    ),
)

CATALOG: Tuple[RoadInfo, ...] = EXPRESSWAYS + NATIONAL_ROADS

_SUFFIX_RE = re.compile(r"(고속도로|고속국도|고속|선)$")
_NATIONAL_NO_RE = re.compile(r"(?:일반)?국도\s*(\d+)\s*(?:호선|번|호)?|(\d+)\s*(?:호선|번)\s*국도")


def _normalize(name: str) -> str:
    compact = re.sub(r"\s+", "", name or "")
    return _SUFFIX_RE.sub("", compact)


_EX_INDEX: Dict[str, RoadInfo] = {}
_EX_BY_NO: Dict[str, RoadInfo] = {}
for _road in EXPRESSWAYS:
    _EX_BY_NO[_road.route_no] = _road
    for _alias in (_road.name,) + _road.aliases:
        _EX_INDEX[_normalize(_alias)] = _road
_NATIONAL_BY_NO: Dict[str, RoadInfo] = {road.route_no: road for road in NATIONAL_ROADS}


def find_catalog_road(
    road_type: str, road_name: str, road_no: Optional[str] = None
) -> Optional[RoadInfo]:
    """도로명(또는 노선번호)에 해당하는 카탈로그 노선을 찾는다."""
    if road_type == "ex":
        found = _EX_INDEX.get(_normalize(road_name))
        if found is None and road_no:
            found = _EX_BY_NO.get(str(road_no).lstrip("0"))
        return found
    match = _NATIONAL_NO_RE.search(re.sub(r"\s+", "", road_name or ""))
    if match:
        return _NATIONAL_BY_NO.get(match.group(1) or match.group(2)) or RoadInfo(
            "its", f"국도{match.group(1) or match.group(2)}호선", match.group(1) or match.group(2)
        )
    if road_no and str(road_no).isdigit():
        return _NATIONAL_BY_NO.get(str(int(road_no)))
    return None


def canonical_road(
    road_type: str, road_name: str, road_no: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """표시용 노선명과 노선번호."""
    found = find_catalog_road(road_type, road_name, road_no)
    if found:
        return found.name, found.route_no
    return (road_name or "이름 미상"), road_no


DIRECTION_LABELS = {
    "up": "상행",
    "down": "하행",
    "start": "기점 방향",
    "end": "종점 방향",
    "상행": "상행",
    "하행": "하행",
}


def direction_label(raw: Optional[str]) -> str:
    """방향 표기."""
    if not raw:
        return "전체"
    return DIRECTION_LABELS.get(raw.strip().lower(), raw.strip())


def describe_event(event: TrafficEvent) -> Dict:
    """돌발 정보를 노선 정규화까지 적용한 딕셔너리로 변환."""
    data = event.to_dict()
    name, route_no = canonical_road(event.road_type, event.road_name, event.road_no)
    data["road_name"] = name
    data["route_no"] = route_no
    data["road_key"] = road_key(event.road_type, name)
    data["direction_label"] = direction_label(event.road_drc_type)
    data["urgent"] = event.kind in URGENT_KINDS
    return data


def _grade_share(links: List[TrafficLink]) -> Dict[str, float]:
    counts = {"smooth": 0, "slow": 0, "jam": 0}
    for link in links:
        grade = link.grade.value
        if grade in counts:
            counts[grade] += 1
    total = sum(counts.values())
    if not total:
        return dict.fromkeys(counts, 0.0)
    return {key: round(value * 100.0 / total, 1) for key, value in counts.items()}


def _speed_stats(links: List[TrafficLink], road_type: str) -> Dict:
    measured = [link for link in links if link.has_measurement]
    avg = round(sum(link.speed for link in measured) / len(measured), 1) if measured else None
    grade = congestion_grade(avg, road_type)
    share = _grade_share(measured)
    # 정체 구간이 30% 이상이면 평균 속도와 상관없이 정체로 본다.
    if share["jam"] >= 30.0:
        grade = CongestionGrade.JAM
    return {
        "link_count": len(links),
        "measured_count": len(measured),
        "avg_speed": avg,
        "min_speed": round(min(link.speed for link in measured), 1) if measured else None,
        "grade": grade.value,
        "grade_label": grade.label,
        "grade_share": share,
    }


def _latest(links: Iterable[TrafficLink]) -> Optional[str]:
    latest: Optional[datetime] = None
    for link in links:
        created = parse_its_datetime(link.created_date)
        if created and (latest is None or created > latest):
            latest = created
    return latest.isoformat() if latest else None


def _group_links(links: Iterable[TrafficLink]) -> Dict[Tuple[str, str], List[TrafficLink]]:
    groups: Dict[Tuple[str, str], List[TrafficLink]] = {}
    for link in links:
        name, _ = canonical_road(link.road_type, link.road_name)
        groups.setdefault((link.road_type, name), []).append(link)
    return groups


def _event_counts(events: Iterable[Dict]) -> Dict[str, Dict[str, int]]:
    counts: Dict[str, Dict[str, int]] = {}
    for event in events:
        bucket = counts.setdefault(event["road_key"], {"events": 0, "urgent": 0})
        bucket["events"] += 1
        if event.get("urgent"):
            bucket["urgent"] += 1
    return counts


def _sort_key(summary: Dict) -> Tuple:
    route_no = summary.get("route_no")
    in_catalog = summary.get("in_catalog")
    number = int(route_no) if route_no and str(route_no).isdigit() else 10**6
    return (0 if in_catalog else 1, number, summary["name"])


def summarize_roads(
    links: Iterable[TrafficLink],
    events: Iterable[Dict] = (),
    road_type: Optional[str] = None,
    include_catalog: bool = True,
) -> List[Dict]:
    """링크 소통정보와 돌발 정보를 노선 단위로 요약.

    Args:
        links: 링크 소통정보
        events: :func:`describe_event` 결과 목록
        road_type: ``ex``/``its``로 거르기 (None이면 전체)
        include_catalog: 소통정보가 없어도 카탈로그 노선을 목록에 포함할지 여부

    Returns:
        노선 요약 목록 (카탈로그 노선 → 노선번호 → 이름 순)
    """
    events = list(events)
    event_counts = _event_counts(events)
    groups = _group_links(link for link in links if not road_type or link.road_type == road_type)

    summaries: Dict[str, Dict] = {}
    for (rtype, name), group in groups.items():
        catalog = find_catalog_road(rtype, name)
        key = road_key(rtype, name)
        summaries[key] = {
            "key": key,
            "road_type": rtype,
            "name": name,
            "route_no": catalog.route_no if catalog else None,
            "in_catalog": catalog is not None and catalog in CATALOG,
            "updated_at": _latest(group),
            **_speed_stats(group, rtype),
        }

    if include_catalog:
        for road in CATALOG:
            if road_type and road.road_type != road_type:
                continue
            summaries.setdefault(
                road.key,
                {
                    "key": road.key,
                    "road_type": road.road_type,
                    "name": road.name,
                    "route_no": road.route_no,
                    "in_catalog": True,
                    "updated_at": None,
                    **_speed_stats([], road.road_type),
                },
            )

    # 소통정보에는 없지만 돌발이 있는 노선도 목록에 보이게 한다.
    for event in events:
        if road_type and event["road_type"] != road_type:
            continue
        if event["road_key"] not in summaries:
            summaries[event["road_key"]] = {
                "key": event["road_key"],
                "road_type": event["road_type"],
                "name": event["road_name"],
                "route_no": event.get("route_no"),
                "in_catalog": False,
                "updated_at": None,
                **_speed_stats([], event["road_type"]),
            }

    for key, summary in summaries.items():
        counts = event_counts.get(key, {"events": 0, "urgent": 0})
        summary["event_count"] = counts["events"]
        summary["urgent_count"] = counts["urgent"]

    return sorted(summaries.values(), key=_sort_key)


def road_detail(
    key: str,
    links: Iterable[TrafficLink],
    events: Iterable[Dict] = (),
    slowest_limit: int = 5,
) -> Optional[Dict]:
    """노선 상세: 방향별 소통 요약, 저속 구간, 돌발 목록."""
    road_type, _, name = key.partition(":")
    if road_type not in ("ex", "its") or not name:
        return None

    group = [
        link
        for link in links
        if link.road_type == road_type and canonical_road(road_type, link.road_name)[0] == name
    ]
    road_events = [event for event in events if event["road_key"] == key]
    catalog = find_catalog_road(road_type, name)
    if not group and not road_events and catalog is None:
        return None

    by_direction: Dict[str, List[TrafficLink]] = {}
    for link in group:
        by_direction.setdefault(direction_label(link.road_drc_type), []).append(link)

    directions = []
    for label, direction_links in sorted(by_direction.items()):
        measured = sorted(
            (link for link in direction_links if link.has_measurement), key=lambda l: l.speed
        )
        directions.append(
            {
                "direction": label,
                **_speed_stats(direction_links, road_type),
                "slowest": [
                    {
                        "link_id": link.link_id,
                        "link_no": link.link_no,
                        "speed": link.speed,
                        "travel_time": link.travel_time,
                        "grade": link.grade.value,
                        "grade_label": link.grade.label,
                    }
                    for link in measured[:slowest_limit]
                ],
            }
        )

    # 긴급(사고·돌발·재난) 먼저, 같은 등급 안에서는 최근 발생 순
    road_events.sort(key=lambda e: e.get("started_at") or "", reverse=True)
    road_events.sort(key=lambda e: not e.get("urgent"))
    return {
        "key": key,
        "road_type": road_type,
        "name": name,
        "route_no": catalog.route_no if catalog else None,
        "path": [list(point) for point in catalog.path] if catalog else [],
        "updated_at": _latest(group),
        **_speed_stats(group, road_type),
        "directions": directions,
        "events": road_events,
    }
