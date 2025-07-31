#!/bin/bash

# Script para construir el Flatpak de Log Viewer

set -e

APP_ID="com.pabmartine.LogViewer"
BUILD_DIR="build-dir"
REPO_DIR="repo"

echo "🔧 Preparando construcción de Flatpak..."

# Limpiar construcciones anteriores
if [ -d "$BUILD_DIR" ]; then
    echo "Limpiando directorio de construcción anterior..."
    rm -rf "$BUILD_DIR"
fi

if [ -d "$REPO_DIR" ]; then
    echo "Limpiando repositorio anterior..."
    rm -rf "$REPO_DIR"
fi

# Verificar que existen los runtimes necesarios
echo "📦 Verificando runtimes de Flatpak..."
if ! flatpak list --runtime | grep -q "org.gnome.Platform.*48"; then
    echo "Instalando runtime de GNOME Platform 48..."
    flatpak install --user flathub org.gnome.Platform//48 org.gnome.Sdk//48 -y
fi

# Compilar traducciones
echo "🌍 Compilando traducciones..."
for lang_dir in locale/*/; do
    if [ -d "$lang_dir" ]; then
        lang=$(basename "$lang_dir")
        po_file="$lang_dir/LC_MESSAGES/log-viewer.po"
        mo_file="$lang_dir/LC_MESSAGES/log-viewer.mo"
        if [ -f "$po_file" ]; then
            echo "Compilando traducción para $lang..."
            msgfmt "$po_file" -o "$mo_file"
        fi
    fi
done

# Construir el Flatpak
echo "🏗️  Construyendo Flatpak..."
flatpak-builder --user --install --force-clean "$BUILD_DIR" "$APP_ID.yaml"

# Crear repositorio local
echo "📚 Creando repositorio local..."
flatpak-builder --user --repo="$REPO_DIR" --force-clean "$BUILD_DIR" "$APP_ID.yaml"

# Crear bundle para distribución
echo "📦 Creando bundle..."
flatpak build-bundle "$REPO_DIR" "$APP_ID.flatpak" "$APP_ID"

echo "✅ ¡Flatpak construido exitosamente!"
echo ""
echo "Para instalar localmente:"
echo "  flatpak install --user $APP_ID.flatpak"
echo ""
echo "Para ejecutar:"
echo "  flatpak run $APP_ID"
echo ""
echo "Para desinstalar:"
echo "  flatpak uninstall --user $APP_ID"