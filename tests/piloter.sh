#!/usr/bin/env bash
# Lance un script de pilotage (tests/pilote.py) dans une instance séparée de
# claude-hub, sur l'écran virtuel, sans toucher à la config ni au presse-papier
# de l'utilisateur.
#
#   tests/piloter.sh [options] script.py [arguments…]
#
# Options :
#   --copie-config   part d'une copie de ~/.config/claude-hub (hôtes, tags,
#                    réglages réels) au lieu d'une config vide (hôte local seul)
#   --faux-home      HOME temporaire avec un ~/.claude vide : la liste ne montre que
#                    les conversations du test, et le faux claude y écrit ses
#                    transcriptions (le hub les relit à la fin d'une réponse)
#   --vrai-claude    garde le vrai claude dans le PATH (Haiku, dossier temporaire,
#                    conversation à supprimer ensuite : voir AGENTS.md)
#   --theme T        sombre (défaut : Breeze sombre, comme le bureau), clair
#                    (Breeze:light) ou adwaita
#   --garder         ne supprime pas le dossier temporaire à la fin
#
# Variables : LARGEUR et HAUTEUR (écran, 1280×800), FAUX_CLAUDE_SCENARIO et
# FAUX_CLAUDE_VITESSE (voir tests/faux-claude), PILOTE_DELAI (secondes, 180).
# Le script reçoit PILOTE_DIR (dossier temporaire), PILOTE_CAPTURES (captures)
# et FAUX_CLAUDE_JOURNAL (ce que le hub a envoyé au faux claude).
set -euo pipefail

racine=$(cd "$(dirname "$0")/.." && pwd)
copie=0 faux_home=0 vrai_claude=0 garder=0 theme=sombre
while [ $# -gt 0 ]; do
    case "$1" in
        --copie-config) copie=1 ;;
        --faux-home) faux_home=1 ;;
        --vrai-claude) vrai_claude=1 ;;
        --garder) garder=1 ;;
        --theme) theme=${2:?--theme sombre|clair|adwaita}; shift ;;
        -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --) shift; break ;;
        -*) echo "option inconnue : $1" >&2; exit 2 ;;
        *) break ;;
    esac
    shift
done
if [ $# -eq 0 ]; then
    echo "usage : $0 [options] script.py [arguments…]  (--help pour les options)" >&2
    exit 2
fi
script=$(realpath "$1"); shift

reel="$HOME/.config"
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-hub-pilote.XXXXXX")
mkdir -p "$T/config" "$T/captures" "$T/bin"

# Config de claude-hub : vide, ou copie de la vraie.
if [ $copie = 1 ] && [ -d "$reel/claude-hub" ]; then
    cp -r "$reel/claude-hub" "$T/config/"
fi
# Thème du bureau : sans ces deux copies, GTK ne trouve ni ses réglages (gtk-4.0)
# ni le nom du thème (dconf) et prend Adwaita.
for d in gtk-4.0 dconf fontconfig; do
    [ -d "$reel/$d" ] && cp -r "$reel/$d" "$T/config/"
done
# Base Cursor : lien (claude-hub l'ouvre en lecture seule) ; absente avec --faux-home.
if [ $faux_home = 0 ] && [ -d "$reel/Cursor" ]; then
    ln -s "$reel/Cursor" "$T/config/Cursor"
fi

empreinte() { (cd "$reel/claude-hub" 2>/dev/null && find . -type f -exec md5sum {} + | sort) || true; }
avant=$(empreinte)

if [ $vrai_claude = 0 ]; then
    ln -s "$racine/tests/faux-claude" "$T/bin/claude"
    export PATH="$T/bin:$PATH"
fi
if [ $faux_home = 1 ]; then
    mkdir -p "$T/home/.claude/projects"
    export HOME="$T/home"
fi
case "$theme" in
    sombre) unset GTK_THEME ;;
    clair) export GTK_THEME=Breeze:light ;;
    adwaita) export GTK_THEME=Adwaita ;;
    *) echo "thème inconnu : $theme (sombre, clair, adwaita)" >&2; exit 2 ;;
esac

export XDG_CONFIG_HOME="$T/config"
export PILOTE_DIR="$T" PILOTE_CAPTURES="${PILOTE_CAPTURES:-$T/captures}"
export FAUX_CLAUDE_JOURNAL="$T/faux-claude.jsonl"
export PYTHONPATH="$racine/src:$racine/tests${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1  # pas de __pycache__ dans le dépôt

set +e
timeout "${PILOTE_DELAI:-180}" "$racine/tests/ecran-virtuel.sh" python3 "$script" "$@" 2>&1 \
    | grep --line-buffered -v -e GtkGizmo -e kf.windowsystem
code=${PIPESTATUS[0]}
set -e
[ "$code" = 124 ] && echo "piloter : arrêté au bout de ${PILOTE_DELAI:-180} s" >&2

if [ "$(empreinte)" != "$avant" ]; then
    ouverte=$(pgrep -f "bin/claude-hub" | tr '\n' ' ' || true)
    echo "ATTENTION : ~/.config/claude-hub a changé pendant le test." >&2
    if [ -n "$ouverte" ]; then
        echo "  Un claude-hub de l'utilisateur tourne (PID $ouverte) et enregistre ses réglages :" \
             "c'est sans doute lui. Pour en être sûr, chercher les id du test dans la vraie config." >&2
    else
        echo "  Aucun claude-hub de l'utilisateur ne tourne : fuite probable du test." >&2
    fi
else
    echo "config réelle inchangée"
fi

if [ -n "$(ls -A "$PILOTE_CAPTURES" 2>/dev/null)" ]; then
    echo "captures : $PILOTE_CAPTURES"
    [ "$PILOTE_CAPTURES" = "$T/captures" ] && garder=1
fi
if [ $garder = 1 ]; then
    echo "dossier gardé : $T"
else
    rm -rf "$T"
fi
exit "$code"
