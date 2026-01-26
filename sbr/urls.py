from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
import os
from django.contrib.auth.views import LoginView
from Aplicaciones.sbr_app.forms import LoginForm # Importar nuestro form con Recaptcha

# Asegurar carga de variables si no se han cargado (redundancia segura)
from dotenv import load_dotenv
load_dotenv()

urlpatterns = [
    # Panel de Administración (Donde configuras las moras y usuarios)
    # Panel de Administración (Ruta Ofuscada)
    path(os.getenv('ADMIN_URL', 'admin/'), admin.site.urls),

    # Sistema de Autenticación (Login/Logout estándar de Django)
    # Sistema de Autenticación (Login personalizado + Defaults)
    path('accounts/login/', LoginView.as_view(authentication_form=LoginForm), name='login'), # Interceptar Login
    path('accounts/', include('django.contrib.auth.urls')),

    # Tu Aplicación Principal (Asumiendo que la llamaste 'core' o 'ventas')
    path('', include('Aplicaciones.sbr_app.urls')), 
]

# Configuración para servir archivos subidos (Fotos recibos y PDFs) en modo DEBUG
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)