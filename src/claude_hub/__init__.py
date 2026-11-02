"""claude-hub — interface graphique (GTK4) pour les sessions Claude Code.

Liste les conversations de ce PC et d'hôtes SSH, les affiche sous forme de chat
et les continue avec `claude -p --resume`. Les conversations restent les
fichiers habituels de ~/.claude : on peut les reprendre dans un terminal
(`claude --resume`, `clh`) et inversement ; la vue se met à jour toute seule.

Hôtes : ~/.config/claude-hub/hosts (un par ligne, « local » pour ce PC), ou le
bouton « + » de la fenêtre. Un hôte distant doit avoir claude, et python3 ou node.
"""
__version__ = "0.1.0"
