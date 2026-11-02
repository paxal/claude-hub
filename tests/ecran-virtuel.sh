#!/usr/bin/env bash
# Lance une commande sur un écran virtuel (KWin sans affichage), hors de la
# session de l'utilisateur : rien n'apparaît à l'écran, et un bus D-Bus séparé
# isole le compositeur, spectacle et le presse-papier.
#
#   tests/ecran-virtuel.sh commande [arguments…]
#
# Dans la commande, WAYLAND_DISPLAY désigne l'écran virtuel ; une vraie capture
# du compositeur se prend avec : spectacle -b -n -f -o fichier.png
# Taille de l'écran : variables LARGEUR et HAUTEUR (1280×800 par défaut).
set -euo pipefail

if [ $# -eq 0 ]; then
    echo "usage : $0 commande [arguments…]" >&2
    exit 2
fi

if [ -z "${ECRAN_VIRTUEL_BUS:-}" ]; then
    # Pas d'accessibilité : sinon le bus privé lance son propre at-spi-bus-launcher
    # qui, avec le DISPLAY hérité, prend la socket de la session
    # (/run/user/…/at-spi/bus_1) et la supprime en partant : les applications
    # lancées ensuite chez l'utilisateur ne trouvent plus le bus d'accessibilité.
    export GTK_A11Y=none NO_AT_BRIDGE=1 QT_ACCESSIBILITY=0
    unset DISPLAY
    # Les services lancés par le bus (ibus, portails…) héritent de ses sorties
    # et les garderaient ouvertes après la fin : le bus écrit dans /dev/null,
    # la commande retrouve les vraies sorties par les descripteurs 3 et 4.
    export ECRAN_VIRTUEL_BUS=1
    exec 3>&1 4>&2
    exec dbus-run-session -- "$0" "$@" >/dev/null 2>&1
fi
exec 1>&3 2>&4 3>&- 4>&-

socket="wl-claude-hub-test-$$"
kwin_wayland --virtual --no-lockscreen \
    --width "${LARGEUR:-1280}" --height "${HAUTEUR:-800}" \
    --socket "$socket" >/dev/null 2>&1 &
kwin=$!
trap 'kill "$kwin" 2>/dev/null; wait "$kwin" 2>/dev/null || true' EXIT

for _ in $(seq 100); do
    [ -S "$XDG_RUNTIME_DIR/$socket" ] && break
    sleep 0.1
done
if [ ! -S "$XDG_RUNTIME_DIR/$socket" ]; then
    echo "l'écran virtuel n'a pas démarré" >&2
    exit 1
fi

export WAYLAND_DISPLAY="$socket" GDK_BACKEND=wayland QT_QPA_PLATFORM=wayland
unset DISPLAY
"$@"
