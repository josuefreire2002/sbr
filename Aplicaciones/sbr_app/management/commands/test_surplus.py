
from django.core.management.base import BaseCommand
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth.models import User
from Aplicaciones.sbr_app.models import Contrato, Cuota, DetallePago, Pago, Cliente, Lote
from Aplicaciones.sbr_app.services import registrar_pago_cliente, generar_tabla_amortizacion

class Command(BaseCommand):
    help = 'Test surplus bug reproduction'

    def handle(self, *args, **options):
        self.stdout.write("Running surplus bug reproduction test...")
        
        # Cleanup
        Contrato.objects.all().delete()
        Cliente.objects.all().delete()
        Lote.objects.all().delete()
        User.objects.filter(username='test_user').delete()
        
        # Setup
        user = User.objects.create(username='test_user')
        cliente = Cliente.objects.create(nombres="Test", apellidos="Client", cedula="1234567890", vendedor=user)
        lote = Lote.objects.create(numero=1, manzano=1, precio_total=1500, valor_entrada=0)
        
        contrato = Contrato.objects.create(
            cliente=cliente,
            lote=lote,
            valor_entrada=0,
            numero_cuotas=3,
            saldo_a_financiar=1500,
            fecha_contrato=timezone.now().date(),
            dia_pago=1
        )
        
        generar_tabla_amortizacion(contrato.id)
        
        # Check initial state
        cuotas = list(contrato.cuotas.order_by('numero_cuota'))
        self.stdout.write(f"Quotas Initial: {[c.total_a_pagar for c in cuotas]}") # [500, 500, 500]
        
        # Scenario: Pay 700. Should pay Q1 (500) partial Q2 (200) rest Q3(0)
        self.stdout.write("\n--- Applying Payment of $700 ---")
        registrar_pago_cliente(contrato.id, 700, 'EFECTIVO', None, user)
        
        # Reload quotas
        cuotas = list(Cuota.objects.filter(contrato=contrato).order_by('numero_cuota'))
        
        for c in cuotas:
            self.stdout.write(f"Quota #{c.numero_cuota}: Status={c.estado}, Paid={c.valor_pagado}, Pending={c.saldo_pendiente}")
            
        self.stdout.write("\n--- DetallePago Records ---")
        detalles = DetallePago.objects.all()
        for d in detalles:
            self.stdout.write(f"Pago #{d.pago.id} -> Quota #{d.cuota.numero_cuota}: ${d.monto_aplicado}")

        # Validation
        q3 = cuotas[2]
        if q3.valor_pagado > 0:
            self.stdout.write(self.style.ERROR("\n[FAIL] Quota 3 (Future) received payment unexpectedly!"))
        else:
            self.stdout.write(self.style.SUCCESS("\n[PASS] Quota 3 (Future) has 0 payment."))
            
        q2 = cuotas[1]
        if q2.valor_pagado == 200:
            self.stdout.write(self.style.SUCCESS("[PASS] Quota 2 received exactly 200."))
        else:
            self.stdout.write(self.style.ERROR(f"[FAIL] Quota 2 received {q2.valor_pagado}, expected 200."))
