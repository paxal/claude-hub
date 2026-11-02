# AGENTS.md

Consignes pour les agents qui travaillent sur claude-hub. Le README et
`docs/` décrivent l'usage, `docs/developpement.md` les tests et
l'organisation des modules ; ce fichier ajoute ce qu'on a appris en
le développant.

## Le projet

Application GTK 4 (Python, PyGObject) qui liste et continue les conversations
Claude Code de ce PC, d'hôtes SSH (`cerberus.stockly.tech`, `rpi4`) et du cloud
(claude.ai/code, agents Cursor), plus les conversations Cursor locales.

- Né comme un script unique `~/.bin/claude-hub`, découpé ensuite en modules
  (choix explicite de l'utilisateur : des modules, pas un gros fichier).
- `~/.bin/claude-hub` est un lien vers `bin/claude-hub` : toute modification
  est prise en compte au prochain lancement, sans installation.
- `clh` (gestionnaire zellij dans `~/.bin`) est un outil à part : ne pas
  l'intégrer au projet.

## Principes à respecter

- **Ne jamais écrire dans les données des autres outils.** `~/.claude` n'est
  modifié que par `claude` lui-même (via `claude -p --resume`). Les bases
  Cursor (`state.vscdb`) sont ouvertes en lecture seule. L'archivage et les tags
  n'existent que dans `~/.config/claude-hub/` (`settings.json`, `tags.json`).
  Les notes vont dans `~/.config/claude-hub/notes/` **de l'hôte de la
  conversation** (ce PC pour le cloud) : lues par les scripts distants (`note`,
  `notes`, index dans `list`), écrites par `sh` avec le texte sur l'entrée
  standard (les scripts distants la prennent pour leur code, et un argument est
  limité à 128 Ko).
- **Scripts distants** (`remote/helper.py`, `remote/helper.js`) : bibliothèque
  standard uniquement, compatibles avec un python3 ancien. Les deux versions
  doivent rester équivalentes (même sortie, erreurs comprises) : certains hôtes
  n'ont que node (cerberus est un NixOS sans python3). Toute nouvelle commande
  s'ajoute aux deux, et on compare leurs sorties.
- **Le cloud passe par l'API interne** de Claude Code (celle de `--teleport`).
  Elle peut casser : une panne ne doit toucher que l'hôte cloud. Le jeton est lu
  dans `~/.claude/.credentials.json` et ne doit jamais être affiché ni journalisé.
- **Écrire à une session Remote Control** (`remote_send`) : par l'API, avec
  l'en-tête `anthropic-client-platform: web_claude_ai`. Sans lui (et avec
  `claude -p --cloud`), le Claude du terminal reçoit « Another Claude session
  sent a message » et ne prend pas le texte pour celui de l'utilisateur.
  Ce chemin ne doit transmettre que du texte que l'utilisateur a tapé.
  Les demandes en attente (question, plan, autorisation) se lisent dans
  `external_metadata.pending_actions` (`remote_pending`) et se répondent par une
  `control_response` sur le même chemin (`remote_respond`), toujours après un
  clic de l'utilisateur. Un `claude` de test lancé depuis une session Claude
  Code doit perdre les variables `CLAUDE*` : sinon il se croit sous-session et
  n'écrit pas sa transcription.
- **Plusieurs conversations à la fois** : chaque conversation a son propre
  processus `claude -p` (`Hub.streams`, par id de session). Ne pas réintroduire
  d'état global « le processus en cours » : c'était la cause du bug où le
  bandeau « Claude utilise Read… » et l'historique restaient bloqués après un
  changement de conversation.
