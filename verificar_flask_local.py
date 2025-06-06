#!/usr/bin/env python3
"""
Script para verificar que Flask puede iniciarse correctamente en el entorno local.
Ejecutar desde el directorio raíz del proyecto.
"""

def main():
    print("🔍 VERIFICACIÓN: Flask + Imports + Librerías")
    print("=" * 50)
    
    # Verificar Flask
    try:
        import flask
        print("✅ Flask disponible")
    except ImportError:
        print("❌ Flask no encontrado")
        return
    
    # Verificar que se puede importar la app
    try:
        from myapp import create_app
        print("✅ Módulo myapp importable")
    except ImportError as e:
        print(f"❌ Error importando myapp: {e}")
        return
    
    # Verificar creación de app
    try:
        app = create_app()
        print("✅ App Flask creada correctamente")
        print("✅ Blueprint reports registrado")
    except Exception as e:
        print(f"❌ Error creando app: {e}")
        return
    
    # Verificar librerías opcionales
    print("\n📦 VERIFICANDO LIBRERÍAS OPCIONALES:")
    
    try:
        import xlsxwriter
        print("✅ xlsxwriter disponible - Exportación Excel funcionará")
    except ImportError:
        print("⚠️  xlsxwriter no disponible - Instalar con: pip install xlsxwriter")
    
    try:
        import reportlab
        print("✅ reportlab disponible - Generación PDF funcionará")
    except ImportError:
        print("⚠️  reportlab no disponible - Instalar con: pip install reportlab")
    
    # Verificar rutas de reports
    try:
        with app.app_context():
            from flask import url_for
            url_index = url_for('reports_bp.index')
            url_excel = url_for('reports_bp.exportar_facturas_excel')
            url_listado = url_for('reports_bp.listado_facturacion')
            print("\n🔗 RUTAS REGISTRADAS:")
            print(f"✅ {url_index}")
            print(f"✅ {url_excel}")
            print(f"✅ {url_listado}")
    except Exception as e:
        print(f"❌ Error con rutas: {e}")
        return
    
    print("\n🎉 ¡VERIFICACIÓN COMPLETADA!")
    print("Flask debería iniciarse correctamente con: flask run")
    print("\nSi faltan librerías, instálalas con:")
    print("pip install xlsxwriter reportlab")

if __name__ == "__main__":
    main()
