"""Tests for ITS Road Traffic data models."""

from datetime import datetime

from data_go_mcp.its_road_traffic.models import (
    CongestionGrade,
    RoadType,
    TrafficEvent,
    TrafficLink,
    congestion_grade,
    normalize_event_kind,
    normalize_road_type,
    parse_its_datetime,
    road_key,
)


class TestNormalization:
    """정규화 함수 테스트."""

    def test_event_kind_from_korean_and_codes(self):
        assert normalize_event_kind("교통사고") == "acc"
        assert normalize_event_kind("공사") == "cor"
        assert normalize_event_kind("기타돌발") == "ete"
        assert normalize_event_kind("재난") == "dis"
        assert normalize_event_kind("기상") == "wea"
        assert normalize_event_kind("ACC") == "acc"
        assert normalize_event_kind("ete") == "ete"

    def test_event_kind_partial_and_unknown(self):
        assert normalize_event_kind("차량 교통사고") == "acc"
        assert normalize_event_kind(None) == "etc"
        assert normalize_event_kind("") == "etc"
        assert normalize_event_kind("알수없음") == "etc"
        assert normalize_event_kind("all") == "etc"

    def test_road_type(self):
        assert normalize_road_type("ex") == "ex"
        assert normalize_road_type("its") == "its"
        assert normalize_road_type("고속도로") == "ex"
        assert normalize_road_type("국도") == "its"
        assert normalize_road_type(None, "경부고속도로") == "ex"
        assert normalize_road_type(None, "세종대로") == "its"

    def test_road_type_enum_label(self):
        assert RoadType.EXPRESSWAY.label == "고속도로"
        assert RoadType("its").label == "국도"


class TestCongestionGrade:
    """소통 등급 판정 테스트."""

    def test_expressway_thresholds(self):
        assert congestion_grade(90, "ex") == CongestionGrade.SMOOTH
        assert congestion_grade(70, "ex") == CongestionGrade.SMOOTH
        assert congestion_grade(55, "ex") == CongestionGrade.SLOW
        assert congestion_grade(20, "ex") == CongestionGrade.JAM

    def test_national_road_thresholds(self):
        assert congestion_grade(45, "its") == CongestionGrade.SMOOTH
        assert congestion_grade(30, "its") == CongestionGrade.SLOW
        assert congestion_grade(10, "its") == CongestionGrade.JAM

    def test_unknown(self):
        assert congestion_grade(None, "ex") == CongestionGrade.UNKNOWN
        assert congestion_grade(0, "ex") == CongestionGrade.UNKNOWN
        assert CongestionGrade.JAM.label == "정체"


class TestDatetime:
    """ITS 일시 파싱 테스트."""

    def test_formats(self):
        assert parse_its_datetime("20260923174512") == datetime(2026, 9, 23, 17, 45, 12)
        assert parse_its_datetime("202609231745") == datetime(2026, 9, 23, 17, 45)
        assert parse_its_datetime("2026-09-23 17:45:12") == datetime(2026, 9, 23, 17, 45, 12)
        assert parse_its_datetime("20260923") == datetime(2026, 9, 23)

    def test_invalid(self):
        assert parse_its_datetime(None) is None
        assert parse_its_datetime("") is None
        assert parse_its_datetime("abc") is None
        assert parse_its_datetime("20261399999999") is None


class TestTrafficLink:
    """링크 소통정보 모델 테스트."""

    def test_parse_api_item(self):
        link = TrafficLink.model_validate(
            {
                "roadName": "경부고속도로",
                "roadDrcType": "0",
                "linkNo": 12,
                "linkId": "1000000100",
                "startNodeId": "100",
                "endNodeId": "101",
                "speed": "85.5",
                "travelTime": "42",
                "createdDate": "20260923174500",
            }
        )
        link.road_type = "ex"
        assert link.speed == 85.5
        assert link.travel_time == 42.0
        assert link.link_no == "12"
        assert link.has_measurement
        assert link.grade == CongestionGrade.SMOOTH

    def test_missing_values(self):
        link = TrafficLink.model_validate({"roadName": None, "speed": "", "travelTime": None})
        assert link.road_name == ""
        assert link.speed is None
        assert not link.has_measurement
        assert link.grade == CongestionGrade.UNKNOWN


class TestTrafficEvent:
    """돌발 정보 모델 테스트."""

    RAW = {
        "type": "고속도로",
        "eventType": "교통사고",
        "eventDetailType": "추돌사고",
        "startDate": "20260923170000",
        "endDate": "20260923180000",
        "coordX": "127.1234567",
        "coordY": "36.1234567",
        "linkId": "1234",
        "roadName": "경부선",
        "roadNo": "1",
        "roadDrcType": "상행",
        "lanesBlockType": "1",
        "lanesBlocked": "2",
        "message": "추돌사고로 2차로 차단",
    }

    def test_parse(self):
        event = TrafficEvent.model_validate(self.RAW)
        assert event.kind == "acc"
        assert event.road_type == "ex"
        assert event.coord_x == 127.1234567
        assert event.lanes_blocked == "2"

    def test_event_id_is_stable_and_distinct(self):
        a = TrafficEvent.model_validate(self.RAW)
        b = TrafficEvent.model_validate(dict(self.RAW))
        c = TrafficEvent.model_validate({**self.RAW, "startDate": "20260923171000"})
        assert a.event_id == b.event_id
        assert a.event_id != c.event_id
        assert len(a.event_id) == 16

    def test_to_dict(self):
        data = TrafficEvent.model_validate(self.RAW).to_dict()
        assert data["kind_label"] == "교통사고"
        assert data["lat"] == 36.1234567
        assert data["lon"] == 127.1234567
        assert data["started_at"] == "2026-09-23T17:00:00"
        assert data["road_key"] == road_key("ex", "경부선")

    def test_empty_strings_become_none(self):
        event = TrafficEvent.model_validate({"eventType": "공사", "lanesBlocked": "", "coordX": ""})
        assert event.lanes_blocked is None
        assert event.coord_x is None
        assert event.to_dict()["started_at"] is None