- **Rendu** : `GSK_RENDERER=cairo` par défaut (`gtk.py`). Les moteurs `gl` et
  `vulkan` de GTK 4.20 laissent des restes quand un texte change (pixels blancs
  ou orange dans le bandeau d'activité). Ne pas revenir dessus sans le vérifier
  avec de vraies captures du compositeur.
- **Classes CSS** : `css_classes=[…]` passé au constructeur remplace les classes
  par défaut du widget : un `Gtk.Popover` perd `background` (menu sans fond),
  `MenuButton` `popup`, `ListBoxRow` `activatable`, `TextView` `view`. Utiliser
  `add_css_class`, ou `widgets.menu_popover` pour un menu.
- **Animations** : avec le rendu cairo, chaque `Gtk.Spinner` qui tourne redessine
  en continu (~20 % de CPU pour une demi-douzaine dans la liste, 0 % figés). N'en
  animer que dans la pastille de l'en-tête et le bandeau de la saisie ; ailleurs,
  l'icône fixe `claude-hub-busy-symbolic`. Mesurer le CPU au repos après un
  changement d'affichage (`os.times()` dans le script de pilotage), quelques
  secondes après la dernière action : sinon la remise en page du fil (volet
  ouvert ou fermé) compte, et l'on croit à une consommation continue.
- **Thèmes** : l'utilisateur est sous KDE/Breeze, qui ne définit pas toutes les
  couleurs GTK nommées. Détecter clair/sombre d'après la couleur réelle du
  texte, pas le nom du thème. Les icônes maison sont des SVG `-symbolic` dans
  `icons/` (à déclarer dans `pyproject.toml`).
- **Mode plan, permissions, sous-agents** : le protocole de contrôle de
  `claude -p` (`--permission-prompt-tool stdio`, `can_use_tool`,
  `parent_tool_use_id`, `subagents/`) est décrit, essais à l'appui, dans
  `docs/mode-plan-et-sous-agents.md`.
- **Icône de l'application** : sous Wayland, KDE retrouve l'icône par le
  `.desktop` nommé comme l'app_id (`org.paxal.ClaudeHub.desktop`), sinon il
  affiche le « W ». `install.sh` y met l'icône en chemin absolu : plasmashell
  ne voit une icône ajoutée au thème qu'après son redémarrage.
- **Pastilles qui passent à la ligne** : `Gtk.FlowBox` aligne ses enfants en
  colonnes dès la deuxième ligne, et les étire. Utiliser `widgets.WrapBox`.
- **Largeur de la barre latérale** : un texte long (chemin, statut, libellé de
  bouton) l'élargit et coupe les messages. Tronquer (ellipsize) et préférer des
  boutons à icône.

## Tester

- Tests unitaires : `python3 -m unittest discover -s tests`. node n'est pas
  installé sur ce PC : une quinzaine de tests échouent alors (`FileNotFoundError:
  'node'`), sans rapport avec le changement. Comparer `helper.py` et
  `helper.js` sur cerberus (node), dans un dossier temporaire supprimé ensuite.
- **Hors écran** : tout test d'interface passe par `tests/ecran-virtuel.sh`,
  qui lance la commande sur une KWin virtuelle (`kwin_wayland --virtual`) avec
  son propre bus D-Bus. Rien ne s'affiche chez l'utilisateur, et le
  presse-papier est isolé du sien. Ne lancer l'appli sur le vrai écran que si
  l'utilisateur le demande.
- Lancer l'appli **dans une instance séparée**, pour ne pas toucher à celle que
  l'utilisateur a ouverte, avec `tests/piloter.sh script.py` : config
  temporaire (qui garde le thème du bureau), faux `claude` dans le PATH, écran
  virtuel, puis vérification que `~/.config/claude-hub` n'a pas changé. Le
  script, dans un dossier temporaire, s'écrit avec `tests/pilote.py` ; le faux
  claude (`tests/faux-claude`) rejoue un scénario au choix (plan, autorisation,
  sous-agent…). Mode d'emploi : skill `piloter-claude-hub`
  (`.claude/skills/piloter-claude-hub/SKILL.md`). Pour regarder une capture,
  ouvrir le PNG avec l'outil de lecture.
- **Ne pas toucher à la config réelle** : pour les tests qui écrivent des tags,
  des hôtes ou des préférences, pointer `XDG_CONFIG_HOME` vers un dossier
  temporaire (attention : la base Cursor est alors introuvable, car `cursor.py`
  s'en sert aussi), ou remplacer `TAGS_FILE`, puis vérifier que
  `~/.config/claude-hub` n'a pas changé.
- **Pas de vraies sessions Claude inutiles** : utiliser un faux `claude` dans le
  PATH qui émet du stream-json (avec des pauses pour tester l'animation). Si un
  vrai appel est nécessaire, prendre Haiku dans un dossier temporaire et
  supprimer la conversation de test ensuite.
- **Presse-papier** : sur le vrai écran, les tests de copie/collage
  (`wl-copy`) écrasent celui de l'utilisateur. Sur l'écran virtuel, ils sont
  sans risque.
- **Défauts d'affichage** : une capture faite par GTK redessine tout et ne
  montre pas les défauts de rafraîchissement. Prendre de vraies captures
  (`spectacle`, qui passe par la KWin virtuelle). Thème clair : `GTK_THEME=Breeze` (ou `Adwaita`).
- **Gros volumes** : passer les vraies conversations (locales, cerberus,
  Cursor) dans le rendu Markdown a révélé plusieurs plantages ; refaire ce test
  après une modification de `render.py`.
- Supprimer les `__pycache__` créés par les tests.
- Dire précisément ce qui a été testé et ce qui ne l'a pas été (dialogues
  natifs, glisser-déposer, survol : non simulables).

## Façon de travailler avec l'utilisateur

- Échanges en **français**, comme les commentaires, docstrings et textes de
  l'interface. Le README et le guide (`docs/`) sont à tenir à jour, tableau
  des modules de `docs/developpement.md` compris.
- Pour une fonctionnalité ouverte (« tu proposes quoi ? »), proposer une
  conception avec une recommandation et des options, puis attendre le feu vert.
  Une demande directe (« ajoute… », « corrige… ») s'exécute sans détour.
- Quand on peut le vérifier soi-même, le faire plutôt que de demander à
  l'utilisateur de tester (« vas-y teste toi-même »).
- **Pas de commit** sans demande explicite. Rappeler à la fin qu'il faut
  relancer claude-hub pour voir les changements (l'instance ouverte garde
  l'ancien code).
- Si `python3` est refusé par les permissions, le dire immédiatement plutôt que
  de livrer du code non vérifié sans le signaler.
