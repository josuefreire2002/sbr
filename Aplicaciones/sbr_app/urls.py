from django.urls import path
from . import views

# Nombre de la app para namespaces (opcional pero recomendado)
# app_name = 'ventas' 

urlpatterns = [
    # --- DASHBOARD / HOME ---
    # Vista principal: Resumen de ventas o accesos directos
    path('', views.dashboard_view, name='dashboard'),

    # --- FLUJO DE VENTAS ---
    # El "Wizard" paso a paso para vender
    path('ventas/nueva/', views.crear_venta_view, name='crear_venta'),
    
    # Listado de mis clientes (Vendedor ve los suyos, Admin ve todos)
    path('clientes/', views.lista_clientes_view, name='lista_clientes'),
    
    # Detalle profundo: Tabla de amortización, estado de cuenta
    path('contrato/<int:pk>/detalle/', views.detalle_contrato_view, name='detalle_contrato'),

    # --- FLUJO DE CAJA (PAGOS) ---
    # Formulario para subir recibo o registrar efectivo
    path('contrato/<int:pk>/pagar/', views.registrar_pago_view, name='registrar_pago'),
    path('contrato/<int:pk>/cerrar/', views.cerrar_contrato_view, name='cerrar_contrato'),
    path('contrato/<int:pk>/cancelar/', views.cancelar_contrato_view, name='cancelar_contrato'),
    path('contrato/<int:pk>/devolucion/', views.devolucion_contrato_view, name='devolucion_contrato'),

    # --- REPORTES Y ARCHIVOS ---
    # Ruta para descargar el PDF generado (WeasyPrint)
    path('contrato/<int:pk>/descargar-pdf/', views.descargar_contrato_pdf, name='descargar_pdf'),
    path('contrato/<int:pk>/descargar-recibo-entrada/', views.descargar_recibo_entrada_pdf, name='descargar_recibo_entrada'),
    path('contrato/<int:pk>/descargar-word/', views.descargar_contrato_word, name='descargar_word'),
    
    # Ruta para ver el recibo de transferencia (imagen)
    path('pago/<int:pago_id>/ver-comprobante/', views.ver_comprobante_view, name='ver_comprobante'),

    # Reporte mensual de ingresos y mora
    path('reportes/mensual/', views.reporte_mensual_view, name='reporte_mensual'),
    path('reportes/mensual/pdf/', views.reporte_mensual_pdf_view, name='reporte_mensual_pdf'),
    path('reportes/general/', views.reporte_general_view, name='reporte_general'),
    path('reportes/general/pdf/', views.reporte_general_pdf_view, name='reporte_general_pdf'),

    path('lotes/', views.gestion_lotes_view, name='gestion_lotes'),
    path('lotes/crear/', views.crear_lote_view, name='crear_lote'),
    path('lotes/editar/<int:pk>/', views.editar_lote_view, name='editar_lote'),
    
    # Control manual de mora
    path('cuota/<int:cuota_id>/toggle-mora/', views.toggle_mora_cuota, name='toggle_mora_cuota'),
]