"""Web Push 알림 (선택 기능).

브라우저 탭이 열려 있을 때는 SSE로 알림이 전달되지만, 휴대폰에서 브라우저를 닫아도 알림을
받으려면 Web Push가 필요하다. ``pip install "data-go-mcp.its-road-traffic[push]"``로
pywebpush를 설치하면 활성화된다.

- VAPID 키: ``ITS_VAPID_PRIVATE_KEY`` 환경변수, 없으면 데이터 디렉토리에 생성해 저장
- 구독 정보: 데이터 디렉토리의 ``push_subscriptions.json``
- iOS는 홈 화면에 추가한 PWA(16.4+)에서만 Web Push를 지원한다.
"""

import asyncio
import base64
import json
import logging
import os
import threading
from .service import event_matches
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

try:  # pragma: no cover - 설치 여부에 따라 달라진다
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01 as Vapid
    from pywebpush import WebPushException, webpush

    PUSH_AVAILABLE = True
except ImportError:  # pragma: no cover
    PUSH_AVAILABLE = False


def default_data_dir() -> Path:
    """구독 정보·키를 저장할 디렉토리."""
    return Path(os.getenv("ITS_DATA_DIR") or Path.home() / ".its-road-traffic")


def notification_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """돌발 정보를 알림 표시용 페이로드로 변환."""
    direction = event.get("direction_label") or ""
    title = f"[{event.get('kind_label', '돌발')}] {event.get('road_name', '')} {direction}".strip()
    body = event.get("message") or event.get("detail") or "돌발상황이 발생했습니다."
    return {
        "title": title,
        "body": body,
        "tag": f"its-{event.get('id')}",
        "event_id": event.get("id"),
        "road_key": event.get("road_key"),
        "url": f"./#road={event.get('road_key', '')}&event={event.get('id', '')}",
        "urgent": bool(event.get("urgent")),
    }


class WebPushNotifier:
    """Web Push 구독 관리 및 발송."""

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        subject: Optional[str] = None,
        private_key: Optional[str] = None,
    ):
        """초기화. pywebpush가 없으면 :attr:`enabled`가 False가 된다."""
        self.enabled = PUSH_AVAILABLE
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.subject = subject or os.getenv("ITS_VAPID_SUBJECT") or "mailto:admin@example.com"
        self._lock = threading.Lock()
        self._subscriptions: Dict[str, Dict[str, Any]] = {}
        self._vapid = None
        self.public_key: Optional[str] = None
        if not self.enabled:
            return
        self._vapid = self._load_vapid(private_key or os.getenv("ITS_VAPID_PRIVATE_KEY"))
        raw = self._vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.public_key = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        self._load_subscriptions()

    @property
    def _subscriptions_path(self) -> Path:
        return self.data_dir / "push_subscriptions.json"

    def _load_vapid(self, private_key: Optional[str]):
        if private_key:
            return Vapid.from_string(private_key=private_key)
        key_path = self.data_dir / "vapid_private.pem"
        if key_path.exists():
            return Vapid.from_file(str(key_path))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        vapid = Vapid()
        vapid.generate_keys()
        vapid.save_key(str(key_path))
        os.chmod(key_path, 0o600)
        logger.info("Generated VAPID key at %s", key_path)
        return vapid

    def _load_subscriptions(self) -> None:
        try:
            data = json.loads(self._subscriptions_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._subscriptions = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            logger.warning("Could not read push subscriptions: %s", e)

    def _save_subscriptions(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._subscriptions_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._subscriptions, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._subscriptions_path)

    @property
    def subscription_count(self) -> int:
        """등록된 구독 수."""
        return len(self._subscriptions)

    def subscribe(self, subscription: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> None:
        """구독 등록/갱신."""
        endpoint = subscription.get("endpoint") if isinstance(subscription, dict) else None
        keys = subscription.get("keys") if isinstance(subscription, dict) else None
        if (
            not endpoint
            or not isinstance(keys, dict)
            or not keys.get("p256dh")
            or not keys.get("auth")
        ):
            raise ValueError("Invalid push subscription")
        with self._lock:
            self._subscriptions[endpoint] = {
                "subscription": {"endpoint": endpoint, "keys": keys},
                "filters": filters or {},
            }
            self._save_subscriptions()

    def unsubscribe(self, endpoint: str) -> bool:
        """구독 해제."""
        with self._lock:
            removed = self._subscriptions.pop(endpoint, None) is not None
            if removed:
                self._save_subscriptions()
        return removed

    def _send(self, entry: Dict[str, Any], payload: str) -> Optional[str]:
        """한 구독에 발송. 만료된 구독이면 endpoint를 돌려준다."""
        try:
            webpush(
                subscription_info=entry["subscription"],
                data=payload,
                vapid_private_key=self._vapid,
                vapid_claims={"sub": self.subject},
                ttl=3600,
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                return entry["subscription"]["endpoint"]
            logger.warning("Web push failed (%s): %s", status, e)
        return None

    def _send_all(self, event: Dict[str, Any]) -> int:
        payload = json.dumps(notification_payload(event), ensure_ascii=False)
        with self._lock:
            targets: List[Dict[str, Any]] = [
                entry
                for entry in self._subscriptions.values()
                if event_matches(event, entry.get("filters"))
            ]
        expired = [endpoint for endpoint in (self._send(t, payload) for t in targets) if endpoint]
        for endpoint in expired:
            self.unsubscribe(endpoint)
        return len(targets) - len(expired)

    async def __call__(self, event: Dict[str, Any]) -> None:
        """신규 돌발을 조건에 맞는 구독자에게 발송 (TrafficService 알림기)."""
        if not self.enabled or not self._subscriptions:
            return
        await asyncio.to_thread(self._send_all, event)
