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
    list_display = ('nombre_empresa', 'ruc_empresa', 'mora_leve_dias', 'mora_grave_dias')
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

# 4. Cuotas (Para verlas dentro del contrato)
class CuotaInline(admin.TabularInline):
    model = Cuota
    extra = 0
    readonly_fields = ('saldo_pendiente', 'total_a_pagar')
    can_delete = False

# 5. Contratos
@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ('id', 'cliente', 'lote', 'fecha_contrato', 'saldo_a_financiar', 'esta_en_mora')
    list_filter = ('esta_en_mora', 'fecha_contrato')
    search_fields = ('cliente__cedula', 'cliente__apellidos')
    inlines = [CuotaInline] # Muestra las cuotas ahí mismo

# 6. Pagos
@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('fecha_pago', 'contrato', 'monto', 'metodo_pago', 'registrado_por')
    list_filter = ('metodo_pago', 'fecha_pago')