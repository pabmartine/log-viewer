#!/usr/bin/env python3

print("Verificando dependencias...")

try:
    import gi
    print("✓ gi (PyGObject) disponible")
except ImportError as e:
    print("✗ gi (PyGObject) NO disponible:", e)
    exit(1)

try:
    gi.require_version('Gtk', '4.0')
    print("✓ Gtk 4.0 disponible")
except Exception as e:
    print("✗ Gtk 4.0 NO disponible:", e)
    exit(1)

try:
    gi.require_version('Adw', '1')
    print("✓ Adw 1.0 disponible")
except Exception as e:
    print("✗ Adw 1.0 NO disponible:", e)
    exit(1)

try:
    from gi.repository import Gtk, Adw, Gio, GLib, Pango, Gdk
    print("✓ Todos los módulos importados correctamente")
except ImportError as e:
    print("✗ Error importando módulos:", e)
    exit(1)

print("\n¡Todas las dependencias están disponibles!")
print("El problema podría ser otro...")

# Prueba básica de aplicación
try:
    class TestApp(Adw.Application):
        def __init__(self):
            super().__init__(application_id="com.test.App")
            self.connect('activate', self.on_activate)
        
        def on_activate(self, app):
            win = Adw.ApplicationWindow(application=app)
            win.set_title("Test")
            win.set_default_size(300, 200)
            win.present()
    
    print("Creando aplicación de prueba...")
    app = TestApp()
    print("Aplicación creada, intentando ejecutar...")
    app.run([])
    
except Exception as e:
    print("✗ Error creando aplicación:", e)
    import traceback
    traceback.print_exc()