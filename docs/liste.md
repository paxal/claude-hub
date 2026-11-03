# Trouver et organiser ses conversations

← [README](../README.md)

## Hôtes affichés

Le bouton du haut de la liste ouvre un menu : les hôtes dont on veut voir les
conversations (ce PC, hôtes SSH, cloud), les sources (Claude Code, Cursor, et
les conversations archivées, avec leur nombre) et « Ajouter un hôte SSH… ». Le
choix est gardé d'un lancement à l'autre. Avec plusieurs hôtes, leurs conversations sont mêlées, triées par
dernier message, et chaque ligne porte un badge à son hôte. Chaque hôte
s'affiche dès qu'il répond : un hôte lent ou injoignable ne retarde pas les
autres, et son erreur s'affiche sous la liste. La recherche dans le contenu, le
filtre `dir:` et le suivi des états (en cours, terminé…) portent sur tous les
hôtes cochés.

## Accueil et nouvelle conversation

Sans conversation affichée (au lancement, ou avec le bouton « Nouvelle »), la
fenêtre montre l'accueil :

- **À traiter** : une carte par conversation qui attend quelque chose. Un plan à
  valider (claude lancé d'ici) se lance (« Lancer », en modifications acceptées)
  ou se renvoie en planification depuis la carte ; une question ou une
  autorisation, ou une conversation qui attend dans son terminal, s'ouvre d'un
  clic ; une réponse non lue montre le début de la réponse (lu sur l'hôte la
  première fois), avec « Ouvrir » et « Marquer comme lue » ;
- **En cours** : les conversations où Claude travaille, ici, dans un terminal ou
  dans Cursor.

Les archivées n'y figurent pas. La saisie de l'accueil démarre une nouvelle
conversation : l'hôte et le dossier se choisissent au-dessus du texte (le menu
du dossier propose les dossiers récents de l'hôte, un chemin à saisir, ou
« Parcourir… »), et « Démarrer » (ou Entrée) la crée avec ce premier message ;
elle apparaît alors en tête de la liste. **Ctrl+N** et le bouton « nouveau
document » de l'en-tête ouvrent l'accueil sur le dossier et l'hôte de la
conversation affichée (s'il existe encore) ; « Nouvelle » aussi.

Chaque conversation garde sa propre saisie en cours (texte et images) quand on
passe à une autre, marquée « brouillon » (crayon) dans la liste ; l'accueil garde
aussi la sienne. Les brouillons restent en mémoire : fermer claude-hub perd ceux
qui n'ont pas été envoyés.

## Liste des conversations

Les conversations sont regroupées par jour (« Aujourd'hui », « Hier »,
« Samedi 31 octobre »…), avec l'heure du dernier message à droite. L'icône de
gauche donne l'état : pastille bleue (réponse non lue : titre en gras, heure en
bleu), roue orange (Claude travaille : « en cours »), triangle rouge (plan,
question ou autorisation à valider : « à valider »), coche verte (terminé, la
session reste ouverte dans un terminal), crayon (brouillon). La roue est fixe :
une animation par ligne ferait redessiner la fenêtre en continu. Une petite
feuille bleue, devant le dossier, signale une
[note](conversation.md#notes) ; son début s'affiche en info-bulle.

Les boutons « Toutes / À traiter / En cours », sous la recherche, filtrent la
liste par état ; les pastilles comptent les conversations à traiter (non lues et
demandes) et en cours. La conversation affichée reste visible quel que soit le
filtre. Au survol d'une ligne, l'heure laisse la place aux boutons tags, épingle
et archive. Le pied de liste donne le nombre de conversations et d'archivées.

Le nombre de conversations à traiter (réponses non lues, plans, questions et
autorisations en attente, sur les hôtes cochés, hors archives) s'affiche aussi
sur l'icône de Claude Hub dans la barre des tâches. KDE le lit dans le signal D-Bus
`com.canonical.Unity.LauncherEntry` (celui des docks d'Unity) ; la pastille
disparaît à zéro et quand on ferme la fenêtre.

## Épingles et vues enregistrées

Une conversation **épinglée** (bouton épingle au survol, clic droit ou menu ⋮ :
« Épingler ») reste en tête de la liste, sous « Épinglées », quelle que soit sa
date. Les filtres et la recherche s'y appliquent comme au reste.

Une **vue** est une recherche gardée (`#stockly`, `dir:"~/chansons scout"`,
`bazel #stockly/ops`…). Quand la recherche n'est pas déjà une vue, « Enregistrer
la vue » apparaît sous elle ; les vues s'y affichent ensuite en pastilles. Un clic
applique la recherche, un second l'enlève ; le clic droit propose de supprimer la
vue.

## Tags et filtres

Le bouton étiquette d'une conversation (dans la liste ou dans l'en-tête) ajoute
ou retire des tags. Un tag avec des « / » se range comme dans un dossier :
`stockly/bazel`, `stockly/ops`…

Dans la recherche, on peut combiner :

- `#stockly` : conversations taguées `stockly` ou `stockly/…`, **sur tous les
  hôtes** (celles d'un autre hôte portent un badge à son nom) ;
- `#stockly/` : seulement ce qui est rangé sous `stockly/` ;
- `dir:~/devel/gauth` : conversations lancées dans ce dossier ou en dessous
  (`dir:"~/mes docs"` si le chemin contient des espaces) ;
- du texte libre : titres, notes (le passage trouvé s'affiche sous la ligne,
  « Note : … »), puis contenu des conversations de l'hôte affiché.

Les boutons étiquette et dossier à droite de la recherche (listes des tags et
des dossiers de travail) et les pastilles des conversations remplissent ces
filtres ; un second clic les retire.

## Clavier

Sous macOS, Cmd remplace Ctrl.

| Touches | Action |
|---|---|
| **Ctrl+K** | sélecteur rapide : taper quelques lettres d'une conversation, ↑/↓, Entrée |
| **Alt+↑ / Alt+↓** | conversation précédente / suivante de la liste (filtre compris) |
| **Alt+Maj+↑ / Alt+Maj+↓** | conversation à traiter précédente / suivante (non lue ou demande), en bouclant |
| **Ctrl+N** | nouvelle conversation (accueil) dans le dossier de celle affichée |
| **Maj+Tab** | mode automatique ⇄ plan (dans la saisie) |
| **Échap** | arrêter la réponse (dans la saisie) |
| **Entrée**, **Ctrl+Entrée**, **Alt+Entrée** | envoyer, tout de suite, en file (voir [Écrire pendant une réponse](conversation.md#écrire-pendant-une-réponse)) |

Le sélecteur (Ctrl+K) cherche de façon approximative : les lettres de chaque mot
doivent apparaître dans l'ordre, pas forcément à la suite (« sepjr » trouve
« Séparateurs de jour »), dans le titre, le dossier, l'hôte ou les tags, sans
tenir compte des accents. Les débuts de mots et les lettres consécutives comptent
davantage, le titre plus que le reste ; les lettres trouvées sont en gras. Sans
texte, il montre les épinglées puis les plus récentes. Il porte sur les
conversations de la liste (hôtes et sources cochés).
