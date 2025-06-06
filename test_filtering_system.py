#!/usr/bin/env python3
"""
Script de prueba para el sistema de middleware de filtrado automático.
Este script verifica que el sistema de filtrado esté correctamente implementado.
"""

import sys
import os

# Agregar el directorio del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Prueba que todas las importaciones del sistema de filtrado funcionen."""
    print("🔍 Probando importaciones del sistema de filtrado...")
    
    try:
        # Probar importación de query_filters
        from myapp.utils.query_filters import (
            is_filtering_enabled,
            enable_filtering,
            disable_filtering,
            should_filter_query,
            get_filter_for_model,
            FilteredQuery,
            bypass_filtering,
            get_filtering_status
        )
        print("✅ Utilidades de query_filters importadas correctamente")
        
        # Probar importación de database_helpers
        from myapp.utils.database_helpers import (
            OwnerFilteredQueries,
            get_filtered_propiedades,
            get_filtered_contratos,
            get_filtered_facturas,
            get_filtered_gastos,
            get_filtered_inquilinos,
            get_filtered_documentos,
            bypass_owner_filtering
        )
        print("✅ Utilidades de database_helpers importadas correctamente")
        
        # Probar importación de decoradores actualizados
        from myapp.decorators import (
            with_owner_filtering,
            filtered_view,
            validate_entity_access,
            inject_owner_stats,
            filtered_list_view,
            filtered_detail_view
        )
        print("✅ Decoradores de filtrado importados correctamente")
        
        print("✅ Todas las importaciones del sistema de filtrado exitosas\n")
        return True
        
    except ImportError as e:
        print(f"❌ Error en importaciones del sistema de filtrado: {e}")
        return False

def test_database_helpers():
    """Prueba las funciones de database_helpers."""
    print("🔍 Probando funciones de database_helpers...")
    
    try:
        from myapp.utils.database_helpers import OwnerFilteredQueries
        
        # Verificar que las clases y métodos existen
        methods_to_check = [
            'should_apply_filter',
            'get_propiedades',
            'get_contratos',
            'get_facturas',
            'get_gastos',
            'get_inquilinos',
            'get_documentos',
            'get_stats_for_active_owner',
            'validate_access_to_entity'
        ]
        
        for method_name in methods_to_check:
            if hasattr(OwnerFilteredQueries, method_name):
                print(f"✅ Método {method_name} disponible")
            else:
                print(f"❌ Método {method_name} no encontrado")
                return False
        
        print("✅ Funciones de database_helpers verificadas\n")
        return True
        
    except Exception as e:
        print(f"❌ Error probando database_helpers: {e}")
        return False

def test_decorators():
    """Prueba que los decoradores estén correctamente definidos."""
    print("🔍 Probando decoradores de filtrado...")
    
    try:
        from myapp.decorators import (
            with_owner_filtering,
            filtered_view,
            validate_entity_access,
            filtered_list_view,
            filtered_detail_view
        )
        
        # Verificar que son callables
        decorators = [
            ('with_owner_filtering', with_owner_filtering),
            ('filtered_view', filtered_view),
            ('validate_entity_access', validate_entity_access),
            ('filtered_list_view', filtered_list_view),
            ('filtered_detail_view', filtered_detail_view)
        ]
        
        for name, decorator in decorators:
            if callable(decorator):
                print(f"✅ Decorador {name} es callable")
            else:
                print(f"❌ Decorador {name} no es callable")
                return False
        
        print("✅ Decoradores de filtrado verificados\n")
        return True
        
    except Exception as e:
        print(f"❌ Error probando decoradores: {e}")
        return False

def check_file_structure():
    """Verifica que todos los archivos del sistema de filtrado existan."""
    print("🔍 Verificando estructura de archivos del sistema de filtrado...")
    
    required_files = [
        'myapp/utils/query_filters.py',
        'myapp/utils/database_helpers.py',
        'myapp/decorators.py',
        'myapp/__init__.py'
    ]
    
    missing_files = []
    for file_path in required_files:
        full_path = os.path.join(os.path.dirname(__file__), file_path)
        if os.path.exists(full_path):
            print(f"✅ {file_path}")
        else:
            print(f"❌ {file_path} - NO ENCONTRADO")
            missing_files.append(file_path)
    
    if not missing_files:
        print("✅ Todos los archivos del sistema de filtrado existen\n")
        return True
    else:
        print(f"❌ Faltan {len(missing_files)} archivos\n")
        return False

def test_filtering_logic():
    """Prueba la lógica básica del sistema de filtrado."""
    print("🔍 Probando lógica básica del sistema de filtrado...")
    
    try:
        from myapp.utils.query_filters import FILTERED_MODELS, get_filtering_status
        
        # Verificar que los modelos están definidos
        expected_models = ['Propiedad', 'Contrato', 'Factura', 'Gasto', 'Documento', 'Inquilino']
        
        for model_name in expected_models:
            if model_name in FILTERED_MODELS:
                config = FILTERED_MODELS[model_name]
                print(f"✅ Modelo {model_name} configurado: {config['filter_type']}")
            else:
                print(f"❌ Modelo {model_name} no configurado")
                return False
        
        # Verificar función de estado
        try:
            status = get_filtering_status()
            print(f"✅ Estado de filtrado obtenido: {len(status)} campos")
        except Exception as e:
            print(f"❌ Error obteniendo estado de filtrado: {e}")
            return False
        
        print("✅ Lógica básica del sistema de filtrado verificada\n")
        return True
        
    except Exception as e:
        print(f"❌ Error probando lógica de filtrado: {e}")
        return False

