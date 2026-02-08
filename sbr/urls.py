from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
import os
from django.contrib.auth.views import LoginView

urlpatterns = [
    # Panel de Administración (Donde configuras las moras y usuarios)
    # Panel de Administración (Ruta Ofuscada)
    path(os.getenv('ADMIN_URL', 'panel_gestion_seguro/'), admin.site.urls),

    # Sistema de Autenticación (Login/Logout estándar de Django)
    path('accounts/', include('django.contrib.auth.urls')),

    # Página Web Pública (Landing Page)
    # Página Web Pública (Landing Page) - Ahora en /web/
    path('web/', include('Aplicaciones.pag_web.urls')),
    
    # Sistema de Gestión Interna (sbr_app) - Requiere autenticación
    # Ahora es la página principal por defecto
    path('', include('Aplicaciones.sbr_app.urls')), 
]

# Configuración para servir archivos subidos (Fotos recibos y PDFs) en modo DEBUG
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)