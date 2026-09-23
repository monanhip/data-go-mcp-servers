# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-23

### Added
- ITS 국가교통정보센터 교통소통정보(trafficInfo)·돌발상황정보(eventInfo) API 클라이언트
- MCP 도구: `list_roads`, `get_road_traffic`, `get_traffic_events`
- 모바일/데스크탑 하이브리드 웹 대시보드 (PWA)
  - 모바일: 고속도로/국도 목록에서 선택 → 노선 상세
  - 데스크탑: 노선 목록 + 지도 + 상세/돌발 피드 3단 화면
- 사고·돌발 발생 알림 (SSE 실시간 알림 + 선택적 Web Push)
- API 키 없이 화면을 확인할 수 있는 데모 모드
