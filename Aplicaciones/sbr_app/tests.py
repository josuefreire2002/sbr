from django.test import TestCase
from decimal import Decimal
from datetime import date, timedelta
from django.contrib.auth.models import User
from .models import Cliente, Lote, Contrato, Cuota, ConfiguracionSistema
from .services import actualizar_moras_contrato

class MoraCalculationTests(TestCase):
    def setUp(self):
        # Create dependencies
        self.user = User.objects.create_user(username='testuser', password='password')
        self.cliente = Cliente.objects.create(
            vendedor=self.user, cedula='1234567890', nombres='Test', apellidos='User', 
            celular='0999999999', direccion='Test Address'
        )
        self.lote = Lote.objects.create(
            manzana='A', numero_lote='1', dimensiones='10x20', precio_contado=1000, 
            creado_por=self.user
        )
        # Create default configuration (defaults to 3%)
        self.config = ConfiguracionSistema.objects.create(
            nombre_empresa='Test Corp', ruc_empresa='123',
            mora_leve_dias=5, mora_porcentaje=3.00
        )

    def test_mora_percentage_calculation(self):
        """Test that mora is calculated as 3% of capital after grace period"""
        # Create contract
        contrato = Contrato.objects.create(
            cliente=self.cliente, lote=self.lote, fecha_contrato=date.today() - timedelta(days=60),
            precio_venta_final=1200, valor_entrada=0, saldo_a_financiar=1200, numero_cuotas=12
        )
        # Create a single past due installment
        # Capital $100. 3% should be $3.00
        cuota = Cuota.objects.create(
            contrato=contrato, numero_cuota=1, 
            fecha_vencimiento=date.today() - timedelta(days=10), # 10 days late (> 5 grace days)
            valor_capital=Decimal('100.00'), valor_mora=0, estado='PENDIENTE'
        )

        # Run logic
        actualizar_moras_contrato(contrato.id)
        
        # Reload and check
        cuota.refresh_from_db()
        self.assertEqual(cuota.estado, 'VENCIDO')
        self.assertEqual(cuota.valor_mora, Decimal('3.00')) # 100 * 0.03 = 3.00

    def test_grade_period_respect(self):
        """Test that mora is 0 if within grace period"""
        contrato = Contrato.objects.create(
            cliente=self.cliente, lote=self.lote, fecha_contrato=date.today(),
            precio_venta_final=1200, valor_entrada=0, saldo_a_financiar=1200, numero_cuotas=12
        )
        # Only 2 days late (grace is 5)
        cuota = Cuota.objects.create(
            contrato=contrato, numero_cuota=1, 
            fecha_vencimiento=date.today() - timedelta(days=2), 
            valor_capital=Decimal('100.00'), valor_mora=0, estado='PENDIENTE'
        )
        
        actualizar_moras_contrato(contrato.id)
        
        cuota.refresh_from_db()
        self.assertEqual(cuota.estado, 'VENCIDO') # Still "VENCIDO" because it's late
        self.assertEqual(cuota.valor_mora, Decimal('0.00')) # But no financial penalty yet
