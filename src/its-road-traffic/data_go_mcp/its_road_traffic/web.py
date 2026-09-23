"""전국 도로 소통정보 모바일/데스크탑 하이브리드 웹 대시보드 (PWA).

실행:
    data-go-mcp.its-road-traffic-web --port 8000          # ITS_API_KEY 필요
    data-go-mcp.its-road-traffic-web --demo               # 데모 데이터
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
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


logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SSE_HEARTBEAT_SECONDS = 20.0


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
        default_interval, default_ttl = 180.0, 600.0

    return TrafficService(
        source,
        event_interval=float(os.getenv("ITS_EVENT_POLL_SECONDS") or default_interval),
        traffic_ttl=float(os.getenv("ITS_TRAFFIC_TTL_SECONDS") or default_ttl),
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
) -> Starlette:
    """Starlette 앱 생성.

    Args:
        service: 사용할 TrafficService. None이면 환경변수로 만든다.
        notifier: Web Push 알림기. None이면 기본 설정으로 만든다.
        start_polling: 앱 시작 시 돌발 폴링을 시작할지 여부
    """
    service = service or build_service()
    notifier = notifier if notifier is not None else WebPushNotifier()
    if notifier.enabled:
        service.add_notifier(notifier)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if start_polling:
            service.start()
        try:
            yield
        finally:
            await service.stop()

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
        Route("/api/demo/incident", demo_incident, methods=["POST"]),
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
