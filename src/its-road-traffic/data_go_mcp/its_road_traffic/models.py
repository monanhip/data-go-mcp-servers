"""Data models for ITS Road Traffic (국가교통정보센터 교통소통·돌발상황 정보)."""

import hashlib
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


# 대한민국 전역 좌표 범위 (경도 X, 위도 Y). ITS API는 조회 영역이 필수다.
KOREA_BBOX = {"minX": 124.0, "maxX": 132.0, "minY": 33.0, "maxY": 39.0}


class RoadType(str, Enum):
    """도로 유형 (ITS API `type` 파라미터)."""

    ALL = "all"
    EXPRESSWAY = "ex"  # 고속도로
    NATIONAL = "its"  # 국도

    @property
    def label(self) -> str:
        """한글 표기."""
        return {"all": "전체", "ex": "고속도로", "its": "국도"}[self.value]


class EventKind(str, Enum):
    """돌발상황 유형 (ITS API `eventType` 파라미터 코드)."""

    ALL = "all"
    CONSTRUCTION = "cor"  # 공사
    ACCIDENT = "acc"  # 교통사고
    WEATHER = "wea"  # 기상
    INCIDENT = "ete"  # 기타돌발
    DISASTER = "dis"  # 재난
    ETC = "etc"  # 기타

    @property
    def label(self) -> str:
        """한글 표기."""
        return EVENT_KIND_LABELS[self.value]


EVENT_KIND_LABELS = {
    "all": "전체",
    "cor": "공사",
    "acc": "교통사고",
    "wea": "기상",
    "ete": "기타돌발",
    "dis": "재난",
    "etc": "기타",
}

# 응답의 eventType은 한글로 오기도, 코드로 오기도 한다.
_EVENT_KIND_ALIASES = {
    "공사": "cor",
    "교통사고": "acc",
    "사고": "acc",
    "기상": "wea",
    "기타돌발": "ete",
    "돌발": "ete",
    "재난": "dis",
    "기타": "etc",
}

# 기본 알림 대상: 사고·돌발·재난
DEFAULT_ALERT_KINDS = ("acc", "ete", "dis")


def normalize_event_kind(raw: Optional[str]) -> str:
    """응답의 eventType 값을 코드(acc, cor, ...)로 정규화."""
    if not raw:
        return "etc"
    value = str(raw).strip()
    lowered = value.lower()
    if lowered in EVENT_KIND_LABELS and lowered != "all":
        return lowered
    if value in _EVENT_KIND_ALIASES:
        return _EVENT_KIND_ALIASES[value]
    for alias, code in _EVENT_KIND_ALIASES.items():
        if alias in value:
            return code
    return "etc"


def normalize_road_type(raw: Optional[str], road_name: str = "") -> str:
    """응답의 도로 유형 값을 ``ex``/``its``로 정규화."""
    value = (raw or "").strip().lower()
    if value in ("ex", "its"):
        return value
    if "고속" in value:
        return "ex"
    if "국도" in value:
        return "its"
    return "ex" if "고속" in road_name else "its"


class CongestionGrade(str, Enum):
    """소통 등급."""

    SMOOTH = "smooth"  # 원활
    SLOW = "slow"  # 서행
    JAM = "jam"  # 정체
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """한글 표기."""
        return {"smooth": "원활", "slow": "서행", "jam": "정체", "unknown": "정보없음"}[self.value]


# 도로 유형별 소통 등급 기준 속도(km/h): (원활 하한, 서행 하한)
GRADE_THRESHOLDS = {"ex": (70.0, 40.0), "its": (40.0, 20.0)}


def congestion_grade(speed: Optional[float], road_type: str) -> CongestionGrade:
    """속도로 소통 등급을 판정."""
    if speed is None or speed <= 0:
        return CongestionGrade.UNKNOWN
    smooth, slow = GRADE_THRESHOLDS.get(road_type, GRADE_THRESHOLDS["its"])
    if speed >= smooth:
        return CongestionGrade.SMOOTH
    if speed >= slow:
        return CongestionGrade.SLOW
    return CongestionGrade.JAM


