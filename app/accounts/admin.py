from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.urls import reverse
from django.utils.html import format_html

from tenants.mixins import TenantAdminMixin

from .models import UserProfile, AuditLog


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name = "Korisnički profil"
    verbose_name_plural = "Korisnički profili"


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    readonly_fields = ('password_change_link',)
    change_form_template = 'admin/auth/user/change_form.html'

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return self.add_fieldsets
        if self._is_self_edit(request, obj):
            return (
                (None, {'fields': ('username', 'password_change_link')}),
                ('Osobni podaci', {'fields': ('first_name', 'last_name', 'email')}),
            )
        fieldsets = list(super().get_fieldsets(request, obj))
        fieldsets[0] = (None, {'fields': ('username', 'password_change_link')})
        return fieldsets

    def get_inlines(self, request, obj=None):
        if self._is_self_edit(request, obj):
            return [UserProfileInline]
        return super().get_inlines(request, obj)

    def has_view_permission(self, request, obj=None):
        if self._is_self_edit(request, obj):
            return True
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if self._is_self_edit(request, obj):
            return True
        return super().has_change_permission(request, obj)

    @staticmethod
    def _is_self_edit(request, obj):
        return (
            obj is not None
            and getattr(request, 'user', None) is not None
            and request.user.is_authenticated
            and obj.pk == request.user.pk
        )

    @admin.display(description='Lozinka')
    def password_change_link(self, obj):
        if not obj or not obj.pk:
            return '—'
        url = reverse('admin:auth_user_password_change', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">Promijeni lozinku</a>',
            url,
        )


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(AuditLog)
class AuditLogAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('user', 'action', 'model_name', 'timestamp')
    list_filter = ('action', 'model_name', 'timestamp')
    readonly_fields = ('user', 'action', 'model_name', 'object_id', 'changes', 'ip_address', 'timestamp')
    search_fields = ('user__username', 'action', 'model_name')
    verbose_name = "Dnevnik promjena"
    verbose_name_plural = "Dnevnik promjena"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
