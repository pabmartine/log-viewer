#!/usr/bin/env python3

import sys
import os
import re
import threading
import locale
import gettext
import json
import tempfile
import subprocess
import time
from pathlib import Path
import gi

# Especificar las versiones de las librerías
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib, Pango, Gdk

# Configuración de internacionalización
LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locale")
DOMAIN = "log-viewer"

# Archivo de configuración
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "log-viewer")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Función para configurar el idioma
def setup_locale(language=None):
    """Configurar el idioma de la aplicación"""
    if language:
        os.environ["LANGUAGE"] = language
        os.environ["LC_ALL"] = language

    # Intentar configurar el locale del sistema
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    # Configurar gettext
    try:
        lang_translations = gettext.translation(DOMAIN, LOCALE_DIR, fallback=True)
        lang_translations.install()
        return lang_translations.gettext
    except Exception:
        # Fallback a inglés si hay problemas
        return lambda text: text

# Configurar idioma inicial (detectar automáticamente)
_ = setup_locale()

class ConfigManager:
    """Gestor de configuración persistente"""

    def __init__(self):
        self.config_file = CONFIG_FILE
        self.default_config = {
            "language": "auto",
            "dark_theme": False,
            "print_font_size": 10,
            "print_margin": 20,
            "window_width": 1000,
            "window_height": 700,
            "sidebar_visible": False,
            "follow_logs": False,  # Nueva opción para seguimiento
        }
        self.config = self.load_config()

    def load_config(self):
        """Cargar configuración desde archivo"""
        try:
            # Crear directorio si no existe
            os.makedirs(CONFIG_DIR, exist_ok=True)

            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                    # Combinar con valores por defecto para nuevas opciones
                    config = self.default_config.copy()
                    config.update(loaded_config)
                    return config
        except Exception as e:
            print(f"Error loading config: {e}")

        return self.default_config.copy()

    def save_config(self):
        """Guardar configuración a archivo"""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key, default=None):
        """Obtener valor de configuración"""
        return self.config.get(key, default)

    def set(self, key, value):
        """Establecer valor de configuración"""
        self.config[key] = value
        self.save_config()

