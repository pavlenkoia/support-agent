from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.case import SupportCase
from app.models.conversation import Conversation
from app.models.viewer_notification_outbox import ViewerNotificationOutbox
from app.models.viewer_push_subscription import ViewerPushSubscription

PushSender = Callable[[ViewerPushSubscription, dict[str, str | int]], None]


class ViewerPushService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker = SessionLocal,
        send_web_push: PushSender | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.send_web_push = send_web_push or self._send_web_push

    def upsert_subscription(
        self,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        expiration_time: int | None = None,
    ) -> bool:
        with self.session_factory() as session:
            subscription = session.scalar(select(ViewerPushSubscription).where(ViewerPushSubscription.endpoint == endpoint))
            created = subscription is None
            if subscription is None:
                subscription = ViewerPushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth)
                session.add(subscription)
            else:
                subscription.p256dh = p256dh
                subscription.auth = auth
                subscription.enabled = True
            subscription.expiration_time = expiration_time
            session.commit()
        return created

    def disable_subscription(self, endpoint: str) -> None:
        with self.session_factory() as session:
            subscription = session.scalar(select(ViewerPushSubscription).where(ViewerPushSubscription.endpoint == endpoint))
            if subscription is not None:
                subscription.enabled = False
                session.commit()

    def process_once(self) -> int:
        with self.session_factory() as session:
            now = datetime.now(UTC)
            reclaim_before = now - timedelta(seconds=settings.viewer_push_processing_lease_seconds)
            event = session.scalar(
                select(ViewerNotificationOutbox)
                .where(
                    or_(
                        (ViewerNotificationOutbox.status == "pending") & (ViewerNotificationOutbox.available_at <= now),
                        (ViewerNotificationOutbox.status == "processing") & (ViewerNotificationOutbox.updated_at <= reclaim_before),
                    ),
                )
                .order_by(ViewerNotificationOutbox.id.asc())
                .limit(1)
            )
            if event is None:
                return 0
            event.status = "processing"
            event.attempts += 1
            session.commit()
            event_id = event.id

        try:
            self._deliver_event(event_id)
        except Exception as exc:
            with self.session_factory() as session:
                event = session.get(ViewerNotificationOutbox, event_id)
                if event is not None:
                    event.status = "pending"
                    event.error_text = str(exc)[:1000]
                    event.available_at = datetime.now(UTC) + timedelta(seconds=min(60 * event.attempts, 900))
                    session.commit()
            return 0
        return 1

    def _deliver_event(self, event_id: int) -> None:
        with self.session_factory() as session:
            event = session.get(ViewerNotificationOutbox, event_id)
            if event is None:
                return
            conversation = session.get(Conversation, event.conversation_id)
            support_case = session.scalar(
                select(SupportCase).where(SupportCase.conversation_id == event.conversation_id).order_by(SupportCase.id.desc())
            )
            if conversation is None or support_case is None:
                raise RuntimeError("viewer notification event has no conversation or support case")
            payload = {
                "type": "viewer_user_message",
                "conversation_id": conversation.external_id,
                "case_id": support_case.id,
            }
            subscriptions = session.scalars(select(ViewerPushSubscription).where(ViewerPushSubscription.enabled.is_(True))).all()

        invalid_endpoints: list[str] = []
        for subscription in subscriptions:
            try:
                self.send_web_push(subscription, payload)
            except Exception as exc:
                if getattr(exc, "status_code", None) in {404, 410}:
                    invalid_endpoints.append(subscription.endpoint)
                    continue
                raise

        with self.session_factory() as session:
            event = session.get(ViewerNotificationOutbox, event_id)
            if event is not None:
                event.status = "delivered"
                event.delivered_at = datetime.now(UTC)
                event.error_text = None
            if invalid_endpoints:
                for subscription in session.scalars(select(ViewerPushSubscription).where(ViewerPushSubscription.endpoint.in_(invalid_endpoints))):
                    subscription.enabled = False
            session.commit()

    @staticmethod
    def _send_web_push(subscription: ViewerPushSubscription, payload: dict[str, str | int]) -> None:
        from pywebpush import webpush

        private_key = settings.viewer_push_vapid_private_key
        subject = settings.viewer_push_vapid_subject
        if not private_key or not subject:
            raise RuntimeError("viewer push VAPID credentials are not configured")
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=__import__("json").dumps(payload, separators=(",", ":")),
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
            ttl=settings.viewer_push_ttl_seconds,
            headers={"Urgency": settings.viewer_push_urgency},
        )
