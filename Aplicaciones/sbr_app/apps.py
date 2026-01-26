from django.apps import AppConfig


class SbrAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Aplicaciones.sbr_app'

    def ready(self):
        import Aplicaciones.sbr_app.signals
