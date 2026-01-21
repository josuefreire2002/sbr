import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sbr.settings')
django.setup()

from Aplicaciones.sbr_app.models import Contrato

# Encontrar contratos cancelados o devueltos
contratos = Contrato.objects.filter(estado__in=['CANCELADO', 'DEVOLUCION'])

count = 0
for contrato in contratos:
    if contrato.lote.estado != 'DISPONIBLE':
        contrato.lote.estado = 'DISPONIBLE'
        contrato.lote.save()
        count += 1
        print(f"Lote {contrato.lote} actualizado a DISPONIBLE")

print(f"\nTotal lotes actualizados: {count}")
