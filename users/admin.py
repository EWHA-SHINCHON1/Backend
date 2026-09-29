from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import SocialAccount, User


class SocialAccountInline(admin.TabularInline):
    model = SocialAccount
    extra = 0
    fields = ('provider', 'provider_user_id', 'created_at')
    readonly_fields = ('provider', 'provider_user_id', 'created_at')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        # 소셜 계정 연결은 카카오 로그인 과정에서만 만듭니다.
        return False


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('id', 'username', 'nickname', 'is_active', 'is_staff', 'date_joined')
    search_fields = ('username', 'nickname')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('서비스 정보', {'fields': ('nickname',)}),
    )
    inlines = [SocialAccountInline]


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'provider', 'provider_user_id', 'created_at')
    list_filter = ('provider',)
    search_fields = ('provider_user_id', 'user__username', 'user__nickname')
    readonly_fields = ('created_at',)
    raw_id_fields = ('user',)
