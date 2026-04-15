from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import ConfiguracionSistema, Lote, Cliente, Contrato, Cuota, Pago, Perfil

class PerfilInline(admin.StackedInline):
    model = Perfil
    can_delete = False
    verbose_name_plural = 'Perfil de Usuario (Cédula)'

class UserAdmin(BaseUserAdmin):
    inlines = (PerfilInline,)

# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)

# 1. Configuración del Sistema (Para las reglas de Mora)
@admin.register(ConfiguracionSistema)
class ConfiguracionAdmin(admin.ModelAdmin):
    list_display = ('nombre_empresa', 'ruc_empresa', 'mora_porcentaje')
    list_editable = ('mora_porcentaje',)
    # Esto evita que creen más de una configuración (Solo debe haber 1)
    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return True

# 2. Lotes (Inventario)
@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    list_display = ('manzana', 'numero_lote', 'dimensiones', 'precio_contado', 'estado')
    list_filter = ('estado', 'manzana')
    search_fields = ('manzana', 'numero_lote')
    list_editable = ('precio_contado', 'estado') # Permite editar rápido desde la lista

# 3. Clientes
@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('apellidos', 'nombres', 'cedula', 'celular', 'vendedor')
    search_fields = ('cedula', 'apellidos', 'nombres')
    list_filter = ('vendedor',)

# 4. Cuotas (Para verlas dentro del contrato - solo como Inline, no registro standalone por bug Jazzmin/Django 6)
class CuotaInline(admin.TabularInline):
    model = Cuota
    extra = 0
    # Enable editing and deletion within contract
    can_delete = True

# 5. Contratos
@admin.action(description='Resetear pagos de cuotas (Dejar solo Entrada inicial)')
def resetear_pagos_contrato(modeladmin, request, queryset):
    from .services import recalcular_deuda_contrato
    from .models import Pago
    
    count = 0
    for contrato in queryset:
        # 1. Identificar el Abono Inicial (El primer pago cronológico, o el marcado como entrada)
        # Asumimos que es_entrada=True lo identifica perfectamente, si no, el primer pago.
        pagos_a_borrar = Pago.objects.filter(contrato=contrato).exclude(es_entrada=True)
        
        # Eliminar físicamente los recibos (DetallePago se va en cascada)
        if pagos_a_borrar.exists():
            pagos_a_borrar.delete()
            
            # Recalcular matemáticamente las cuotas a 0 (excepto la entrada, que no afecta a las cuotas regulares)
            recalcular_deuda_contrato(contrato.id)
            count += 1
            
    modeladmin.message_user(request, f"Se han reseteado a $0.00 las cuotas regulares de {count} contrato(s).")

@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ('id', 'cliente', 'lote', 'fecha_contrato', 'saldo_a_financiar', 'esta_en_mora')
    list_filter = ('esta_en_mora', 'fecha_contrato')
    search_fields = ('cliente__cedula', 'cliente__apellidos')
    inlines = [CuotaInline] # Muestra las cuotas ahí mismo
    actions = [resetear_pagos_contrato]

# 6. Cuotas (Standalone Registration for Deep Intervention)
@admin.register(Cuota)
class CuotaAdmin(admin.ModelAdmin):
    list_display = ('contrato', 'numero_cuota', 'fecha_vencimiento', 'valor_capital', 'valor_mora', 'valor_pagado', 'estado', 'mora_exenta')
    list_filter = ('estado', 'mora_exenta')
    search_fields = ('contrato__cliente__nombres', 'contrato__cliente__apellidos', 'contrato__id')
    list_editable = ('valor_mora', 'valor_pagado', 'estado', 'mora_exenta')
    
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .services import recalcular_deuda_contrato
        recalcular_deuda_contrato(obj.contrato.id)
        
    def delete_model(self, request, obj):
        contrato_id = obj.contrato.id
        super().delete_model(request, obj)
        from .services import recalcular_deuda_contrato
        recalcular_deuda_contrato(contrato_id)

# 7. Pagos
@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('fecha_pago', 'contrato', 'monto', 'metodo_pago', 'registrado_por')
    list_filter = ('metodo_pago', 'fecha_pago')
    search_fields = (
        'contrato__cliente__nombres', 
        'contrato__cliente__apellidos', 
        'contrato__cliente__cedula',
        'contrato__id'
    )
    date_hierarchy = 'fecha_pago'

    def save_model(self, request, obj, form, change):
        """
        Al guardar un pago desde el admin (crear o editar),
        recalculamos toda la deuda del contrato para mantener consistencia.
        """
        super().save_model(request, obj, form, change)
        from .services import recalcular_deuda_contrato
        recalcular_deuda_contrato(obj.contrato.id)

    def delete_model(self, request, obj):
        """
        Al eliminar un pago desde el admin,
        recalculamos la deuda para deshacer el efecto del pago borrado.
        """
        contrato_id = obj.contrato.id
        super().delete_model(request, obj)
        from .services import recalcular_deuda_contrato
        recalcular_deuda_contrato(contrato_id)
from django.contrib import admin
from .models import *

# Registramos tus modelos existentes si no lo están
# (Asumo que ya tienes algunos, pero agrego LogActividad explícitamente)

# 8. Detalles de Pago (Para corregir distribuciones defectuosas manualmente)
@admin.register(DetallePago)
class DetallePagoAdmin(admin.ModelAdmin):
    list_display = ('pago', 'cuota', 'monto_aplicado')
    search_fields = ('pago__contrato__cliente__apellidos', 'pago__id')
    
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .services import recalcular_deuda_contrato
        recalcular_deuda_contrato(obj.pago.contrato.id)
        
    def delete_model(self, request, obj):
        contrato_id = obj.pago.contrato.id
        super().delete_model(request, obj)
        from .services import recalcular_deuda_contrato
        recalcular_deuda_contrato(contrato_id)

@admin.register(LogActividad)
class LogActividadAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'usuario', 'accion', 'ip_address')
    list_filter = ('accion', 'fecha', 'usuario')
    search_fields = ('usuario__username', 'accion', 'detalle', 'ip_address')
    readonly_fields = ('fecha', 'usuario', 'accion', 'detalle', 'ip_address')

    def has_add_permission(self, request):
        return False # Nadie puede crear logs manuales, solo el sistema

    def has_change_permission(self, request, obj=None):
        return False # Nadie puede editar logs (Integridad)
        
    def has_delete_permission(self, request, obj=None):
        return False # Nadie puede borrar logs (Integridad)
