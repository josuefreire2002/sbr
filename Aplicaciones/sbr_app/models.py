from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator

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
    logo = models.ImageField(upload_to='config/logos/', blank=True, null=True)

    def __str__(self):
        return "Configuración General del Sistema"


class Perfil(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    cedula = models.CharField(max_length=13, unique=True, null=True, blank=True)

    def __str__(self):
        return f"Perfil de {self.user.username}"


class Lote(models.Model):
    ESTADOS = [
        ('DISPONIBLE', 'Disponible'),
        ('RESERVADO', 'Reservado'),
        ('VENDIDO', 'Vendido'),
    ]

    manzana = models.CharField(max_length=10)
    numero_lote = models.CharField(max_length=10)
    dimensiones = models.CharField(max_length=50, help_text="Ej: 10x20m")
    precio_contado = models.DecimalField(max_digits=12, decimal_places=2)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='DISPONIBLE')
    
    # Imagen opcional del lote
    imagen = models.ImageField(upload_to='lotes/', blank=True, null=True, help_text="Foto del lote (opcional)")
    
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

    def __str__(self):
        return f"{self.apellidos} {self.nombres}"


class Contrato(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    # Changed from OneToOneField to ForeignKey to allow lote reuse after cancellation/devolucion
    lote = models.ForeignKey(Lote, on_delete=models.PROTECT)
    
    fecha_contrato = models.DateField()
    # Fecha para reportes (cuando se cerró/canceló/devolvió)
    fecha_cancelacion = models.DateField(null=True, blank=True) 
    
    archivo_contrato_pdf = models.FileField(upload_to='contratos/', blank=True, null=True)
    
    # Datos financieros congelados al momento de la venta
    precio_venta_final = models.DecimalField(max_digits=12, decimal_places=2)
    valor_entrada = models.DecimalField(max_digits=12, decimal_places=2)
    saldo_a_financiar = models.DecimalField(max_digits=12, decimal_places=2)
    numero_cuotas = models.IntegerField()
    
    
    # Campo calculado para facilitar reportes
    esta_en_mora = models.BooleanField(default=False) 

    ESTADOS_CONTRATO = [
        ('ACTIVO', 'Activo'),
        ('CERRADO', 'Cerrado/Finalizado'),
        ('ANULADO', 'Anulado'),
        ('CANCELADO', 'Cancelado'),
        ('DEVOLUCION', 'Devolución'),
    ]
    estado = models.CharField(max_length=20, choices=ESTADOS_CONTRATO, default='ACTIVO')

    def __str__(self):
        return f"Contrato #{self.id} - {self.cliente}"


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
    comprobante_imagen = models.FileField(upload_to='pagos/comprobantes/', blank=True, null=True)
    
    observacion = models.TextField(blank=True, null=True)
    registrado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True) # Auditoría

    def __str__(self):
        return f"Pago ${self.monto} - {self.contrato}"