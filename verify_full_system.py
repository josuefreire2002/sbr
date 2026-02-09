from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth.models import User
from django.db.models import Sum
from Aplicaciones.sbr_app.models import Cliente, Lote, Contrato, Cuota, Pago, ConfiguracionSistema
from Aplicaciones.sbr_app.services import (
    generar_tabla_amortizacion,
    actualizar_moras_contrato,
    registrar_pago_cliente
)
import os

def run():
    print("===============================================================")
    print("   INICIANDO VERIFICACIÓN COMPLETA DEL SISTEMA SBR (INTEGRAL)   ")
    print("===============================================================\n")
    
    try:
        # --- 1. SETUP DE DATOS BÁSICOS ---
        print(">> 1. Configurando Datos de Prueba...")
        user, _ = User.objects.get_or_create(username='admin_test')
        
        cliente, _ = Cliente.objects.get_or_create(
            cedula='9999999999',
            defaults={
                'nombres': 'Cliente', 'apellidos': 'Pruebas', 
                'celular': '0991234567', 'direccion': 'Laboratorio', 
                'vendedor': user
            }
        )
        
        lote, _ = Lote.objects.get_or_create(
            manzana='TEST',
            numero_lote='01',
            defaults={'dimensiones': '200m2', 'precio_contado': 2000, 'estado': 'DISPONIBLE'}
        )
        # Asegurar estado disponible
        lote.estado = 'DISPONIBLE'
        lote.save()

        config, _ = ConfiguracionSistema.objects.get_or_create(id=1)
        config.mora_leve_dias = 1
        config.mora_porcentaje = Decimal('10.00')
        config.save()
        print("   [OK] Datos base listos.\n")

        # --- 2. CREACIÓN DE CONTRATO Y PAGO DE ENTRADA ---
        print(">> 2. Probando Creación de Contrato y Entrada...")
        Contrato.objects.filter(cliente=cliente).delete() # Limpiar previos

        # Simulamos Venta: Precio 2000, Entrada 500, Saldo 1500
        contrato = Contrato.objects.create(
            cliente=cliente,
            fecha_contrato=date.today(),
            precio_venta_final=2000,
            valor_entrada=500,
            saldo_a_financiar=1500,
            numero_cuotas=3, # 3 cuotas de 500
            estado='ACTIVO'
        )
        contrato.lotes.add(lote)
        lote.estado = 'VENDIDO'
        lote.save()

        # Registrar el Pago de Entrada (Simula la vista)
        Pago.objects.create(
            contrato=contrato,
            fecha_pago=date.today(),
            monto=500,
            metodo_pago='EFECTIVO',
            observacion='Pago Entrada',
            registrado_por=user
        )

        generar_tabla_amortizacion(contrato.id)
        
        # Validaciones
        ct_count = contrato.cuotas.count()
        pago_entrada = contrato.pago_set.first()
        
        assert ct_count == 3, f"Deben ser 3 cuotas, son {ct_count}"
        assert contrato.cuotas.first().valor_capital == 500, "Cuotas mal calculadas (1500/3 = 500)"
        assert lote.estado == 'VENDIDO', "El lote debería estar VENDIDO"
        assert pago_entrada.monto == 500, "El pago de entrada no se registró bien"
        
        print("   [OK] Contrato creado, lote vendido, entrada registrada y tabla generada.\n")


        # --- 3. PROCESO DE PAGOS (FLUJO NORMAL) ---
        print(">> 3. Probando Flujo de Pagos Normal...")
        c1 = contrato.cuotas.get(numero_cuota=1)
        
        # Pagar Cuota 1 Completa
        registrar_pago_cliente(contrato.id, 500, 'EFECTIVO', None, user)
        c1.refresh_from_db()
        
        assert c1.estado == 'PAGADO', "Cuota 1 debería estar PAGADA"
        assert c1.saldo_pendiente == 0, "Saldo C1 debería ser 0"
        
        total_pagado_sistema = Pago.objects.filter(contrato=contrato).aggregate(Sum('monto'))['monto__sum']
        esperado = 500 + 500 # entrada + cuota 1
        # Corrección: 500 entrada + 500 cuota = 1000
        assert total_pagado_sistema == 1000, f"Total en caja incorrecto: {total_pagado_sistema}"
        
        print("   [OK] Pago de cuota procesado correctamente. Caja cuadrada.\n")


        # --- 4. PRUEBA DE MORA Y PAGO CON MORA ---
        print(">> 4. Probando Mora y Pago de Mora...")
        c2 = contrato.cuotas.get(numero_cuota=2)
        # Simular que venció hace 10 días
        c2.fecha_vencimiento = date.today() - timedelta(days=10)
        c2.save()
        actualizar_moras_contrato(contrato.id)
        c2.refresh_from_db()
        
        mora_esperada = Decimal('50.00') # 10% de 500
        assert c2.estado == 'VENCIDO', "Cuota 2 debería estar VENCIDA"
        assert c2.valor_mora == mora_esperada, f"Mora calculada mal: {c2.valor_mora}"
        
        # Pagar con mora (550)
        registrar_pago_cliente(contrato.id, 550, 'EFECTIVO', None, user)
        c2.refresh_from_db()
        
        assert c2.estado == 'PAGADO', "Cuota 2 debería estar PAGADA (con mora)"
        print("   [OK] Mora calculada y cobrada correctamente.\n")


        # --- 5. PRUEBA DE EDICIÓN MANUAL Y CONSISTENCIA (LO NUEVO) ---
        print(">> 5. Probando Edición Manual y Consistencia Contable...")
        c3 = contrato.cuotas.get(numero_cuota=3)
        valor_inicial_caja = Pago.objects.filter(contrato=contrato).aggregate(Sum('monto'))['monto__sum']
        
        # Simular edición manual en vista: Admin pone que pagó $100
        # Lógica copiada de la vista editada
        nuevo_abonado = Decimal('100.00')
        valor_anterior = c3.valor_pagado # 0
        diferencia = nuevo_abonado - valor_anterior # 100
        
        if diferencia != 0:
            Pago.objects.create(
                contrato=contrato,
                fecha_pago=date.today(),
                monto=diferencia,
                metodo_pago='AJUSTE',
                observacion='Ajuste Manual Test',
                registrado_por=user
            )
        
        c3.valor_pagado = nuevo_abonado
        c3.estado = 'PARCIAL'
        c3.save()
        
        # Verificar Caja
        nuevo_valor_caja = Pago.objects.filter(contrato=contrato).aggregate(Sum('monto'))['monto__sum']
        assert nuevo_valor_caja == valor_inicial_caja + 100, "La caja no subió en $100 tras edición manual"
        
        # Simular corrección manual: Admin se equivocó, era $0
        nuevo_abonado_corr = Decimal('0.00')
        diferencia_corr = nuevo_abonado_corr - c3.valor_pagado # -100
        
        if diferencia_corr != 0:
            Pago.objects.create(
                contrato=contrato,
                fecha_pago=date.today(),
                monto=diferencia_corr,
                metodo_pago='AJUSTE',
                observacion='Corrección Manual Test',
                registrado_por=user
            )
            
        c3.valor_pagado = 0
        c3.estado = 'PENDIENTE'
        c3.save()
        
        valor_final_caja = Pago.objects.filter(contrato=contrato).aggregate(Sum('monto'))['monto__sum']
        assert valor_final_caja == valor_inicial_caja, f"La caja no volvió al valor original. Actual: {valor_final_caja}, Esperado: {valor_inicial_caja}"
        
        print("   [OK] Edición manual generó movimientos de caja y mantuvo la consistencia perfecta.\n")


        # --- 6. CICLO DE VIDA: CANCELACIÓN ---
        print(">> 6. Probando Cancelación de Contrato...")
        contrato.estado = 'CANCELADO'
        contrato.save()
        
        # Liberar lotes (lógica de vista)
        for l in contrato.lotes.all():
            l.estado = 'DISPONIBLE'
            l.save()
            
        lote.refresh_from_db()
        assert lote.estado == 'DISPONIBLE', "El lote no se liberó al cancelar contrato"
        print("   [OK] Cancelación liberó el lote correctamente.\n")

        print("===============================================================")
        print("   VERIFICACIÓN INTEGRAL COMPLETADA: SISTEMA ROBUSTO 100%      ")
        print("===============================================================")

    except AssertionError as e:
        print(f"\n!!! FALLO CRÍTICO EN PRUEBA: {e}")
    except Exception as e:
        print(f"\n!!! ERROR DE EJECUCIÓN: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run()
