from django.contrib import admin
from .models import Servicio, Testimonio, ContactoMensaje


@admin.register(Servicio)
class ServicioAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'icono', 'orden', 'activo']
    list_editable = ['orden', 'activo']
    list_filter = ['activo']
    search_fields = ['titulo', 'descripcion']


@admin.register(Testimonio)
class TestimonioAdmin(admin.ModelAdmin):
    list_display = ['nombre_cliente', 'calificacion', 'activo', 'fecha_creacion']
    list_editable = ['activo']
    list_filter = ['activo', 'calificacion']
    search_fields = ['nombre_cliente', 'testimonio']


@admin.register(ContactoMensaje)
class ContactoMensajeAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'email', 'telefono', 'fecha_envio', 'leido']
    list_editable = ['leido']
    list_filter = ['leido', 'fecha_envio']
    search_fields = ['nombre', 'email', 'mensaje']
    readonly_fields = ['nombre', 'email', 'telefono', 'mensaje', 'fecha_envio']
    
    def has_add_permission(self, request):
        return False  # Los mensajes solo se crean desde el formulario público
