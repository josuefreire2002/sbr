from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_gestor_view, name='gestor_dashboard'),
    path('registrar/', views.registrar_transaccion_view, name='registrar_transaccion'),
    path('editar/<int:tr_id>/', views.editar_transaccion_view, name='editar_transaccion'),
    path('eliminar/<int:tr_id>/', views.eliminar_transaccion_view, name='eliminar_transaccion'),
    path('api/categoria/crear/', views.crear_categoria_api, name='api_crear_categoria'),
    path('api/totales/', views.api_totales_view, name='api_totales'),
]
