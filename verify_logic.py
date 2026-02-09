from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth.models import User
from Aplicaciones.sbr_app.models import Cliente, Lote, Contrato, Cuota, Pago, ConfiguracionSistema
from Aplicaciones.sbr_app.services import (
    generar_tabla_amortizacion,
    actualizar_moras_contrato,
    registrar_pago_cliente
)
import os

def run():
    print("--- INICIANDO VERIFICACIÓN DE LÓGICA DE NEGOCIO ---")
    
    # 1. Setup Data
    try:
        user, _ = User.objects.get_or_create(username='test_admin')
        cliente, _ = Cliente.objects.get_or_create(
            cedula='1111111111',
            defaults={'nombres': 'Test', 'apellidos': 'User', 'celular': '0999999999', 'direccion': 'Test Dir', 'vendedor': user}
        )
        lote, _ = Lote.objects.get_or_create(
            manzana='Z',
            numero_lote='99',
            defaults={'dimensiones': '10x10', 'precio_contado': 5000, 'estado': 'DISPONIBLE'}
        )
        config, _ = ConfiguracionSistema.objects.get_or_create(
            id=1,
            defaults={'mora_leve_dias': 1, 'mora_porcentaje': 10.00, 'nombre_empresa': 'TestCorp', 'ruc_empresa': '123'}
        )
        # Force config values for test
        config.mora_leve_dias = 1
        config.mora_porcentaje = Decimal('10.00') # 10% for easy math
        config.save()

        # Clean previous test contracts
        Contrato.objects.filter(cliente=cliente).delete()

        # Create Contract
        contrato = Contrato.objects.create(
            cliente=cliente,
            fecha_contrato=date.today() - timedelta(days=30), # Contrato hace 1 mes
            precio_venta_final=1000,
            valor_entrada=0,
            saldo_a_financiar=1000,
            numero_cuotas=2,
            estado='ACTIVO'
        )
        contrato.lotes.add(lote)
        
        print(f"[OK] Contrato Creado: ID {contrato.id}")
        generar_tabla_amortizacion(contrato.id)
        
        # --- TEST MANUAL EDIT SCENARIOS ---
        print("\n--- TEST: EDICIÓN MANUAL DE CUOTAS ---")
        
        c1 = contrato.cuotas.get(numero_cuota=1)
        # Simulamos que C1 venció hace días
        c1.fecha_vencimiento = date.today() - timedelta(days=5)
        c1.save()
        actualizar_moras_contrato(contrato.id)
        c1.refresh_from_db()
        
        print(f"Estado Inicial C1: {c1.estado}, Mora: {c1.valor_mora}")
        
        # SCENARIO A: Admin manually sets payment to FULL (without creating Pago object)
        # This simulates `editar_cuota_view` logic
        print(">> Simulando edición manual: Marcar como PAGADO ($550) sin registrar ingreso...")
        c1.valor_pagado = Decimal('550.00') # Capital 500 + Mora 50
        
        # Logic copied from `editar_cuota_view`
        saldo = (c1.valor_capital + c1.valor_mora) - c1.valor_pagado
        if saldo < Decimal('0.01'):
            c1.estado = 'PAGADO'
        elif c1.fecha_vencimiento < date.today():
             c1.estado = 'VENCIDO'
        
        c1.save()
        actualizar_moras_contrato(contrato.id)
        c1.refresh_from_db()

        assert c1.estado == 'PAGADO', "El estado debería ser PAGADO tras edición manual"
        print("[OK] La cuota cambió a PAGADO correctamente.")
        
        # CHECK CONSISTENCY
        total_en_cuotas = sum(c.valor_pagado for c in contrato.cuotas.all())
        total_en_pagos = sum(p.monto for p in contrato.pago_set.all())
        
        print(f"Total registrado en Cuotas: ${total_en_cuotas}")
        print(f"Total registrado en Pagos (Caja): ${total_en_pagos}")
        
        if total_en_cuotas != total_en_pagos:
            print("!!! ALERTA DE CONSISTENCIA DETECTADA !!!")
            print("El dinero en la tabla de amortización NO coincide con el dinero en caja (Pagos).")
            print("Esto significa que el reporte mensual NO mostrará este ingreso manual.")
        
        # SCENARIO B: Admin removes payment manually
        print("\n>> Simulando reversión manual: Poner en 0...")
        c1.valor_pagado = Decimal('0.00')
        c1.estado = 'VENCIDO' # Logic would set it back to VENCIDO because date is passed
        c1.save()
        actualizar_moras_contrato(contrato.id)
        c1.refresh_from_db()
        
        assert c1.estado == 'VENCIDO', "Debería volver a VENCIDO tras quitar el pago"
        assert c1.valor_mora > 0, "Debería volver a tener mora calculada"
        print("[OK] La cuota volvió a VENCIDO y recalculó mora correctamente.")

        print("\n--- VERIFICACIÓN FINALIZADA ---")

    except AssertionError as e:
        print(f"!!! FALLO EN VERIFICACIÓN: {e}")
    except Exception as e:
        print(f"!!! ERROR DE EJECUCIÓN: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run()