class LogViewerWindow(Gtk.ApplicationWindow):
    """Ventana principal del visor de logs"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Gestor de configuración
        self.config = ConfigManager()

        # Variables de estado
        self.current_file = None
        self.original_content = ""
        self.filtered_content = ""
        self.search_text = ""
        self.highlight_rules = []
        self.sidebar_visible = self.config.get("sidebar_visible", False)
        self.current_language = self.config.get("language", "auto")

        # Variables para el seguimiento de archivos
        self.follow_enabled = self.config.get("follow_logs", False)
        self.follow_thread = None
        self.follow_active = False
        self.last_file_size = 0
        self.follow_lock = threading.Lock()

        # Variables para la búsqueda
        self.search_matches = []
        self.current_search_index = -1
        self.search_tag = None

        # Variables para pestañas de filtros
        self.filter_tabs = {}  # ID -> contenido filtrado
        self.next_filter_id = 1

        # Variables para resaltado por pestaña
        self.tab_highlights = {}  # page_num -> lista de resaltados
        self.highlight_counter = 0

        # Aplicar configuración guardada
        self.apply_saved_config()

        # Configurar la ventana
        self.set_title(_("Log Viewer"))
        window_width = self.config.get("window_width", 1000)
        window_height = self.config.get("window_height", 700)
        self.set_default_size(window_width, window_height)

        # Crear la interfaz
        self.setup_ui()
        self.setup_shortcuts()

        # Conectar eventos de ventana para guardar configuración
        self.connect("close-request", self.on_window_close)

    def apply_saved_config(self):
        """Aplicar configuración guardada"""
        # Aplicar idioma guardado
        saved_language = self.config.get("language", "auto")
        if saved_language != "auto":
            global _
            _ = setup_locale(saved_language)

        # Aplicar tema oscuro
        dark_theme = self.config.get("dark_theme", False)
        self.apply_theme(dark_theme)

    def apply_theme(self, dark_theme):
        """Aplicar tema claro/oscuro"""
        style_manager = Adw.StyleManager.get_default()
        if dark_theme:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def on_window_close(self, window):
        """Guardar configuración al cerrar ventana"""
        # Detener seguimiento si está activo
        self.stop_following()
        
        # Guardar tamaño de ventana
        width, height = self.get_default_size()
        self.config.set("window_width", width)
        self.config.set("window_height", height)

        # Guardar estado del sidebar
        self.config.set("sidebar_visible", self.sidebar_visible)

        return False  # Permitir que la ventana se cierre

    def change_language(self, language_code):
        """Cambiar el idioma de la aplicación"""
        global _

        if language_code == "auto":
            _ = setup_locale()
        else:
            _ = setup_locale(language_code)

        # Guardar preferencia de idioma
        self.config.set("language", language_code)
        self.current_language = language_code

        # Recrear la interfaz con los nuevos textos
        self.recreate_ui()

    def change_theme(self, dark_theme):
        """Cambiar tema de la aplicación"""
        self.apply_theme(dark_theme)
        self.config.set("dark_theme", dark_theme)

    def change_follow_logs(self, follow_enabled):
        """Cambiar configuración de seguimiento de logs"""
        self.follow_enabled = follow_enabled
        self.config.set("follow_logs", follow_enabled)
        
        # Si hay un archivo cargado, aplicar el cambio inmediatamente
        if self.current_file:
            if follow_enabled:
                self.start_following()
            else:
                self.stop_following()

    def start_following(self):
        """Iniciar seguimiento del archivo actual"""
        if not self.current_file or self.follow_active:
            return
            
        self.follow_active = True
        self.follow_thread = threading.Thread(target=self._follow_file_thread)
        self.follow_thread.daemon = True
        self.follow_thread.start()

    def stop_following(self):
        """Detener seguimiento del archivo"""
        self.follow_active = False
        if self.follow_thread and self.follow_thread.is_alive():
            self.follow_thread.join(timeout=1)

    def _follow_file_thread(self):
        """Hilo para seguimiento del archivo (similar a tail -f)"""
        if not self.current_file:
            return
            
        try:
            # Obtener tamaño inicial del archivo
            self.last_file_size = os.path.getsize(self.current_file)
            
            while self.follow_active:
                try:
                    current_size = os.path.getsize(self.current_file)
                    
                    # Si el archivo ha crecido, leer el nuevo contenido
                    if current_size > self.last_file_size:
                        with open(self.current_file, 'r', encoding='utf-8', errors='replace') as f:
                            f.seek(self.last_file_size)
                            new_content = f.read()
                            
                        if new_content:
                            # Actualizar en el hilo principal
                            GLib.idle_add(self._append_new_content, new_content)
                            self.last_file_size = current_size
                    
                    # Si el archivo es más pequeño (fue truncado), recargar completamente
                    elif current_size < self.last_file_size:
                        GLib.idle_add(self._reload_file_content)
                        self.last_file_size = current_size
                    
                    # Esperar antes de la siguiente verificación
                    time.sleep(1)
                    
                except (OSError, IOError) as e:
                    # El archivo podría haber sido eliminado o renombrado
                    print(f"Error following file: {e}")
                    time.sleep(2)  # Esperar más tiempo si hay error
                    
        except Exception as e:
            print(f"Error in follow thread: {e}")
        finally:
            self.follow_active = False

    def _append_new_content(self, new_content):
        """Añadir nuevo contenido al buffer de texto"""
        with self.follow_lock:
            # Obtener la posición actual del scroll
            vadj = self.text_scrolled.get_vadjustment()
            at_bottom = (vadj.get_value() + vadj.get_page_size()) >= vadj.get_upper() - 10
            
            # Añadir contenido al buffer
            end_iter = self.text_buffer.get_end_iter()
            self.text_buffer.insert(end_iter, new_content)
            
            # Actualizar contenido original para otras funciones
            self.original_content += new_content
            
            # Si estábamos al final, hacer scroll automático
            if at_bottom:
                end_iter = self.text_buffer.get_end_iter()
                self.text_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 0.0)
            
            # Actualizar estadísticas
            self._update_stats(self.original_content)
        
        return False

    def _reload_file_content(self):
        """Recargar completamente el contenido del archivo"""
        if self.current_file:
            try:
                with open(self.current_file, "r", encoding="utf-8", errors="replace") as file:
                    content = file.read()
                self._update_content(content)
                self.last_file_size = len(content.encode('utf-8'))
                # Si el seguimiento está activo, hacer scroll al final
                if self.follow_active:
                    self._scroll_to_end()
            except Exception as e:
                print(f"Error reloading file: {e}")
        return False

    def recreate_ui(self):
        """Recrear la interfaz con los textos actualizados"""
        # Actualizar título de ventana
        if self.current_file:
            filename = os.path.basename(self.current_file)
            self.set_title(f"{filename} - {_('Log Viewer')}")
        else:
            self.set_title(_("Log Viewer"))

        # Actualizar tooltips de botones
        self.sidebar_button.set_tooltip_text(_("Show side panel (F9)"))
        self.open_button.set_tooltip_text(_("Open file (Ctrl+O)"))
        self.search_button.set_tooltip_text(_("Search (Ctrl+F)"))


        # Actualizar placeholder de búsqueda
        self.search_entry.set_placeholder_text(_("Search in file..."))

        # Actualizar botones de navegación de búsqueda
        self.prev_button.set_tooltip_text(_("Previous"))
        self.next_button.set_tooltip_text(_("Next"))

        # Actualizar textos del sidebar
        self.sidebar_title.set_markup(f"<b>{_('Tools')}</b>")
        self.filter_group.set_title(_("Filters"))
        self.filter_label.set_text(_("Text to filter"))
        self.filter_entry.set_placeholder_text(_("Filter lines..."))
        self.add_filter_button.set_label(_("Create Filter Tab"))

        self.highlight_group.set_title(_("Highlighting"))
        self.highlight_text_label.set_text(_("Text"))
        self.highlight_entry.set_placeholder_text(_("Word to highlight..."))
        self.highlight_color_label.set_text(_("Color"))
        self.highlight_mode_label.set_text(_("Highlighting mode"))
        self.highlight_word_button.set_label(_("Word"))
        self.highlight_line_button.set_label(_("Line"))
        self.add_highlight_button.set_label(_("Add Highlight"))
        self.highlight_list_label.set_text(_("Highlights in this tab:"))
        self.clear_highlights_button.set_label(_("Clear Tab Highlights"))

        # Actualizar página de bienvenida
        self.welcome_title.set_markup(
            f"<span size='x-large' weight='bold'>{_('Open a log file')}</span>"
        )
        self.welcome_subtitle.set_text(_("Drag a file here or use Ctrl+O to open it"))
        self.welcome_open_button.set_label(_("Open File"))
        self.welcome_recent_label.set_text(_("Recent files will appear here"))

        # Actualizar etiquetas de pestañas
        self.main_tab_label.set_text(_("Main File"))

        # Actualizar estadísticas si hay archivo cargado
        if hasattr(self, "original_content") and self.original_content:
            self._update_stats(self.original_content)

    def setup_ui(self):
        """Configurar la interfaz de usuario estilo GNOME Text Editor"""

        # Header bar con controles de ventana
        self.create_toolbar()

        # Layout principal con overlay para el sidebar
        overlay = Gtk.Overlay()

        # Contenedor principal
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Área de contenido principal
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_vexpand(True)

        # Área de texto principal
        self.create_text_area()
        content_box.append(self.text_scrolled)

        # Barra de búsqueda (inicialmente oculta)
        self.search_bar = self.create_search_bar()
        content_box.append(self.search_bar)

        main_box.append(content_box)

        # Sidebar como overlay desde la derecha (sin backdrop)
        self.sidebar = self.create_sidebar()
        self.sidebar.set_visible(self.sidebar_visible)

        overlay.set_child(main_box)
        overlay.add_overlay(self.sidebar)

        # Guardar referencia al sidebar
        self.sidebar_container = self.sidebar

        self.set_child(overlay)

        # CSS para la aplicación
        css_provider = Gtk.CssProvider()
        css_data = """
        .sidebar {
            background-color: @window_bg_color;
            border-left: 1px solid @borders;
            box-shadow: -3px 0 10px rgba(0, 0, 0, 0.15);
            min-width: 320px;
        }
        .compact-row {
            min-height: 0;
        }
        .compact-entry {
            min-height: 24px !important;
            max-height: 24px !important;
            padding: 2px 8px !important;
            margin: 0px !important;
            font-size: 13px;
        }
        entry.compact-entry {
            min-height: 24px !important;
            max-height: 24px !important;
            padding: 2px 8px !important;
            margin: 0px !important;
        }
        searchentry.compact-entry {
            min-height: 24px !important;
            max-height: 24px !important;
            padding: 2px 8px !important;
            margin: 0px !important;
        }
        """
        css_provider.load_from_data(css_data.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Mostrar estado inicial
        self.show_welcome_state()

    def create_toolbar(self):
        """Crear header bar personalizado"""
        header_bar = Gtk.HeaderBar()

        # Botones del lado izquierdo
        # Botón sidebar
        self.sidebar_button = Gtk.Button()
        self.sidebar_button.set_icon_name("sidebar-show-symbolic")
        self.sidebar_button.set_tooltip_text(_("Show side panel (F9)"))
        self.sidebar_button.connect("clicked", lambda x: self.toggle_sidebar())
        header_bar.pack_start(self.sidebar_button)

        # Botón abrir archivo
        self.open_button = Gtk.Button()
        self.open_button.set_icon_name("document-open-symbolic")
        self.open_button.set_tooltip_text(_("Open file (Ctrl+O)"))
        self.open_button.connect("clicked", self.on_open_file)
        header_bar.pack_start(self.open_button)

        # Botón buscar
        self.search_button = Gtk.Button()
        self.search_button.set_icon_name("system-search-symbolic")
        self.search_button.set_tooltip_text(_("Search (Ctrl+F)"))
        self.search_button.connect("clicked", lambda x: self.toggle_search())
        header_bar.pack_start(self.search_button)

        # Botón de menú de aplicación (lado derecho)
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text(_("Application menu"))

        # Crear menú
        menu_model = Gio.Menu()

        # Submenú de impresion
        main_section = Gio.Menu()
        main_section.append(_("Print..."), "app.print")
        menu_model.append_section(None, main_section)

        # Submenú de idioma
        language_menu = Gio.Menu()
        language_menu.append(_("Auto-detect"), "app.language::auto")
        language_menu.append(_("English"), "app.language::en")
        language_menu.append(_("Español"), "app.language::es")
        menu_model.append_submenu(_("Language"), language_menu)

        # Separador y elementos adicionales
        menu_model.append(_("Preferences"), "app.preferences")
        menu_model.append(_("About"), "app.about")

        menu_button.set_menu_model(menu_model)
        header_bar.pack_end(menu_button)

        # Configurar título
        header_bar.set_title_widget(None)
        self.set_titlebar(header_bar)
        self.header_bar = header_bar

        return header_bar

    def create_text_area(self):
        """Crear el área de visualización del texto con pestañas"""
        self.text_scrolled = Gtk.ScrolledWindow()
        self.text_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.text_scrolled.set_vexpand(True)
        self.text_scrolled.set_hexpand(True)

        # Stack para mostrar diferentes estados
        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # Estado de bienvenida
        self.welcome_page = self.create_welcome_page()
        self.content_stack.add_named(self.welcome_page, "welcome")

        # Área con pestañas para el contenido del archivo
        self.tabs_area = self.create_tabs_area()
        self.content_stack.add_named(self.tabs_area, "content")

        self.text_scrolled.set_child(self.content_stack)

    def create_tabs_area(self):
        """Crear área con pestañas para el contenido principal y filtros"""
        tabs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Notebook para las pestañas
        self.notebook = Gtk.Notebook()
        self.notebook.set_tab_pos(Gtk.PositionType.TOP)
        self.notebook.set_scrollable(True)
        self.notebook.set_vexpand(True)

        # Pestaña principal (archivo original)
        self.main_text_view = self.create_text_view()
        self.main_text_buffer = self.main_text_view.get_buffer()

        # Crear etiquetas para búsqueda en pestaña principal
        self.search_tag = self.main_text_buffer.create_tag("search_highlight")
        self.search_tag.set_property("background", "#ffff00")
        self.search_tag.set_property("weight", Pango.Weight.BOLD)

        self.current_search_tag = self.main_text_buffer.create_tag(
            "current_search_highlight"
        )
        self.current_search_tag.set_property("background", "#ff6600")
        self.current_search_tag.set_property("weight", Pango.Weight.BOLD)

        # Scrolled para pestaña principal
        main_scrolled = Gtk.ScrolledWindow()
        main_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        main_scrolled.set_child(self.main_text_view)

        # Etiqueta de pestaña principal
        self.main_tab_label = Gtk.Label()
        self.main_tab_label.set_text(_("Main File"))

        self.notebook.append_page(main_scrolled, self.main_tab_label)

        # Conectar evento de cambio de pestaña para actualizar lista de resaltados
        self.notebook.connect("switch-page", self.on_tab_switched)

        tabs_box.append(self.notebook)

        # Barra de estado estilo Sublime Text
        self.status_bar = self.create_status_bar()
        tabs_box.append(self.status_bar)

        # Referencias para compatibilidad
        self.text_view = self.main_text_view
        self.text_buffer = self.main_text_buffer
        self.text_tags = {}

        return tabs_box

    def create_status_bar(self):
        """Crear barra de estado estilo Sublime Text"""
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_box.add_css_class("toolbar")
        status_box.set_spacing(15)
        status_box.set_margin_start(10)
        status_box.set_margin_end(10)
        status_box.set_margin_top(3)
        status_box.set_margin_bottom(3)

        # Estadísticas del archivo actual
        self.lines_label = Gtk.Label()
        self.lines_label.set_text(_("0 lines"))
        self.lines_label.add_css_class("dim-label")
        status_box.append(self.lines_label)

        # Separador
        sep1 = Gtk.Separator()
        sep1.set_orientation(Gtk.Orientation.VERTICAL)
        status_box.append(sep1)

        self.words_label = Gtk.Label()
        self.words_label.set_text(_("0 words"))
        self.words_label.add_css_class("dim-label")
        status_box.append(self.words_label)

        # Separador
        sep2 = Gtk.Separator()
        sep2.set_orientation(Gtk.Orientation.VERTICAL)
        status_box.append(sep2)

        self.chars_label = Gtk.Label()
        self.chars_label.set_text(_("0 characters"))
        self.chars_label.add_css_class("dim-label")
        status_box.append(self.chars_label)

        # Separador
        sep3 = Gtk.Separator()
        sep3.set_orientation(Gtk.Orientation.VERTICAL)
        status_box.append(sep3)

        self.size_label = Gtk.Label()
        self.size_label.set_text("0 B")
        self.size_label.add_css_class("dim-label")
        status_box.append(self.size_label)

        # Espaciador para empujar el resto a la derecha
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_box.append(spacer)

        # Información de la pestaña actual
        self.tab_info_label = Gtk.Label()
        self.tab_info_label.set_text(_("Main File"))
        self.tab_info_label.add_css_class("dim-label")
        status_box.append(self.tab_info_label)

        # Indicador de seguimiento activo
        self.follow_indicator = Gtk.Label()
        self.follow_indicator.set_text("")
        self.follow_indicator.add_css_class("dim-label")
        self.follow_indicator.set_visible(False)
        status_box.append(self.follow_indicator)

        return status_box

    def create_text_view(self):
        """Crear un TextView configurado"""
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.set_monospace(True)
        text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        text_view.set_margin_start(20)
        text_view.set_margin_end(20)
        text_view.set_margin_top(20)
        text_view.set_margin_bottom(20)
        return text_view

    def create_welcome_page(self):
        """Crear página de bienvenida estilo GNOME Text"""
        welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        welcome_box.set_valign(Gtk.Align.CENTER)
        welcome_box.set_halign(Gtk.Align.CENTER)
        welcome_box.set_spacing(30)

        # Icono grande
        icon = Gtk.Image()
        icon.set_from_icon_name("text-x-generic-symbolic")
        icon.set_pixel_size(128)
        icon.add_css_class("dim-label")
        welcome_box.append(icon)

        # Título
        self.welcome_title = Gtk.Label()
        self.welcome_title.set_markup(
            f"<span size='x-large' weight='bold'>{_('Open a log file')}</span>"
        )
        self.welcome_title.add_css_class("title-1")
        welcome_box.append(self.welcome_title)

        # Subtítulo
        self.welcome_subtitle = Gtk.Label()
        self.welcome_subtitle.set_text(_("Drag a file here or use Ctrl+O to open it"))
        self.welcome_subtitle.add_css_class("dim-label")
        welcome_box.append(self.welcome_subtitle)

        # Botón de acción principal
        self.welcome_open_button = Gtk.Button()
        self.welcome_open_button.set_label(_("Open File"))
        self.welcome_open_button.add_css_class("suggested-action")
        self.welcome_open_button.add_css_class("pill")
        self.welcome_open_button.connect("clicked", self.on_open_file)
        welcome_box.append(self.welcome_open_button)

        # Archivos recientes (placeholder)
        self.welcome_recent_label = Gtk.Label()
        self.welcome_recent_label.set_text(_("Recent files will appear here"))
        self.welcome_recent_label.add_css_class("dim-label")
        self.welcome_recent_label.set_margin_top(40)
        welcome_box.append(self.welcome_recent_label)

        return welcome_box

    def create_search_bar(self):
        """Crear la barra de búsqueda estilo GNOME Text"""
        search_bar = Gtk.SearchBar()

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        search_box.set_spacing(6)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(6)
        search_box.set_margin_bottom(6)

        # Entry de búsqueda
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.set_placeholder_text(_("Search in file..."))
        self.search_entry.add_css_class("compact-entry")
        self.search_entry.set_size_request(-1, 30)
        self.search_entry.connect("search-changed", self.on_search_changed)
        search_box.append(self.search_entry)

        # Botones de navegación
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nav_box.add_css_class("linked")

        self.prev_button = Gtk.Button()
        self.prev_button.set_icon_name("go-up-symbolic")
        self.prev_button.set_tooltip_text(_("Previous"))
        self.prev_button.connect("clicked", self.on_search_previous)
        nav_box.append(self.prev_button)

        self.next_button = Gtk.Button()
        self.next_button.set_icon_name("go-down-symbolic")
        self.next_button.set_tooltip_text(_("Next"))
        self.next_button.connect("clicked", self.on_search_next)
        nav_box.append(self.next_button)

        search_box.append(nav_box)

        # Contador de resultados
        self.search_results_label = Gtk.Label()
        self.search_results_label.add_css_class("dim-label")
        self.search_results_label.set_margin_start(12)
        search_box.append(self.search_results_label)

        search_bar.set_child(search_box)
        search_bar.connect_entry(self.search_entry)

        return search_bar

    def create_sidebar(self):
        """Crear sidebar como overlay estilo GNOME"""
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.set_size_request(320, -1)
        sidebar_box.add_css_class("sidebar")
        sidebar_box.set_valign(Gtk.Align.FILL)
        sidebar_box.set_halign(Gtk.Align.END)

        # Header del sidebar
        sidebar_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar_header.set_spacing(10)
        sidebar_header.set_margin_start(15)
        sidebar_header.set_margin_end(15)
        sidebar_header.set_margin_top(15)
        sidebar_header.set_margin_bottom(10)

        self.sidebar_title = Gtk.Label()
        self.sidebar_title.set_markup(f"<b>{_('Tools')}</b>")
        self.sidebar_title.set_hexpand(True)
        self.sidebar_title.set_halign(Gtk.Align.START)
        sidebar_header.append(self.sidebar_title)

        close_button = Gtk.Button()
        close_button.set_icon_name("window-close-symbolic")
        close_button.add_css_class("flat")
        close_button.connect("clicked", lambda x: self.toggle_sidebar())
        sidebar_header.append(close_button)

        sidebar_box.append(sidebar_header)

        # Separator
        separator = Gtk.Separator()
        sidebar_box.append(separator)

        # Contenido scrollable del sidebar
        scrolled_sidebar = Gtk.ScrolledWindow()
        scrolled_sidebar.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_sidebar.set_vexpand(True)

        sidebar_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_content.set_spacing(20)
        sidebar_content.set_margin_start(15)
        sidebar_content.set_margin_end(15)
        sidebar_content.set_margin_top(15)
        sidebar_content.set_margin_bottom(15)

        # Sección de filtros
        self.create_filter_section(sidebar_content)

        # Sección de resaltado
        self.create_highlight_section(sidebar_content)

        scrolled_sidebar.set_child(sidebar_content)
        sidebar_box.append(scrolled_sidebar)

        return sidebar_box

    def create_filter_section(self, parent):
        """Crear sección de filtros"""
        self.filter_group = Adw.PreferencesGroup()
        self.filter_group.set_title(_("Filters"))

        # Contenedor para filtro
        filter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        filter_box.set_spacing(6)
        filter_box.set_margin_start(12)
        filter_box.set_margin_end(12)
        filter_box.set_margin_top(6)
        filter_box.set_margin_bottom(6)

        # Etiqueta
        self.filter_label = Gtk.Label()
        self.filter_label.set_text(_("Text to filter"))
        self.filter_label.set_halign(Gtk.Align.START)
        self.filter_label.add_css_class("caption-heading")
        filter_box.append(self.filter_label)

        # Campo de filtro
        self.filter_entry = Gtk.Entry()
        self.filter_entry.set_placeholder_text(_("Filter lines..."))
        self.filter_entry.set_size_request(-1, 28)
        filter_box.append(self.filter_entry)

        self.filter_group.add(filter_box)

        # Botón crear nueva pestaña con filtro
        self.add_filter_button = Gtk.Button()
        self.add_filter_button.set_label(_("Create Filter Tab"))
        self.add_filter_button.add_css_class("suggested-action")
        self.add_filter_button.set_margin_top(10)
        self.add_filter_button.connect("clicked", self.on_add_filter_tab)
        self.filter_group.add(self.add_filter_button)

        parent.append(self.filter_group)

    def create_highlight_section(self, parent):
        """Crear sección de resaltado"""
        self.highlight_group = Adw.PreferencesGroup()
        self.highlight_group.set_title(_("Highlighting"))

        # Contenedor para entrada de texto
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text_box.set_spacing(6)
        text_box.set_margin_start(12)
        text_box.set_margin_end(12)
        text_box.set_margin_top(6)
        text_box.set_margin_bottom(6)

        # Etiqueta
        self.highlight_text_label = Gtk.Label()
        self.highlight_text_label.set_text(_("Text"))
        self.highlight_text_label.set_halign(Gtk.Align.START)
        self.highlight_text_label.add_css_class("caption-heading")
        text_box.append(self.highlight_text_label)

        # Entrada para palabra
        self.highlight_entry = Gtk.Entry()
        self.highlight_entry.set_placeholder_text(_("Word to highlight..."))
        self.highlight_entry.set_size_request(-1, 28)
        text_box.append(self.highlight_entry)

        self.highlight_group.add(text_box)

        # Contenedor para color
        color_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        color_box.set_spacing(6)
        color_box.set_margin_start(12)
        color_box.set_margin_end(12)
        color_box.set_margin_top(6)
        color_box.set_margin_bottom(6)

        # Etiqueta
        self.highlight_color_label = Gtk.Label()
        self.highlight_color_label.set_text(_("Color"))
        self.highlight_color_label.set_halign(Gtk.Align.START)
        self.highlight_color_label.add_css_class("caption-heading")
        color_box.append(self.highlight_color_label)

        # Selector de color
        self.color_button = Gtk.ColorButton()
        self.color_button.set_size_request(-1, 28)
        color_box.append(self.color_button)

        self.highlight_group.add(color_box)

        # Contenedor para modo
        mode_box_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        mode_box_container.set_spacing(6)
        mode_box_container.set_margin_start(12)
        mode_box_container.set_margin_end(12)
        mode_box_container.set_margin_top(6)
        mode_box_container.set_margin_bottom(6)

        # Etiqueta
        self.highlight_mode_label = Gtk.Label()
        self.highlight_mode_label.set_text(_("Highlighting mode"))
        self.highlight_mode_label.set_halign(Gtk.Align.START)
        self.highlight_mode_label.add_css_class("caption-heading")
        mode_box_container.append(self.highlight_mode_label)

        # Selector de modo (Palabra/Línea)
        self.highlight_word_button = Gtk.ToggleButton(label=_("Word"))
        self.highlight_line_button = Gtk.ToggleButton(label=_("Line"))
        self.highlight_line_button.set_group(self.highlight_word_button)
        self.highlight_word_button.set_active(True)

        self.highlight_word_button.set_hexpand(True)
        self.highlight_line_button.set_hexpand(True)

        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        mode_box.add_css_class("linked")
        mode_box.set_homogeneous(True)
        mode_box.append(self.highlight_word_button)
        mode_box.append(self.highlight_line_button)

        mode_box_container.append(mode_box)
        self.highlight_group.add(mode_box_container)

        # Botón agregar
        self.add_highlight_button = Gtk.Button()
        self.add_highlight_button.set_label(_("Add Highlight"))
        self.add_highlight_button.add_css_class("suggested-action")
        self.add_highlight_button.set_margin_top(10)
        self.add_highlight_button.connect("clicked", self.on_add_highlight)
        self.highlight_group.add(self.add_highlight_button)

        # Contenedor para la lista de resaltados
        self.highlight_list_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.highlight_list_container.set_visible(False)
        self.highlight_list_container.set_margin_start(12)
        self.highlight_list_container.set_margin_end(12)
        self.highlight_list_container.set_margin_top(15)
        self.highlight_list_container.set_margin_bottom(10)

        # Lista de resaltados de la pestaña actual
        self.highlight_list_label = Gtk.Label()
        self.highlight_list_label.set_text(_("Highlights in this tab:"))
        self.highlight_list_label.set_halign(Gtk.Align.START)
        self.highlight_list_label.set_margin_bottom(8)
        self.highlight_list_label.add_css_class("heading")
        self.highlight_list_container.append(self.highlight_list_label)

        self.highlight_list = Gtk.ListBox()
        self.highlight_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.highlight_list.add_css_class("boxed-list")
        self.highlight_list_container.append(self.highlight_list)

        self.highlight_group.add(self.highlight_list_container)

        # Botón limpiar resaltados
        self.clear_highlights_button = Gtk.Button()
        self.clear_highlights_button.set_label(_("Clear Tab Highlights"))
        self.clear_highlights_button.add_css_class("destructive-action")
        self.clear_highlights_button.set_margin_top(10)
        self.clear_highlights_button.set_visible(False)
        self.clear_highlights_button.connect("clicked", self.on_clear_tab_highlights)
        self.highlight_group.add(self.clear_highlights_button)

        parent.append(self.highlight_group)

    def setup_shortcuts(self):
        """Configurar atajos de teclado"""
        shortcuts = [
            ("<Control>f", self.on_search_shortcut),
            ("<Control>o", self.on_open_shortcut),
            ("F9", self.on_sidebar_shortcut),
        ]

        controller = Gtk.ShortcutController()
        for trigger, callback in shortcuts:
            shortcut = Gtk.Shortcut()
            shortcut.set_trigger(Gtk.ShortcutTrigger.parse_string(trigger))
            shortcut.set_action(Gtk.CallbackAction.new(callback))
            controller.add_shortcut(shortcut)

        self.add_controller(controller)

    def show_welcome_state(self):
        """Mostrar estado de bienvenida"""
        self.content_stack.set_visible_child_name("welcome")
        self.set_title(_("Log Viewer"))
        self._update_toolbar_visibility(False)

        print_action = self.get_application().lookup_action("print")
        if print_action:
            print_action.set_enabled(False)

    def show_content_state(self):
        """Mostrar contenido del archivo"""
        self.content_stack.set_visible_child_name("content")
        self._update_toolbar_visibility(True)

    def _update_toolbar_visibility(self, show_buttons):
        """Mostrar u ocultar botones de la barra de herramientas"""
        self.sidebar_button.set_visible(show_buttons)
        self.open_button.set_visible(show_buttons)
        self.search_button.set_visible(show_buttons)

    def toggle_sidebar(self):
        """Mostrar/ocultar sidebar"""
        self.sidebar_visible = not self.sidebar_visible
        self.sidebar_container.set_visible(self.sidebar_visible)

    def toggle_search(self):
        """Mostrar/ocultar barra de búsqueda"""
        if self.current_file:
            is_active = not self.search_bar.get_search_mode()
            self.search_bar.set_search_mode(is_active)
            if is_active:
                self.search_entry.grab_focus()

    # Event handlers (simplificados para mantener el código corto)
    def on_open_shortcut(self, widget, args):
        self.on_open_file(None)
        return True

    def on_search_shortcut(self, widget, args):
        if self.current_file:
            self.search_bar.set_search_mode(True)
            self.search_entry.grab_focus()
            return True

    def on_sidebar_shortcut(self, widget, args):
        if self.current_file:
            self.toggle_sidebar()
            return True

    def on_print_shortcut(self, widget, args):
        if self.current_file:
            self.on_print_file(None)
            return True

    def on_open_file(self, button):
        """Abrir selector de archivos"""
        dialog = Gtk.FileChooserDialog(
            title=_("Open log file"), parent=self, action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            _("_Cancel"), Gtk.ResponseType.CANCEL, _("_Open"), Gtk.ResponseType.ACCEPT
        )
        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()

    def on_file_dialog_response(self, dialog, response_id):
        """Manejar respuesta del selector de archivos"""
        if response_id == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                self.load_file(file.get_path())
        dialog.destroy()

    def load_file(self, file_path):
        """Cargar archivo"""
        # Detener seguimiento anterior si estaba activo
        self.stop_following()
        
        self.current_file = file_path
        filename = os.path.basename(file_path)
        self.set_title(f"{filename} - {_('Log Viewer')}")
        thread = threading.Thread(target=self._load_file_thread, args=(file_path,))
        thread.daemon = True
        thread.start()

    def _load_file_thread(self, file_path):
        """Cargar archivo en hilo separado"""
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as file:
                content = file.read()
            GLib.idle_add(self._update_content, content)
        except Exception as e:
            GLib.idle_add(self._show_error, f"{_('Error loading file')}: {str(e)}")

    def _update_content(self, content):
        """Actualizar contenido en el hilo principal"""
        self.original_content = content
        self.text_buffer.set_text(content)
        self.show_content_state()
        # NO abrir sidebar automáticamente - que permanezca cerrado por defecto
        self.tab_highlights[0] = []
        self._update_stats(content)

        # Activar la acción de imprimir
        print_action = self.get_application().lookup_action("print")
        if print_action:
            print_action.set_enabled(True)

        # Iniciar seguimiento si está habilitado
        if self.follow_enabled:
            self.start_following()
            self._update_follow_indicator()
            # Hacer scroll al final cuando el seguimiento está activo
            self._scroll_to_end()

        return False

    def _update_follow_indicator(self):
        """Actualizar indicador de seguimiento en la barra de estado"""
        if self.follow_active:
            self.follow_indicator.set_text("📡 Following")
            self.follow_indicator.set_visible(True)
        else:
            self.follow_indicator.set_visible(False)

    def _update_stats(self, content):
        """Actualizar estadísticas en la barra de estado"""
        lines = content.split('\n')
        words = content.split()
        chars = len(content)
        file_size = len(content.encode('utf-8'))
        
        # Actualizar etiquetas de la barra de estado
        self.lines_label.set_text(f"{len(lines):,} {_('lines').lower()}")
        self.words_label.set_text(f"{len(words):,} {_('words').lower()}")
        self.chars_label.set_text(f"{chars:,} {_('characters').lower()}")
        self.size_label.set_text(self._format_file_size(file_size))

    def _format_file_size(self, size_bytes):
        """Formatear tamaño de archivo"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    def _show_error(self, message):
        print(f"Error: {message}")
        return False

    # Métodos simplificados para funcionalidades básicas
    def on_add_filter_tab(self, button):
        pass  # Implementar según necesidad

    def on_add_highlight(self, button):
        pass  # Implementar según necesidad

    def on_clear_tab_highlights(self, button):
        pass  # Implementar según necesidad

    def on_tab_switched(self, notebook, page, page_num):
        pass  # Implementar según necesidad

    def on_search_changed(self, entry):
        pass  # Implementar según necesidad

    def on_search_next(self, button):
        pass  # Implementar según necesidad

    def on_search_previous(self, button):
        pass  # Implementar según necesidad

    # Dentro de la clase LogViewerWindow

    def on_print_file(self, button):
        """Imprimir el contenido de la pestaña actual usando el diálogo nativo."""
        if not self.current_file:
            return

        # Tu lógica para obtener el text_view_to_print está bien...
        current_page_num = self.notebook.get_current_page()
        page_widget = self.notebook.get_nth_page(current_page_num)
        
        if isinstance(page_widget, Gtk.ScrolledWindow):
            text_view_to_print = page_widget.get_child()
            if not isinstance(text_view_to_print, Gtk.TextView):
                 return
        else:
            return

        print_op = Gtk.PrintOperation()
        print_op.set_job_name(f"Log: {os.path.basename(self.current_file)}")
        
        # ELIMINA ESTA LÍNEA QUE CAUSA EL ERROR
        # print_op.set_transient_for(self) 

        print_op.connect("begin_print", self._begin_print, text_view_to_print.get_buffer())
        print_op.connect("draw_page", self._draw_page, text_view_to_print.get_buffer())

        # Esta llamada ya asocia el diálogo con la ventana principal (self)
        res = print_op.run(Gtk.PrintOperationAction.PRINT_DIALOG, self)

        if res == Gtk.PrintOperationResult.ERROR:
            # El manejo de errores está bien
            error_dialog = Adw.MessageDialog.new(self, _("Printing Error"), print_op.get_status_string())
            error_dialog.add_response("close", _("Close"))
            error_dialog.present()

    def _begin_print(self, operation, context, buffer):
        """Prepara la paginación del texto antes de imprimir."""
        # Se usa Pango para maquetar el texto
        layout = context.create_pango_layout()
        font_desc = Pango.FontDescription("Monospace 10")
        layout.set_font_description(font_desc)
        layout.set_text(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False), -1)
        
        # Calcula el número de páginas
        line_count = layout.get_line_count()
        lines_per_page = 55 # Un valor aproximado, se puede calcular de forma más precisa
        num_pages = (line_count + lines_per_page - 1) // lines_per_page
        operation.set_n_pages(num_pages)
        
        # Almacenar el layout para usarlo en _draw_page
        operation.layout = layout


    def _draw_page(self, operation, context, page_nr, buffer):
        """Dibuja el contenido de una página específica."""
        cr = context.get_cairo_context()
        layout = operation.layout

        # Dibuja el texto de la página actual
        cr.save()
        Pango.cairo_show_layout(cr, layout) # Simplificación: esto dibuja todo el layout.
                                            # Una implementación completa debería dibujar solo
                                            # las líneas correspondientes a la `page_nr`.
        cr.restore()


    def execute_print(self):
        """Ejecutar impresión del archivo"""
        if not self.current_file or not hasattr(self, 'original_content'):
            return
        
        try:
            # Crear archivo temporal HTML para impresión
            html_content = self.create_print_html(
                self.original_content, 
                os.path.basename(self.current_file),
                12,  # Tamaño de fuente por defecto
                20   # Margen por defecto
            )
            
            # Crear archivo temporal
            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
                f.write(html_content)
                temp_file = f.name
            
            # Intentar abrir con el navegador predeterminado para imprimir
            if sys.platform.startswith('linux'):
                subprocess.run(['xdg-open', temp_file])
            elif sys.platform == 'darwin':
                subprocess.run(['open', temp_file])
            elif sys.platform == 'win32':
                os.startfile(temp_file)
            
            # Programar eliminación del archivo temporal después de un tiempo
            GLib.timeout_add_seconds(30, lambda: self.cleanup_temp_file(temp_file))
            
        except Exception as e:
            print(f"Error al imprimir: {e}")
    
    def create_print_html(self, content, title, font_size, margin):
        """Crear HTML formateado para impresión"""
        # Escapar contenido HTML
        import html
        escaped_content = html.escape(content)
        
        html_template = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{html.escape(title)}</title>
    <style>
        @media print {{
            @page {{
                margin: {margin}mm;
            }}
        }}
        body {{
            font-family: 'Courier New', monospace;
            font-size: {font_size}pt;
            line-height: 1.2;
            margin: {margin}mm;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        .header {{
            text-align: center;
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
            margin-bottom: 20px;
            font-weight: bold;
        }}
        .content {{
            font-family: 'Courier New', monospace;
        }}
    </style>
</head>
<body>
    <div class="header">{html.escape(title)}</div>
    <div class="content">{escaped_content}</div>
</body>
</html>
"""
        return html_template
    
    def cleanup_temp_file(self, temp_file):
        """Limpiar archivo temporal"""
        try:
            os.unlink(temp_file)
        except:
            pass
        return False  # No repetir el timeout

class LogViewerApplication(Adw.Application):
    """Aplicación principal del visor de logs"""

    def __init__(self):
        super().__init__(
            application_id="com.pabmartine.LogViewer",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.connect("activate", self.on_activate)
        self.setup_actions()

    # Dentro de la clase LogViewerApplication
    def setup_actions(self):
        """Configurar acciones de la aplicación"""
        
        # Acción de imprimir (NUEVO)
        print_action = Gio.SimpleAction.new("print", None)
        print_action.connect("activate", self.on_print)
        print_action.set_enabled(False)
        self.add_action(print_action)
        self.set_accels_for_action("app.print", ["<Control>p"])

        # Acción de cambio de idioma
        language_action = Gio.SimpleAction.new_stateful(
            "language", GLib.VariantType.new("s"), GLib.Variant("s", "auto")
        )
        language_action.connect("activate", self.on_language_changed)
        self.add_action(language_action)

        # Acción de preferencias
        preferences_action = Gio.SimpleAction.new("preferences", None)
        preferences_action.connect("activate", self.on_preferences)
        self.add_action(preferences_action)

        # Acción de acerca de
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.on_about)
        self.add_action(about_action)

    def on_language_changed(self, action, parameter):
        """Cambiar idioma de la aplicación"""
        language_code = parameter.get_string()
        action.set_state(parameter)
        if hasattr(self, "win"):
            self.win.change_language(language_code)

    def on_preferences(self, action, parameter):
        """Mostrar ventana de preferencias"""
        if hasattr(self, "win"):
            self.show_preferences_dialog()

    def show_preferences_dialog(self):
        """Mostrar diálogo de preferencias"""
        dialog = Adw.PreferencesWindow()
        dialog.set_title(_("Preferences"))
        dialog.set_modal(True)
        dialog.set_transient_for(self.win)

        # Página de preferencias generales
        page = Adw.PreferencesPage()
        page.set_title(_("General"))

        # Grupo de idioma
        language_group = Adw.PreferencesGroup()
        language_group.set_title(_("Language"))

        # Selector de idioma
        language_row = Adw.ComboRow()
        language_row.set_title(_("Interface Language"))
        language_model = Gtk.StringList()
        language_model.append(_("Auto-detect"))
        language_model.append(_("English"))
        language_model.append(_("Español"))
        language_row.set_model(language_model)

        # Establecer selección actual
        current_lang = self.win.current_language
        if current_lang == "auto":
            language_row.set_selected(0)
        elif current_lang == "en":
            language_row.set_selected(1)
        elif current_lang == "es":
            language_row.set_selected(2)

        language_row.connect("notify::selected", self.on_language_row_changed)
        language_group.add(language_row)
        page.add(language_group)

        # Grupo de apariencia
        appearance_group = Adw.PreferencesGroup()
        appearance_group.set_title(_("Appearance"))

        # Switch para tema oscuro
        dark_theme_row = Adw.SwitchRow()
        dark_theme_row.set_title(_("Dark Theme"))
        dark_theme_row.set_subtitle(_("Use dark theme for the application"))
        dark_theme_row.set_active(self.win.config.get("dark_theme", False))
        dark_theme_row.connect("notify::active", self.on_theme_changed)
        appearance_group.add(dark_theme_row)
        page.add(appearance_group)

        # Grupo de seguimiento (NUEVO)
        monitoring_group = Adw.PreferencesGroup()
        monitoring_group.set_title(_("Monitoring"))

        # Switch para seguimiento de logs
        follow_logs_row = Adw.SwitchRow()
        follow_logs_row.set_title(_("Follow Logs"))
        follow_logs_row.set_subtitle(_("Automatically update when file changes (like tail -f)"))
        follow_logs_row.set_active(self.win.config.get("follow_logs", False))
        follow_logs_row.connect("notify::active", self.on_follow_logs_changed)
        monitoring_group.add(follow_logs_row)
        page.add(monitoring_group)

        dialog.add(page)
        dialog.present()

    def on_theme_changed(self, switch_row, param):
        """Manejar cambio de tema"""
        dark_theme = switch_row.get_active()
        self.win.change_theme(dark_theme)

    def on_follow_logs_changed(self, switch_row, param):
        """Manejar cambio en el seguimiento de logs"""
        follow_logs = switch_row.get_active()
        self.win.change_follow_logs(follow_logs)

    def on_language_row_changed(self, combo_row, param):
        """Manejar cambio en el selector de idioma"""
        selected = combo_row.get_selected()
        language_codes = ["auto", "en", "es"]
        if selected < len(language_codes):
            language_code = language_codes[selected]
            action = self.lookup_action("language")
            if action:
                action.activate(GLib.Variant("s", language_code))

    def on_about(self, action, parameter):
        """Mostrar diálogo Acerca de"""
        about_dialog = Adw.AboutWindow()
        about_dialog.set_transient_for(self.win)
        about_dialog.set_modal(True)
        about_dialog.set_application_name(_("Log Viewer"))
        about_dialog.set_version("1.3.0")
        about_dialog.set_developer_name(_("Developer"))
        about_dialog.set_copyright("© 2025")
        about_dialog.set_comments(_("A simple and powerful log file viewer"))
        about_dialog.set_license_type(Gtk.License.GPL_3_0)
        about_dialog.set_developers([_("Main Developer")])
        about_dialog.present()

    def on_activate(self, app):
        """Se llama cuando se activa la aplicación"""
        self.win = LogViewerWindow(application=app)
        self.win.present()

    def on_print(self, action, parameter):
        """Manejar la acción de imprimir"""
        # Llama a la función de impresión de la ventana si existe y tiene un archivo
        if hasattr(self, "win") and self.win.current_file:
            self.win.on_print_file(None)

def main():
    """Función principal"""
    app = LogViewerApplication()
    return app.run(sys.argv)

if __name__ == "__main__":
    main()