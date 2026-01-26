
from django.contrib import admin
from .models import *

# Registramos tus modelos existentes si no lo están
# (Asumo que ya tienes algunos, pero agrego LogActividad explícitamente)

@admin.register(LogActividad)
class LogActividadAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'usuario', 'accion', 'ip_address')
    list_filter = ('accion', 'fecha', 'usuario')
    search_fields = ('usuario__username', 'accion', 'detalle', 'ip_address')
    readonly_fields = ('fecha', 'usuario', 'accion', 'detalle', 'ip_address')

    def has_add_permission(self, request):
        return False # Nadie puede crear logs manuales, solo el sistema

    def has_change_permission(self, request, obj=None):
        return False # Nadie puede editar logs (Integridad)
        
    def has_delete_permission(self, request, obj=None):
        return False # Nadie puede borrar logs (Integridad)
