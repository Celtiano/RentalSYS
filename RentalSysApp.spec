# RentalSysApp.spec
# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Definir la ruta base del proyecto
project_root = os.getcwd() 
app_folder = os.path.join(project_root, 'myapp')
static_folder = os.path.join(app_folder, 'static') # Definir static_folder

a = Analysis(
    ['run_prod.py'], 
    pathex=[project_root], 
    binaries=[],
    datas=[
        (os.path.join(app_folder, 'templates'), 'myapp/templates'),
		(os.path.join(app_folder, 'static'), 'myapp/static'), # Asegúrate que static se copia correctamente
		('.env', '.'),
    ],
    hiddenimports=[
        'waitress', # Si usas waitress
        'sqlalchemy.dialects.sqlite', # Importante para SQLite
        'flask_wtf.csrf',
        'flask_login',
		'python_dotenv', # O 'dotenv' dependiendo de cómo se registre
        'flask_mail',
        'jinja2.ext', # A veces necesario
        'pkg_resources.py2_warn', # A veces necesario para evitar warnings
        'fdb',
		# Añade aquí otros módulos que PyInstaller no detecte automáticamente
        # Ej: 'babel.numbers', 'your_custom_module'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Recolectar archivos de datos de paquetes (ej. plantillas de extensiones)
# datas += collect_data_files('flask_wtf') # Si Flask-WTF tiene templates propios (raro)
# datas += collect_data_files('flask_login') # Si Flask-Login tiene templates (raro)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
icon_path = os.path.join(static_folder, 'images', 'rentalsys_icon_1024.ico')

if not os.path.exists(icon_path):
    print(f"ADVERTENCIA: Archivo de icono no encontrado en {icon_path}. Se usará el icono por defecto.")
    icon_path = None # Opcional: o deja que PyInstaller falle si el icono es obligatorio

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas, # Asegúrate de que 'datas' esté aquí
    [],
    name='RentalSysApp',
    debug='imports',
    bootloader_ignore_signals=False,
    strip=False,
    upx=True, # Comprime el ejecutable (opcional)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # False si usas --windowed y un servidor como waitress
                  # True si usas app.run() y quieres ver la consola de Flask
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
	icon=icon_path
    #icon='path/to/your/icon.ico' # Opcional: ruta a un archivo .ico
)