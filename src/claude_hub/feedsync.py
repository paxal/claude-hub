"""Relecture du fil : ce qui a changé depuis la lecture précédente.

La transcription d'une conversation ne fait que grandir : une nouvelle lecture
redonne les mêmes éléments, suivis des nouveaux. Seuls les derniers peuvent
changer (sortie d'une commande « ! » rattachée à sa commande, sous-agent du cloud
qui avance). Le fil garde donc ses éléments et n'ajoute que ce qui est nouveau ou
a changé, au lieu d'être reconstruit (Markdown compris) à chaque relecture.

Chaque lecture donne une fenêtre de la transcription : ses `limit` derniers
éléments, dont le premier est à l'index total - len(items).
"""


def same_item(a, b):
    """Même élément d'une lecture à l'autre. Les images comptent par leur nombre : le
    budget des scripts distants peut retirer le contenu d'une ancienne image quand une
    nouvelle arrive, sans que l'élément change."""
    def key(it):
        return {k: len(v) if k == "images" and isinstance(v, list) else v for k, v in it.items()}
    return a is b or key(a) == key(b)


def changed_from(shown, shown_start, items, start):
    """Index (dans la transcription) du premier élément affiché à refaire.

    shown : éléments affichés, le premier à l'index shown_start ; items : ceux de la
    nouvelle lecture, le premier à l'index start. Renvoie la fin de shown si rien n'a
    changé (ce qui suit dans items est à ajouter), None s'il faut tout reconstruire :
    rien d'affiché, transcription raccourcie, ou trou entre les deux lectures."""
    end = shown_start + len(shown)
    if not shown or start < shown_start or start > end:
        return None
    total = start + len(items)
    for at in range(start, min(end, total)):
        if not same_item(shown[at - shown_start], items[at - start]):
            return at
    return min(end, total)
