#!/usr/bin/env python3
"""
Test rápido para verificar que Flask puede iniciarse sin errores.
"""

def test_flask_startup():
    print("🔍 Probando startup de Flask...")
    
    try:
        # Importar y crear la app
        from myapp import create_app
        app = create_app()
        
        print("✅ App Flask creada exitosamente")
        
        # Verificar rutas de reports
        with app.app_context():
            from flask import url_for
            
            # Probar las rutas principales
            routes_to_test = [
                ('reports_bp.index', {}),
                ('reports_bp.exportar_facturas_excel', {}),
                ('reports_bp.listado_facturacion', {}),
                ('reports_bp.generar_pdf_facturacion', {})
            ]
            
            print("✅ Verificando rutas del blueprint reports:")
            for route_name, kwargs in routes_to_test:
                try:
                    url = url_for(route_name, **kwargs)
                    print(f"  ✅ {route_name} → {url}")
                except Exception as e:
                    print(f"  ❌ {route_name} → Error: {e}")
                    return False
        
        print("\n🎉 ¡Flask puede iniciarse correctamente!")
        print("Puedes ejecutar: flask run")
        return True
        
    except Exception as e:
        print(f"❌ Error al crear la app: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_flask_startup()
    if not success:
        print("\n💡 Sugerencias:")
        print("1. Verifica que estés en el directorio correcto")
        print("2. Verifica que el entorno virtual esté activado")
        print("3. Revisa los imports en reports.py")
