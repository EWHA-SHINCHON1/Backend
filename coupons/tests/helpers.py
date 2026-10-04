import threading
import time
from datetime import timedelta

from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from promotions.models import Promotion
from stores.models import Store

ME_URL = '/api/v1/auth/me/'


def issue_url(promotion_id):
    return f'/api/v1/promotions/{promotion_id}/coupons/'


def make_store(**kwargs):
    data = {'name': '매장', 'category': Store.Category.BAKERY_CAFE, 'address': '주소'}
    data.update(kwargs)
    return Store.objects.create(**data)


def make_promotion(store=None, **kwargs):
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
    return Promotion.objects.create(**data)


def make_non_coupon_promotion(store=None, **kwargs):
    kwargs.setdefault('requires_coupon', False)
    kwargs.setdefault('redeem_until', None)
    kwargs.setdefault('total_quantity', None)
    return make_promotion(store, **kwargs)


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


def wait_for_lock_waiter(timeout=10):
    """현재 테스트 DB에서 행 잠금을 기다리는 다른 연결이 생길 때까지 기다린다."""
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
