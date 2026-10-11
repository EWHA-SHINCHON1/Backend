from rest_framework.throttling import SimpleRateThrottle


class PromotionEventIPThrottle(SimpleRateThrottle):
    """Limit only the anonymous event-recording endpoint by client IP."""

    scope = 'promotion_events'
    rate = '60/min'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}
