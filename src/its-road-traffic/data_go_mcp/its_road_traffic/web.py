"""전국 도로 소통정보 모바일/데스크탑 하이브리드 웹 대시보드 (PWA).

실행:
    data-go-mcp.its-road-traffic-web --port 8000          # ITS_API_KEY 필요
    data-go-mcp.its-road-traffic-web --demo               # 데모 데이터
"""

import argparse
import asyncio
import contextlib
import httpx
import json
import logging
import os
import re
from .models import EVENT_KIND_LABELS
from .push import WebPushNotifier
from .service import TrafficService
from dotenv import load_dotenv
from pathlib import Path
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from typing import Any, AsyncIterator, Dict, Optional
from urllib.parse import quote, urljoin, urlparse


logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SSE_HEARTBEAT_SECONDS = 20.0
PROXY_MAX_BYTES = 20 * 1024 * 1024

# 기본 지도 스타일: OpenFreeMap (API 키 불필요, OSM 기반 벡터 타일)
DEFAULT_MAP_STYLE = "https://tiles.openfreemap.org/styles/positron"
DEFAULT_MAP_STYLE_DARK = "https://tiles.openfreemap.org/styles/dark"

_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')


def rewrite_playlist(text: str, base_url: str, proxy_path: str = "proxy?u=") -> str:
    """HLS 플레이리스트(m3u8)의 세그먼트·키 URI를 프록시 경로로 바꾼다.

    프록시 경로는 플레이리스트 URL(/api/cctv/proxy)을 기준으로 한 상대경로라
    앱이 하위 경로에 배포되어도 동작한다.
    """

    def proxied(uri: str) -> str:
        return proxy_path + quote(urljoin(base_url, uri), safe="")

    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(proxied(stripped))
        elif stripped.startswith("#") and "URI=" in stripped:
            lines.append(_URI_ATTR_RE.sub(lambda m: f'URI="{proxied(m.group(1))}"', line))
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def build_service(demo: Optional[bool] = None) -> TrafficService:
    """환경변수 설정으로 TrafficService를 만든다. API 키가 없으면 데모 모드."""
    load_dotenv()
    api_key = os.getenv("ITS_API_KEY") or os.getenv("API_KEY")
    if demo is None:
        demo = _env_flag("ITS_DEMO_MODE")
    if not demo and not api_key:
        logger.warning("ITS_API_KEY is not set. Starting in demo mode.")
        demo = True

    if demo:
        from .demo import DemoTrafficSource

        source: Any = DemoTrafficSource()
        default_interval, default_ttl = 30.0, 60.0
    else:
        from .api_client import ItsRoadTrafficAPIClient

        source = ItsRoadTrafficAPIClient(api_key=api_key)
        # 개발계정 1,000건/일: 돌발 3분(480건) + 소통 고속도로·국도 각 10분(288건)
        # + CCTV 고속도로·국도 각 30분(96건, 요청이 있을 때만)
        default_interval, default_ttl = 180.0, 600.0

    return TrafficService(
        source,
        event_interval=float(os.getenv("ITS_EVENT_POLL_SECONDS") or default_interval),
        traffic_ttl=float(os.getenv("ITS_TRAFFIC_TTL_SECONDS") or default_ttl),
        cctv_ttl=float(os.getenv("ITS_CCTV_TTL_SECONDS") or 1800),
        demo=demo,
    )


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _read_json(request: Request) -> Optional[Dict[str, Any]]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def create_app(
    service: Optional[TrafficService] = None,
    notifier: Optional[WebPushNotifier] = None,
    start_polling: bool = True,
    stream_transport: Optional[httpx.AsyncBaseTransport] = None,
) -> Starlette:
    """Starlette 앱 생성.

    Args:
        service: 사용할 TrafficService. None이면 환경변수로 만든다.
        notifier: Web Push 알림기. None이면 기본 설정으로 만든다.
        start_polling: 앱 시작 시 돌발 폴링을 시작할지 여부
        stream_transport: CCTV 프록시용 httpx 전송 계층 (테스트용)
    """
    service = service or build_service()
    notifier = notifier if notifier is not None else WebPushNotifier()
    if notifier.enabled:
        service.add_notifier(notifier)
    cctv_proxy_enabled = os.getenv("ITS_CCTV_PROXY", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    map_style = os.getenv("ITS_MAP_STYLE_URL") or DEFAULT_MAP_STYLE
    map_style_dark = os.getenv("ITS_MAP_STYLE_DARK_URL") or (
        DEFAULT_MAP_STYLE_DARK if not os.getenv("ITS_MAP_STYLE_URL") else map_style
    )
    http = httpx.AsyncClient(timeout=15.0, follow_redirects=True, transport=stream_transport)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if start_polling:
            service.start()
        try:
            yield
        finally:
            await service.stop()
            await http.aclose()

    async def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def service_worker(request: Request) -> Response:
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    async def manifest(request: Request) -> Response:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json"
        )

    async def status(request: Request) -> Response:
        return JSONResponse(
            {
                **service.status(),
                "push": {
                    "enabled": notifier.enabled,
                    "subscriptions": notifier.subscription_count,
                },
                "event_kinds": {k: v for k, v in EVENT_KIND_LABELS.items() if k != "all"},
                "map": {"style": map_style, "style_dark": map_style_dark},
                "cctv": {"enabled": service.supports_cctv, "proxy": cctv_proxy_enabled},
            }
        )

    async def roads(request: Request) -> Response:
        road_type = request.query_params.get("type", "ex")
        if road_type not in ("ex", "its", "all"):
            return _error("type must be one of ex, its, all", 400)
        try:
            items = await service.roads(road_type)
        except Exception as e:
            return _error(f"소통정보를 가져오지 못했습니다: {e}", 502)
        return JSONResponse(
            {
                "road_type": road_type,
                "items": items,
                "updated_at": service.status()["traffic_updated"].get(road_type),
                "demo": service.demo,
            }
        )

    async def road(request: Request) -> Response:
        key = request.path_params["key"]
        try:
            detail = await service.road(key)
        except Exception as e:
            return _error(f"소통정보를 가져오지 못했습니다: {e}", 502)
        if detail is None:
            return _error(f"노선을 찾을 수 없습니다: {key}", 404)
        return JSONResponse(detail)

    async def events(request: Request) -> Response:
        params = request.query_params
        kinds = [k for k in params.get("kinds", "").split(",") if k]
        try:
            items = await service.filtered_events(
                road_type=params.get("type"), kinds=kinds, road_key=params.get("road")
            )
        except Exception as e:
            return _error(f"돌발정보를 가져오지 못했습니다: {e}", 502)
        return JSONResponse(
            {
                "items": items,
                "updated_at": service.status()["events_updated"],
                "demo": service.demo,
            }
        )

    async def alerts(request: Request) -> Response:
        return JSONResponse({"items": service.recent_alerts})

    async def stream(request: Request) -> Response:
        queue = service.subscribe()

        async def generator() -> AsyncIterator[str]:
            try:
                yield f"event: hello\ndata: {json.dumps(service.status())}\n\n"
                while True:
                    try:
                        message = await asyncio.wait_for(queue.get(), SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield ": heartbeat\n\n"
                        continue
                    data = json.dumps(message, ensure_ascii=False)
                    yield f"event: {message['type']}\ndata: {data}\n\n"
            finally:
                service.unsubscribe(queue)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def push_config(request: Request) -> Response:
        return JSONResponse({"enabled": notifier.enabled, "public_key": notifier.public_key})

    async def push_subscribe(request: Request) -> Response:
        if not notifier.enabled:
            return _error("Web Push is not enabled on this server (install the [push] extra)", 503)
        body = await _read_json(request)
        if body is None:
            return _error("JSON body required", 400)
        try:
            notifier.subscribe(body.get("subscription") or {}, body.get("filters") or {})
        except ValueError as e:
            return _error(str(e), 400)
        return JSONResponse({"ok": True})

    async def push_unsubscribe(request: Request) -> Response:
        body = await _read_json(request)
        if body is None or not body.get("endpoint"):
            return _error("endpoint required", 400)
        return JSONResponse({"ok": notifier.unsubscribe(body["endpoint"])})

    async def demo_incident(request: Request) -> Response:
        if not service.demo:
            return _error("Only available in demo mode", 403)
        body = await _read_json(request) or {}
        kind = body.get("kind") or "acc"
        if kind not in EVENT_KIND_LABELS or kind == "all":
            return _error(f"invalid kind: {kind}", 400)
        service.source.add_event(kind=kind, road_key=body.get("road_key"))
        new_events = await service.refresh_events()
        return JSONResponse({"new_events": new_events})

    async def cctv(request: Request) -> Response:
        road_type = request.query_params.get("type", "all")
        if road_type not in ("ex", "its", "all"):
            return _error("type must be one of ex, its, all", 400)
        try:
            groups = await service.cctv_groups(road_type, request.query_params.get("q"))
        except Exception as e:
            return _error(f"CCTV 정보를 가져오지 못했습니다: {e}", 502)
        updated = service.status()["cctv_updated"]
        return JSONResponse(
            {
                "road_type": road_type,
                "groups": groups,
                "total": sum(group["count"] for group in groups),
                "updated_at": max(updated.values()) if updated else None,
                "demo": service.demo,
            }
        )

    async def cctv_proxy(request: Request) -> Response:
        """CCTV 영상 프록시. HTTPS 페이지의 HTTP 영상(혼합 콘텐츠)·CORS 문제를 피한다."""
        if not cctv_proxy_enabled:
            return _error("CCTV proxy is disabled", 403)
        url = request.query_params.get("u", "")
        if not service.is_allowed_stream(url):
            return _error("URL is not an ITS CCTV stream", 403)
        try:
            upstream = await http.get(url)
        except httpx.HTTPError as e:
            return _error(f"CCTV 영상에 연결하지 못했습니다: {e!r}", 502)
        if upstream.status_code >= 400:
            return _error(f"CCTV 서버 응답 {upstream.status_code}", 502)
        if len(upstream.content) > PROXY_MAX_BYTES:
            return _error("CCTV 응답이 너무 큽니다", 502)
        final_url = str(upstream.url)
        final_host = urlparse(final_url).hostname
        if final_host:
            # 리다이렉트된 호스트의 세그먼트도 허용한다
            service.allow_stream_host(final_host)
        content_type = upstream.headers.get("content-type", "application/octet-stream")
        headers = {"Cache-Control": "no-cache"}
        if "mpegurl" in content_type.lower() or urlparse(final_url).path.endswith(".m3u8"):
            body = rewrite_playlist(upstream.text, final_url)
            return Response(body, media_type="application/vnd.apple.mpegurl", headers=headers)
        return Response(upstream.content, media_type=content_type, headers=headers)

    async def demo_cctv(request: Request) -> Response:
        if not service.demo:
            return _error("Only available in demo mode", 403)
        svg = service.source.render_cctv(request.path_params["slug"])
        if svg is None:
            return _error("CCTV not found", 404)
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    routes = [
        Route("/", index),
        Route("/sw.js", service_worker),
        Route("/manifest.webmanifest", manifest),
        Route("/api/status", status),
        Route("/api/roads", roads),
        Route("/api/roads/{key:path}", road),
        Route("/api/events", events),
        Route("/api/alerts", alerts),
        Route("/api/stream", stream),
        Route("/api/push/config", push_config),
        Route("/api/push/subscribe", push_subscribe, methods=["POST"]),
        Route("/api/push/unsubscribe", push_unsubscribe, methods=["POST"]),
        Route("/api/cctv", cctv),
        Route("/api/cctv/proxy", cctv_proxy),
        Route("/api/demo/incident", demo_incident, methods=["POST"]),
        Route("/api/demo/cctv/{slug}.svg", demo_cctv),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.service = service
    app.state.notifier = notifier
    return app


def main() -> None:
    """웹 대시보드 실행."""
    import uvicorn

    parser = argparse.ArgumentParser(description="ITS 전국 도로 소통정보 웹 대시보드")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--demo", action="store_true", help="API 키 없이 데모 데이터로 실행")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app(build_service(demo=True if args.demo else None))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
