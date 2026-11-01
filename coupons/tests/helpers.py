import threading
import time
from datetime import timedelta

from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from coupons.models import Coupon, CouponRound
from promotions.models import Promotion
from stores.models import Store

ME_URL = '/api/v1/auth/me/'


def issue_url(promotion_id):
    return f'/api/v1/promotions/{promotion_id}/coupons/'


def make_store(**kwargs):
    data = {'name': '매장', 'category': Store.Category.BAKERY_CAFE, 'address': '주소'}
    data.update(kwargs)
    return Store.objects.create(**data)


def make_promotion(store=None, *, with_round=True, **kwargs):
    """쿠폰형이면 기본으로 오늘 발급분(수량 = total_quantity)을 함께 만든다."""
    now = timezone.now().replace(microsecond=0)
    data = {
        'store': store or make_store(),
        'title': '프로모션',
        'starts_at': now - timedelta(days=1),
        'ends_at': now + timedelta(days=6),
        'redeem_until': now + timedelta(days=13),
        'total_quantity': 10,
        'is_published': True,
    }
    data.update(kwargs)
    promotion = Promotion.objects.create(**data)
    if with_round and promotion.requires_coupon:
        make_round(promotion)
    return promotion


def make_non_coupon_promotion(store=None, **kwargs):
    kwargs.setdefault('requires_coupon', False)
    kwargs.setdefault('redeem_until', None)
    kwargs.setdefault('total_quantity', None)
    return make_promotion(store, **kwargs)


def end_of_day(moment):
    """moment가 속한 Asia/Seoul 날짜의 23:59:59"""
    return timezone.localtime(moment).replace(hour=23, minute=59, second=59, microsecond=0)


def start_of_day(moment):
    """moment가 속한 Asia/Seoul 날짜의 00:00:00"""
    return timezone.localtime(moment).replace(hour=0, minute=0, second=0, microsecond=0)


def make_round(promotion, *, day=None, issue_starts_at=None, expires_at=None, quantity=None):
    """하루치 발급분. 기본 기간은 day(기준 시각, 기본 지금)가 속한 날 00:00 ~ 23:59:59(redeem_until이 더 이르면 그때)."""
    day = day or timezone.now()
    if expires_at is None:
        expires_at = end_of_day(day)
        if promotion.redeem_until is not None:
            expires_at = min(expires_at, promotion.redeem_until)
    if issue_starts_at is None:
        issue_starts_at = min(start_of_day(day), start_of_day(expires_at))
    return CouponRound.objects.create(
        promotion=promotion,
        issue_starts_at=issue_starts_at,
        expires_at=expires_at,
        quantity=quantity or promotion.total_quantity or 1000,
    )


def make_coupon(**kwargs):
    """Coupon.objects.create와 같은 인자를 받고, round가 없으면 지금 열린 발급분(없으면 오늘 발급분을 새로 만듦)에 연결한다.

    expires_at을 주지 않으면 발급분의 expires_at을 쓴다.
    """
    promotion = kwargs['promotion']
    coupon_round = kwargs.pop('round', None)
    if coupon_round is None:
        now = timezone.now()
        coupon_round = (
            CouponRound.objects.filter(promotion=promotion, issue_starts_at__lte=now, expires_at__gt=now).first()
            or make_round(promotion)
        )
    kwargs.setdefault('expires_at', coupon_round.expires_at)
    return Coupon.objects.create(round=coupon_round, **kwargs)


def logged_in_client(user):
    client = APIClient()
    client.force_login(user)
    return client


def csrf_client(user=None):
    """CSRF 검사를 실제로 하는 클라이언트. me를 호출해 csrftoken 쿠키를 받는다."""
    client = APIClient(enforce_csrf_checks=True)
    if user is not None:
        client.force_login(user)
    client.get(ME_URL)
    return client, client.cookies['csrftoken'].value


def run_in_thread(func, results, key):
    """별도 DB 연결을 쓰는 스레드에서 func를 실행하고 결과(또는 예외)를 results[key]에 담는다."""

    def target():
        try:
            results[key] = func()
        except Exception as exc:  # 테스트에서 확인하기 위해 예외도 결과로 보관
            results[key] = exc
        finally:
            connection.close()

    thread = threading.Thread(target=target)
    thread.start()
    return thread


def wait_for_lock_waiter(timeout=60):
    """현재 테스트 DB에서 행 잠금을 기다리는 다른 연결이 생길 때까지 기다린다.

    잠금 대기가 생기면 바로 돌아오므로 timeout은 느린 로컬 DB(Docker)에서도 넉넉하게 둔다.
    """
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            # pg_stat_activity는 트랜잭션 안에서 스냅샷이 고정되므로 매번 비운다.
            cursor.execute('SELECT pg_stat_clear_snapshot()')
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            )
            if cursor.fetchone()[0] > 0:
                return True
            time.sleep(0.05)
    return False
