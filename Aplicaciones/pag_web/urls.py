from django.urls import path
from . import views

app_name = 'pag_web'

urlpatterns = [
    # Página principal (landing page)
    path('', views.index_view, name='index'),
    
    # Páginas individuales
    path('lotes/', views.lotes_view, name='lotes'),
    path('lotes/<int:pk>/', views.lote_detalle_view, name='lote_detalle'),
    path('servicios/', views.servicios_view, name='servicios'),
    path('nosotros/', views.nosotros_view, name='nosotros'),
    path('testimonios/', views.testimonios_view, name='testimonios'),
    path('contacto/', views.contacto_view, name='contacto'),
]
