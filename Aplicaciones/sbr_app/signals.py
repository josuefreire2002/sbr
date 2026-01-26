
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver
from .models import LogActividad

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    ip = get_client_ip(request)
    LogActividad.objects.create(
        usuario=user,
        accion="Inicio de Sesión",
        detalle=f"Login exitoso desde {ip}",
        ip_address=ip
    )

@receiver(user_login_failed)
def log_user_login_failed(sender, credentials, request, **kwargs):
    ip = get_client_ip(request)
    username = credentials.get('username', 'Desconocido')
    LogActividad.objects.create(
        accion="Intento de Login Fallido",
        detalle=f"Usuario intentado: {username}. IP: {ip}",
        ip_address=ip
    )
