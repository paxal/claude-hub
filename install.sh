#!/bin/sh
# Installe claude-hub pour l'utilisateur courant, en pointant sur ce dépôt :
# les modifications du code sont prises en compte au prochain lancement.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
bindir=${BINDIR:-$HOME/.bin}
datadir=${XDG_DATA_HOME:-$HOME/.local/share}
appdir=$datadir/applications
icondir=$datadir/icons/hicolor/scalable/apps

mkdir -p "$bindir" "$appdir" "$icondir"
ln -sfn "$here/bin/claude-hub" "$bindir/claude-hub"
# Le nom du .desktop doit être l'app_id de la fenêtre : sous Wayland, c'est
# ainsi que le compositeur trouve l'icône (sinon il affiche le « W »).
# Icône en chemin absolu : plasmashell ne voit une icône ajoutée au thème
# qu'après son redémarrage.
sed -e "s|^Exec=.*|Exec=$bindir/claude-hub|" \
    -e "s|^Icon=.*|Icon=$icondir/org.paxal.ClaudeHub.svg|" "$here/data/org.paxal.ClaudeHub.desktop" \
    > "$appdir/org.paxal.ClaudeHub.desktop"
rm -f "$appdir/claude-hub.desktop"  # ancien nom
ln -sfn "$here/data/org.paxal.ClaudeHub.svg" "$icondir/org.paxal.ClaudeHub.svg"
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$datadir/icons/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database "$appdir" || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
echo "claude-hub -> $bindir/claude-hub"
