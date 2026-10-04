from rest_framework import serializers


class MeSerializer(serializers.Serializer):
    """GET /api/v1/auth/me/ 응답. 비밀번호·username 등 내부 필드는 포함하지 않습니다."""

    id = serializers.IntegerField(allow_null=True)
    nickname = serializers.CharField(allow_null=True)
    is_authenticated = serializers.BooleanField()

    def to_representation(self, user):
        if not user.is_authenticated:
            return {'id': None, 'nickname': None, 'is_authenticated': False}
        return {'id': user.id, 'nickname': user.nickname, 'is_authenticated': True}