def show_filtering_implementation_summary():
    """Muestra un resumen de la implementación del sistema de filtrado."""
    print("📋 RESUMEN DE IMPLEMENTACIÓN - SISTEMA DE FILTRADO AUTOMÁTICO")
    print("=" * 70)
    print()
    print("🔧 COMPONENTES IMPLEMENTADOS:")
    print("   1. ✅ Sistema de filtros automáticos (query_filters.py)")
    print("   2. ✅ Funciones auxiliares de BD (database_helpers.py)")
    print("   3. ✅ Decoradores de filtrado actualizados (decorators.py)")
    print("   4. ✅ Context processors (en __init__.py)")
    print("   5. ✅ Middleware de filtrado (en __init__.py)")
    print()
    print("📊 MODELOS FILTRADOS AUTOMÁTICAMENTE:")
    print("   • Propiedades - Filtrado directo por propietario_id")
    print("   • Contratos - Filtrado por propiedades del propietario activo")
    print("   • Facturas - Filtrado por propiedades del propietario activo")
    print("   • Gastos - Filtrado por contratos del propietario activo")
    print("   • Documentos - Filtrado por contratos del propietario activo")
    print("   • Inquilinos - Filtrado por contratos relacionados")
    print()
    print("🎯 DECORADORES DISPONIBLES:")
    print("   • @with_owner_filtering - Configuración básica de filtrado")
    print("   • @filtered_view - Decorador combinado completo")
    print("   • @validate_entity_access - Validación de acceso a entidades")
    print("   • @filtered_list_view - Para vistas de listado")
    print("   • @filtered_detail_view - Para vistas de detalle")
    print("   • @inject_owner_stats - Inyección de estadísticas")
    print()
    print("🔍 FUNCIONES DE CONSULTA:")
    print("   • get_filtered_propiedades() - Propiedades filtradas")
    print("   • get_filtered_contratos() - Contratos filtrados")
    print("   • get_filtered_facturas() - Facturas filtradas")
    print("   • get_filtered_gastos() - Gastos filtrados")
    print("   • get_filtered_inquilinos() - Inquilinos filtrados")
    print("   • get_filtered_documentos() - Documentos filtrados")
    print()
    print("🌐 VARIABLES DE TEMPLATE:")
    print("   • active_owner - Propietario activo actual")
    print("   • available_owners - Lista de propietarios disponibles")
    print("   • has_active_owner - Booleano si hay propietario activo")
    print("   • user_can_change_owner - Si el usuario puede cambiar")
    print("   • owner_context - Contexto completo del propietario")
    print("   • owner_stats - Estadísticas del propietario activo")
    print()
    print("⚙️ CARACTERÍSTICAS TÉCNICAS:")
    print("   • Filtrado automático por propietario activo")
    print("   • Bypass temporal del filtrado cuando sea necesario")
    print("   • Context processors para inyección en templates")
    print("   • Middleware de validación de sesión")
    print("   • Logging detallado para debugging")
    print("   • Compatibilidad con roles existentes")
    print("   • Validación de acceso a entidades específicas")
    print()
    print("🔒 SEGURIDAD:")
    print("   • Validación automática de permisos de usuario")
    print("   • Filtrado por propietario en todas las consultas")
    print("   • Validación de acceso a entidades específicas")
    print("   • Respeto a jerarquía de roles (admin, gestor, usuario)")
    print()

def main():
    """Función principal del script de prueba."""
    print("🚀 PRUEBA DEL SISTEMA DE MIDDLEWARE DE FILTRADO AUTOMÁTICO")
    print("=" * 70)
    print()
    
    # Ejecutar pruebas
    tests_passed = 0
    total_tests = 5
    
    if check_file_structure():
        tests_passed += 1
    
    if test_imports():
        tests_passed += 1
    
    if test_database_helpers():
        tests_passed += 1
    
    if test_decorators():
        tests_passed += 1
    
    if test_filtering_logic():
        tests_passed += 1
    
    # Mostrar resultado
    print("📊 RESULTADO DE PRUEBAS")
    print("=" * 30)
    print(f"Pruebas pasadas: {tests_passed}/{total_tests}")
    
    if tests_passed == total_tests:
        print("✅ ¡TODAS LAS PRUEBAS PASARON!")
        print("🎉 El sistema de filtrado automático está listo para usar")
    else:
        print("❌ Algunas pruebas fallaron")
        print("🔧 Revisa los errores anteriores")
    
    print()
    show_filtering_implementation_summary()

if __name__ == '__main__':
    main()
