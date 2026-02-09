from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from .validators import validar_archivo_seguro
import bleach

class ConfiguracionSistema(models.Model):
    # Moras configurables (días y montos)
    mora_leve_dias = models.IntegerField(default=5, help_text="Días para aplicar primera mora")
    mora_leve_valor = models.DecimalField(max_digits=10, decimal_places=2, default=5.00)
    
    mora_media_dias = models.IntegerField(default=10)
    mora_media_valor = models.DecimalField(max_digits=10, decimal_places=2, default=10.00)
    
    mora_grave_dias = models.IntegerField(default=20)
    mora_grave_valor = models.DecimalField(max_digits=10, decimal_places=2, default=20.00)

    # Mora Porcentual (Nueva Lógica)
    mora_porcentaje = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=3.00, 
        help_text="Porcentaje de mora sobre el capital de la cuota (Ej: 3.00 = 3%)"
    )

    # Datos para el Contrato PDF
    nombre_empresa = models.CharField(max_length=100)
    ruc_empresa = models.CharField(max_length=13)
    logo = models.ImageField(upload_to='config/logos/', blank=True, null=True, validators=[validar_archivo_seguro])

    def __str__(self):
        return "Configuración General del Sistema"


class Perfil(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    cedula = models.CharField(max_length=13, unique=True, null=True, blank=True)
    
    # Datos Bancarios (Opcionales)
    numero_cuenta = models.CharField(max_length=20, null=True, blank=True, help_text="Número de cuenta bancaria")
    banco = models.CharField(max_length=50, null=True, blank=True, help_text="Nombre del Banco")
    tipo_cuenta = models.CharField(max_length=20, null=True, blank=True, choices=[('AHORROS', 'Ahorros'), ('CORRIENTE', 'Corriente')])

    def __str__(self):
        return f"Perfil de {self.user.username}"


class Lote(models.Model):
    ESTADOS = [
        ('DISPONIBLE', 'Disponible'),
        ('RESERVADO', 'Reservado'),
        ('VENDIDO', 'Vendido'),
    ]

    manzana = models.CharField(max_length=10)
    numero_lote = models.CharField(max_length=30)
    dimensiones = models.CharField(max_length=50, help_text="Ej: 10x20m")
    precio_contado = models.DecimalField(max_digits=12, decimal_places=2)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='DISPONIBLE')
    
    # Imagen opcional del lote
    imagen = models.ImageField(upload_to='lotes/', blank=True, null=True, help_text="Foto del lote (opcional)", validators=[validar_archivo_seguro])
    # Nueva foto específica para listados (Portada)
    foto_lista = models.ImageField(upload_to='lotes/portadas/', blank=True, null=True, help_text="Foto para mostrar en el listado (opcional)", validators=[validar_archivo_seguro])
    
    # Ubicación (Opcionales)
    ciudad = models.CharField(max_length=100, blank=True, null=True)
    parroquia = models.CharField(max_length=100, blank=True, null=True)
    provincia = models.CharField(max_length=100, blank=True, null=True)
    canton = models.CharField(max_length=100, blank=True, null=True)
    

    
    # Usuario que creó el lote (para control de permisos de edición)
    creado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='lotes_creados')
    
    # Para saber si está ocupado rápido
    def __str__(self):
        return f"Mz. {self.manzana} - Lote {self.numero_lote} ({self.estado})"


class Cliente(models.Model):
    # Relación con el vendedor (Usuario de Django)
    vendedor = models.ForeignKey(User, on_delete=models.PROTECT, related_name='mis_clientes')
    
    cedula = models.CharField(max_length=10)
    nombres = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    celular = models.CharField(max_length=15)
    email = models.EmailField(blank=True, null=True)
    direccion = models.TextField()
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Sanitización de Inputs (Bleach) - Punto 3.1
        if self.direccion:
            self.direccion = bleach.clean(self.direccion, tags=[], attributes={}, strip=True) # Elimina todo HTML
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.apellidos} {self.nombres}"


