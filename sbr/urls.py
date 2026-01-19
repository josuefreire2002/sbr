from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Panel de Administración (Donde configuras las moras y usuarios)
    path('admin/', admin.site.urls),

    # Sistema de Autenticación (Login/Logout estándar de Django)
    path('accounts/', include('django.contrib.auth.urls')),

    # Tu Aplicación Principal (Asumiendo que la llamaste 'core' o 'ventas')
    path('', include('Aplicaciones.sbr_app.urls')), 
]

# Configuración para servir archivos subidos (Fotos recibos y PDFs) en modo DEBUG
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)