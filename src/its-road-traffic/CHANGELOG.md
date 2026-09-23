# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-23

### Added
- ITS 국가교통정보센터 교통소통정보(trafficInfo)·돌발상황정보(eventInfo)·CCTV정보(cctvInfo) API 클라이언트
- MCP 도구: `list_roads`, `get_road_traffic`, `get_traffic_events`, `list_cctv`
- MapLibre GL 전국 지도: 노선별 소통 등급 색, 돌발 마커, CCTV 클러스터, 레이어 켜기/끄기
- CCTV: 노선별 목록(노선을 따라 정렬)·전국 지도, HLS/MP4/정지영상 플레이어, 돌발 근처 CCTV 바로 보기,
  HTTPS 혼합 콘텐츠·CORS를 피하는 영상 중계 프록시(허용 호스트 제한)
- 모바일/데스크탑 하이브리드 웹 대시보드 (PWA)
  - 모바일: 노선 · 전국지도 · 돌발 · CCTV · 알림 탭, 고속도로/국도 목록에서 선택 → 노선 상세
  - 데스크탑: 노선 소통·CCTV 목록 + 전국 지도 + 상세/돌발 피드 3단 화면
- 사고·돌발 발생 알림 (SSE 실시간 알림 + 선택적 Web Push)
- API 키 없이 화면을 확인할 수 있는 데모 모드