def parse_its_datetime(value: Optional[str]) -> Optional[datetime]:
    """ITS 일시 문자열(YYYYMMDDHHMMSS 등)을 datetime으로 변환."""
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    for fmt, length in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12), ("%Y%m%d", 8)):
        if len(digits) >= length:
            try:
                return datetime.strptime(digits[:length], fmt)
            except ValueError:
                return None
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TrafficLink(BaseModel):
    """표준링크 단위 소통정보 (trafficInfo 응답 item)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    road_name: str = Field(default="", alias="roadName", description="도로명")
    road_drc_type: Optional[str] = Field(None, alias="roadDrcType", description="도로 방향")
    link_no: Optional[str] = Field(None, alias="linkNo", description="링크 순번")
    link_id: str = Field(default="", alias="linkId", description="링크 ID")
    start_node_id: Optional[str] = Field(None, alias="startNodeId", description="시작 노드 ID")
    end_node_id: Optional[str] = Field(None, alias="endNodeId", description="종료 노드 ID")
    speed: Optional[float] = Field(None, description="평균 속도(km/h)")
    travel_time: Optional[float] = Field(None, alias="travelTime", description="통행시간(초)")
    created_date: Optional[str] = Field(None, alias="createdDate", description="생성일시")
    road_type: str = Field(default="its", description="도로 유형 (ex: 고속도로, its: 국도)")

    @field_validator("speed", "travel_time", mode="before")
    @classmethod
    def _parse_number(cls, value: Any) -> Optional[float]:
        return _to_float(value)

    @field_validator("road_name", "link_id", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("road_drc_type", "link_no", "start_node_id", "end_node_id", mode="before")
    @classmethod
    def _to_str(cls, value: Any) -> Optional[str]:
        return None if value is None else str(value).strip()

    @property
    def has_measurement(self) -> bool:
        """속도·통행시간이 모두 0이면 측정값이 없는 링크다."""
        return bool(self.speed and self.speed > 0)

    @property
    def grade(self) -> CongestionGrade:
        """소통 등급."""
        return congestion_grade(self.speed, self.road_type)


class TrafficEvent(BaseModel):
    """돌발상황 정보 (eventInfo 응답 item)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    type: Optional[str] = Field(None, description="도로 유형 원문")
    event_type: Optional[str] = Field(None, alias="eventType", description="돌발 유형 원문")
    event_detail_type: Optional[str] = Field(
        None, alias="eventDetailType", description="돌발 세부 유형"
    )
    start_date: Optional[str] = Field(None, alias="startDate", description="발생일시")
    end_date: Optional[str] = Field(None, alias="endDate", description="종료 예정일시")
    coord_x: Optional[float] = Field(None, alias="coordX", description="경도")
    coord_y: Optional[float] = Field(None, alias="coordY", description="위도")
    link_id: Optional[str] = Field(None, alias="linkId", description="링크 ID")
    road_name: str = Field(default="", alias="roadName", description="도로명")
    road_no: Optional[str] = Field(None, alias="roadNo", description="노선 번호")
    road_drc_type: Optional[str] = Field(None, alias="roadDrcType", description="도로 방향")
    lanes_block_type: Optional[str] = Field(
        None, alias="lanesBlockType", description="차단 통제 유형"
    )
    lanes_blocked: Optional[str] = Field(None, alias="lanesBlocked", description="차단 차로")
    message: str = Field(default="", description="돌발 내용")

    @field_validator("coord_x", "coord_y", mode="before")
    @classmethod
    def _parse_coord(cls, value: Any) -> Optional[float]:
        return _to_float(value)

    @field_validator(
        "type",
        "event_type",
        "event_detail_type",
        "start_date",
        "end_date",
        "link_id",
        "road_no",
        "road_drc_type",
        "lanes_block_type",
        "lanes_blocked",
        mode="before",
    )
    @classmethod
    def _to_str(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("road_name", "message", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> str:
        return "" if value is None else str(value).strip()

    @property
    def kind(self) -> str:
        """정규화된 돌발 유형 코드."""
        return normalize_event_kind(self.event_type)

    @property
    def road_type(self) -> str:
        """정규화된 도로 유형 (ex/its)."""
        return normalize_road_type(self.type, self.road_name)

    @property
    def event_id(self) -> str:
        """돌발 식별자. API가 ID를 주지 않으므로 주요 필드로 만든다."""
        x = f"{self.coord_x:.4f}" if self.coord_x is not None else ""
        y = f"{self.coord_y:.4f}" if self.coord_y is not None else ""
        raw = "|".join(
            [
                self.kind,
                self.event_detail_type or "",
                self.road_name,
                self.road_drc_type or "",
                self.link_id or "",
                self.start_date or "",
                x,
                y,
            ]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """API/화면용 딕셔너리."""
        started = parse_its_datetime(self.start_date)
        ends = parse_its_datetime(self.end_date)
        return {
            "id": self.event_id,
            "kind": self.kind,
            "kind_label": EVENT_KIND_LABELS[self.kind],
            "detail": self.event_detail_type or "",
            "road_type": self.road_type,
            "road_name": self.road_name,
            "road_no": self.road_no,
            "road_key": road_key(self.road_type, self.road_name),
            "direction": self.road_drc_type,
            "lanes_blocked": self.lanes_blocked,
            "lanes_block_type": self.lanes_block_type,
            "message": self.message,
            "lat": self.coord_y,
            "lon": self.coord_x,
            "started_at": started.isoformat() if started else None,
            "ends_at": ends.isoformat() if ends else None,
        }


class TrafficResponse(BaseModel):
    """소통정보 응답."""

    items: List[TrafficLink] = Field(default_factory=list)
    total_count: int = 0


class EventResponse(BaseModel):
    """돌발상황 응답."""

    items: List[TrafficEvent] = Field(default_factory=list)
    total_count: int = 0


class CctvType(str, Enum):
    """CCTV 영상 형식 (ITS API `cctvType` 파라미터)."""

    STREAM = "1"  # 실시간 스트리밍 (HLS)
    VIDEO = "2"  # 동영상 파일
    IMAGE = "3"  # 정지 영상
    STREAM_HTTPS = "4"  # 실시간 스트리밍 (HLS, HTTPS)
    VIDEO_HTTPS = "5"  # 동영상 파일 (HTTPS)


_CCTV_FIELDS = {
    "cctvname": "name",
    "cctvurl": "url",
    "coordx": "coord_x",
    "coordy": "coord_y",
    "cctvformat": "format",
    "cctvtype": "cctv_type",
    "cctvresolution": "resolution",
    "roadsectionid": "road_section_id",
    "filecreatetime": "file_create_time",
}


def cctv_media_type(url: str, fmt: Optional[str]) -> str:
    """재생 방식: ``hls``(m3u8), ``image``(정지영상), ``video``(mp4 등)."""
    fmt_l = (fmt or "").lower()
    path = urlparse(url or "").path.lower()
    if "hls" in fmt_l or "m3u8" in fmt_l or path.endswith(".m3u8"):
        return "hls"
    if any(t in fmt_l for t in ("jpg", "jpeg", "png", "img", "image")) or path.endswith(
        (".jpg", ".jpeg", ".png", ".svg")
    ):
        return "image"
    if "mp4" in fmt_l or path.endswith(".mp4"):
        return "video"
    # 형식을 알 수 없으면 실시간 스트리밍(HLS)으로 본다
    return "hls"


class CctvCamera(BaseModel):
    """CCTV 정보 (cctvInfo 응답 item). 응답 키가 소문자(cctvname)로 오므로 대소문자를 무시한다."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="", description="CCTV 명칭 (예: [경부선] 양재)")
    url: str = Field(default="", description="영상 URL")
    coord_x: Optional[float] = Field(None, description="경도")
    coord_y: Optional[float] = Field(None, description="위도")
    format: Optional[str] = Field(None, description="영상 형식 (HLS, MP4, JPEG ...)")
    cctv_type: Optional[str] = Field(None, description="CCTV 유형 코드")
    resolution: Optional[str] = Field(None, description="해상도")
    road_section_id: Optional[str] = Field(None, description="도로 구간 ID")
    file_create_time: Optional[str] = Field(None, description="파일 생성 시각")
    road_type: str = Field(default="ex", description="도로 유형 (ex/its)")

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out: Dict[str, Any] = {}
        for key, value in data.items():
            out[_CCTV_FIELDS.get(str(key).lower(), key)] = value
        return out

    @field_validator("coord_x", "coord_y", mode="before")
    @classmethod
    def _parse_coord(cls, value: Any) -> Optional[float]:
        return _to_float(value)

    @field_validator("name", "url", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> str:
        return "" if value is None else str(value).strip()

    @field_validator(
        "format", "cctv_type", "resolution", "road_section_id", "file_create_time", mode="before"
    )
    @classmethod
    def _to_str(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @property
    def cctv_id(self) -> str:
        """CCTV 식별자. 영상 URL은 인증 토큰이 바뀌므로 이름·좌표로 만든다."""
        x = f"{self.coord_x:.5f}" if self.coord_x is not None else ""
        y = f"{self.coord_y:.5f}" if self.coord_y is not None else ""
        raw = "|".join([self.road_type, self.name, x, y])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def media(self) -> str:
        """재생 방식."""
        return cctv_media_type(self.url, self.format)

    def to_dict(self) -> Dict[str, Any]:
        """API/화면용 딕셔너리."""
        return {
            "id": self.cctv_id,
            "name": self.name,
            "url": self.url,
            "media": self.media,
            "format": self.format,
            "resolution": self.resolution,
            "road_type": self.road_type,
            "lat": self.coord_y,
            "lon": self.coord_x,
        }


class CctvResponse(BaseModel):
    """CCTV 응답."""

    items: List[CctvCamera] = Field(default_factory=list)
    total_count: int = 0


def road_key(road_type: str, road_name: str) -> str:
    """노선 식별 키 (예: ``ex:경부고속도로``)."""
    return f"{road_type}:{road_name}"