class Contrato(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    # Changed from OneToOneField to ForeignKey to allow lote reuse after cancellation/devolucion
    # DEPRECATED: Se eliminará en favor de 'lotes' (M2M)
    lote = models.ForeignKey(Lote, on_delete=models.PROTECT, null=True, blank=True)
    
    # Nuevo campo para múltiples lotes
    lotes = models.ManyToManyField(Lote, related_name='contratos', blank=True)
    
    fecha_contrato = models.DateField()
    # Fecha para reportes (cuando se cerró/canceló/devolvió)
    fecha_fin_contrato = models.DateField(null=True, blank=True)
    
    precio_venta_final = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    valor_entrada = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Este saldo inicial servirá de base. 
    # Si hay múltiples lotes, es la suma de precios - entradas.
    saldo_a_financiar = models.DecimalField(max_digits=10, decimal_places=2)
    
    numero_cuotas = models.IntegerField()
    # ESTADO: ACTIVO, CANCELADO, FINALIZADO (pagado tod), DEVOLUCION (dinero devuelto)
    ESTADOS_CONTRATO = [
        ('ACTIVO', 'Activo'),
        ('CERRADO', 'Cerrado/Finalizado'),
        ('ANULADO', 'Anulado'),
        ('CANCELADO', 'Cancelado'),
        ('DEVOLUCION', 'Devolución'),
    ]
    estado = models.CharField(max_length=20, choices=ESTADOS_CONTRATO, default='ACTIVO')
    
    observacion = models.TextField(blank=True, null=True)
    archivo_contrato_pdf = models.FileField(upload_to='contratos_pdfs/', blank=True, null=True)
    
    # Bandera para saber si está en mora actualmente (calculado)
    esta_en_mora = models.BooleanField(default=False)

    def __str__(self):
        return f"Contrato #{self.id} - {self.cliente}"

    @property
    def lote_principal(self):
        """Devuelve el primer lote asociado para compatibilidad."""
        return self.lotes.first() or self.lote

    @property
    def lotes_display(self):
        """String concatenado de los lotes: 'Mz A - 1, 2'"""
        lotes_qs = self.lotes.all()
        if not lotes_qs.exists() and self.lote:
            return f"Mz {self.lote.manzana} - Lote {self.lote.numero_lote}"
            
        # Agrupar por Manzana
        grupos = {}
        for l in lotes_qs:
            if l.manzana not in grupos:
                grupos[l.manzana] = []
            grupos[l.manzana].append(str(l.numero_lote))
        
        textos = []
        for mz, nums in grupos.items():
            nums_str = ", ".join(nums)
            textos.append(f"Mz {mz} - Lote(s) {nums_str}")
            
        return " / ".join(textos) if textos else "Sin Lote"

    @property
    def manzanas_str(self):
        """Devuelve string de manzanas unicas: 'A, B'"""
        lotes_qs = self.lotes.all()
        if not lotes_qs.exists() and self.lote:
            return str(self.lote.manzana)
        mzs = sorted(list(set(l.manzana for l in lotes_qs)))
        return ", ".join(mzs)

    @property
    def numeros_lotes_str(self):
        """Devuelve string de numeros de lote: '1, 2, 5'"""
        lotes_qs = self.lotes.all()
        if not lotes_qs.exists() and self.lote:
            return str(self.lote.numero_lote)
        
        # Opcional: mostrar 'Mz A: 1, 2 / Mz B: 5' si hay mezcla compleja
        # Para simplificar en columnas separadas, solo listamos números
        # Si queremos ser precisos cuando hay multiple manzanas, lo mejor es el lotes_display general.
        # Pero intentaremos listar todos los números.
        nums = sorted([str(l.numero_lote) for l in lotes_qs], key=lambda x: int(x) if x.isdigit() else x)
        return ", ".join(nums)


class Cuota(models.Model):
    ESTADOS_PAGO = [
        ('PENDIENTE', 'Pendiente'),
        ('PARCIAL', 'Pago Parcial'),
        ('PAGADO', 'Pagado'),
        ('VENCIDO', 'Vencido/Mora'),
    ]

    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name='cuotas')
    numero_cuota = models.IntegerField()
    fecha_vencimiento = models.DateField()
    
    # Valores Económicos
    valor_capital = models.DecimalField(max_digits=10, decimal_places=2) # La cuota base (ej: $180)
    valor_mora = models.DecimalField(max_digits=10, decimal_places=2, default=0) # (ej: $10)
    
    # Control de pagos
    valor_pagado = models.DecimalField(max_digits=10, decimal_places=2, default=0) # Cuánto han abonado a esta cuota
    estado = models.CharField(max_length=20, choices=ESTADOS_PAGO, default='PENDIENTE')
    
    # Control manual de mora
    mora_exenta = models.BooleanField(default=False, help_text="Si está marcado, esta cuota NO tendrá mora aunque esté vencida")
    
    fecha_ultimo_pago = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['numero_cuota'] # Ordenar cronológicamente

    def __str__(self):
        return f"Cuota {self.numero_cuota} - {self.contrato}"

    @property
    def total_a_pagar(self):
        capital = self.valor_capital or 0
        mora = self.valor_mora or 0
        return capital + mora
        
    @property
    def saldo_pendiente(self):
        from decimal import Decimal
        capital = self.valor_capital or Decimal('0')
        mora = self.valor_mora or Decimal('0')
        pagado = self.valor_pagado or Decimal('0')
        resultado = (capital + mora) - pagado
        # Treat sub-cent values as zero (precision tolerance)
        if resultado < Decimal('0.01'):
            return Decimal('0.00')
        return resultado


class Pago(models.Model):
    METODOS = [
        ('EFECTIVO', 'Efectivo'),
        ('TRANSFERENCIA', 'Transferencia/Depósito'),
    ]

    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE)
    fecha_pago = models.DateField()
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    metodo_pago = models.CharField(max_length=20, choices=METODOS)
    
    # Evidencia (Obligatorio por validación si es Transferencia)
    comprobante_imagen = models.FileField(upload_to='pagos/comprobantes/', blank=True, null=True, validators=[validar_archivo_seguro])
    
    observacion = models.TextField(blank=True, null=True)
    registrado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True) # Auditoría

    def save(self, *args, **kwargs):
        # Sanitización de Inputs (Bleach)
        if self.observacion:
            self.observacion = bleach.clean(self.observacion, tags=[], attributes={}, strip=True)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Pago ${self.monto} - {self.contrato}"
class LogActividad(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    accion = models.CharField(max_length=255) # Ej: "Login Exitoso", "Vio CV de Juan"
    detalle = models.TextField(blank=True, null=True) # JSON o texto extra
    fecha = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    def __str__(self):
        return f"[{self.fecha.strftime('%Y-%m-%d %H:%M')}] {self.usuario} - {self.accion}"

    class Meta:
        verbose_name = "Log de Actividad"
        verbose_name_plural = "Logs de Actividad"
        ordering = ['-fecha']
