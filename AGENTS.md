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
  n'écrit pas sa transcription. Un `claude` en cours est relié à Remote Control
  si son fichier `~/.claude/sessions/<pid>.json` a `bridgeSessionId` : la
  transcription garde les `bridge-session` des lancements précédents. Sans
  Remote Control, ne pas compter sur la transcription pour montrer la question
  en attente : en mode auto, Claude Code ne l'écrit qu'une fois répondue
  (`docs/mode-plan-et-sous-agents.md`, « Terminal sans Remote Control »). Pour
  piloter un vrai `claude` interactif sans tmux : `pty.fork()`, puis taper le
  prompt une fois l'invite affichée (celui passé en argument se perd après le
  dialogue de confiance d'un dossier neuf).
- **Plusieurs conversations à la fois** : chaque conversation a son propre
  processus `claude -p` (`Hub.streams`, par id de session). Ne pas réintroduire
  d'état global « le processus en cours » : c'était la cause du bug où le
  bandeau « Claude utilise Read… » et l'historique restaient bloqués après un
  changement de conversation.
- **Fil mis à jour, pas reconstruit** : reconstruire 300 éléments (Markdown,
  coloration) fige la fenêtre une à deux secondes, et l'on perd les appels
  dépliés et le défilement. Après l'ouverture, `load_history` ne fait entrer que
  les éléments nouveaux ou changés (`update_feed`, `feedsync.py`). Tout élément de
  l'historique passe par `add_entry`, qui le retient dans `Hub.feed` ; ce qui suit
  le dernier d'entre eux (réponse en direct, avis, cartes) est remplacé à la
  relecture suivante, sauf les cartes encore en attente et les commandes « ! »
  pas envoyées.
- **Volet de droite par conversation** : un seul volet (plan, fichier,
  sous-agents, modifications, note) sert à toutes les conversations ; ce que
  chacune y avait ouvert est retenu dans `Hub.sides` en la quittant
  (`stash_side`) et rétabli en y revenant (`restore_side`). Une nouvelle page ne
  doit pas rester ouverte d'une conversation à l'autre : l'ajouter à
  `SIDE_PAGES` et à `side_state`/`restore_side`. Ouvrir ou fermer une page passe
  par `set_side_page` : GTK 4.22 garde l'onglet (`Gtk.StackSwitcher`) d'une page
  dont seul le widget est caché.
- **Widgets jamais libérés** : un gestionnaire branché sur un enfant ou un
  contrôleur d'un widget (`self.expander.connect(…, self.on_x)`, `GestureClick`
  avec `self.methode`, lambda qui capture le widget parent), ou un attribut d'un
  enfant qui désigne un ancêtre, forme un cycle qui passe par GTK. PyGObject ne
  le montre pas au ramasse-miettes : le widget reste en mémoire une fois retiré.
  Tout ce qui est recréé (fil, liste, cartes, popovers) fuyait ainsi : 4,5 Go et
  le CPU saturé après une journée. Passer par `widgets.weak_handler(self, fn)`, ou
  retrouver le widget depuis l'émetteur (`geste.get_widget()`,
  `bouton.get_ancestor(…)`). Pas de `weakref` de Python : PyGObject recrée
  l'objet Python d'un widget toujours affiché. `self.connect(…, self.methode)` sur
  le widget lui-même, ou un gestionnaire qui ne retient que la fenêtre, ne posent
  pas de problème. Vérifier avec `pilote.orphelins()`. Un widget hors fenêtre
  n'est pas forcément une fuite : un `Gtk.Expander` replié détache son contenu
  (`orphelins()` en tient compte). À la fermeture de l'appli, l'avertissement
  « Finalizing …Fit, but it still has children left » est sans conséquence
  (Python s'arrête avant le `do_dispose` du tableau).
- **Feuilles de style** : installées une seule fois, dans `do_startup`.
  `do_activate` est rappelé à chaque clic sur une notification et à chaque
  relance de `claude-hub` ; chaque feuille ajoutée fait recalculer le style de
  tous les widgets.
- **Rendu** : `GSK_RENDERER=gl` par défaut (`gtk.py`). Avec cairo, GTK 4.22
  sous Wayland repeint toute la fenêtre à chaque image : KWin garde le tampon
  affiché, GTK dessine dans deux tampons à tour de rôle et reporte sur l'autre
  toute sa zone à repeindre, arriéré compris (`gdkcairocontext-wayland.c`), si
  bien que la zone ne fait que grandir. Mesuré sur l'écran de l'utilisateur
  (1280×820 à l'échelle 1,45, longue conversation), cairo puis gl : roue de la
  liste d'activité 81 % / 13 %, réponse qui arrive 92 % / 19 %, défilement
  59 % / 16 %, saisie 19 % / 2 %. Avec GTK 4.20, gl et vulkan laissaient des
  restes quand un texte change (pixels blancs ou orange au-dessus d'une
  étiquette, ticket GTK #8340, encore ouvert) ; pas revus avec GTK 4.22 sur de
  vraies captures du compositeur. S'ils reviennent : `GSK_DEBUG=full-redraw`
  (gl, 30 % pour la roue) plutôt que cairo. Pour voir ce qui est repeint :
  `WAYLAND_DEBUG=client` et les `wl_surface.damage_buffer` (en gl, GTK annonce
  toujours toute la fenêtre au compositeur, mais ne redessine que ce qui a
  changé). Sur l'écran virtuel, gl passe en rendu logiciel (llvmpipe) : le CPU
  y sert à comparer deux versions, pas à juger le rendu ; le mesurer sur le
  vrai écran, avec l'accord de l'utilisateur.
- **Classes CSS** : `css_classes=[…]` passé au constructeur remplace les classes
  par défaut du widget : un `Gtk.Popover` perd `background` (menu sans fond),
  `MenuButton` `popup`, `ListBoxRow` `activatable`, `TextView` `view`. Utiliser
  `add_css_class`, ou `widgets.menu_popover` pour un menu.
- **Animations** : un `Gtk.Spinner` qui tourne fait redessiner la fenêtre en
  continu (13 % de CPU en gl pour la seule roue de la liste d'activité, 80 à
  90 % en cairo ; 0 % figé). N'en animer que dans la ligne de Claude de la
  liste d'activité (à côté de la saisie) ; ailleurs, l'icône fixe `claude-hub-busy-symbolic` ou une icône propre
  à la ligne. Mesurer le CPU au repos après un
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
- **Largeur de la saisie** : sa barre (modèle, effort, mode, Envoyer) demande
  environ 530 px. La liste d'activité (`activity.py`) s'ajoute donc à droite de la
  saisie, dans la marge (`Clamp(extra=…)`), plutôt que de la rétrécir ; elle ne
  passe au-dessus que quand la place manque vraiment.

## Tester

- **CPU ou mémoire anormaux sur l'instance ouverte** : l'observer sans la
  modifier. `/proc/PID/status` (`VmRSS`, et `VmSwap` : une fuite finit dans le
  swap), `top -H -p PID` (c'est le fil principal qui tourne ?), et quelques
  `eu-stack -p PID -1` à une seconde d'écart pour voir où il est. Ne pas y
  injecter de code (gdb) : reproduire ensuite dans une instance séparée
  (`tests/piloter.sh`, avec `pilote.orphelins()` et `pilote.cpu()`).
- Tests unitaires : `make` (un seul fichier : `make test T=test_tags.py`), qui
  n'écrit pas de `__pycache__`. node est installé sur ce PC : les tests qui
  comparent `helper.py` et `helper.js` tournent en local. Sans node, une
  quinzaine échouent (`FileNotFoundError: 'node'`) : comparer alors sur
  cerberus, dans un dossier temporaire supprimé ensuite.
- **Hors écran** : tout test d'interface passe par `tests/ecran-virtuel.sh`,
  qui lance la commande sur une KWin virtuelle (`kwin_wayland --virtual`) avec
  son propre bus D-Bus. Rien ne s'affiche chez l'utilisateur, et le
  presse-papier est isolé du sien. Ne lancer l'appli sur le vrai écran que si
  l'utilisateur le demande. Défaut connu : chaque lancement laisse un
  `ksecretd` démarré par ce bus (variable `ECRAN_VIRTUEL_BUS=1` dans son
  environnement), qui recrée `home/.local/share/kwalletd` dans le dossier
  `/tmp/claude-hub-pilote.*` après son nettoyage (« rm : … n'est pas vide »).
  Les arrêter à la fin, sans toucher au `ksecretd --pam-login` de la session.
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
- Supprimer les `__pycache__` créés par les tests (`python3 -B` n'empêche pas
  `py_compile` d'en écrire un).
- Dire précisément ce qui a été testé et ce qui ne l'a pas été (dialogues
  natifs, glisser-déposer, vrai pointeur : non simulables). Les contrôleurs
  (survol, clic droit, clic sur une image) se déclenchent en émettant leur
  signal, ce qui teste le gestionnaire mais pas le geste.

## Façon de travailler avec l'utilisateur

- Échanges en **français**, comme les commentaires, docstrings et textes de
  l'interface. Le README et le guide (`docs/`) sont à tenir à jour, tableau
  des modules de `docs/developpement.md` compris.
- Pour une fonctionnalité ouverte (« tu proposes quoi ? »), proposer une
  conception avec une recommandation et des options, puis attendre le feu vert.
  Une demande directe (« ajoute… », « corrige… ») s'exécute sans détour.
- Quand on peut le vérifier soi-même, le faire plutôt que de demander à
  l'utilisateur de tester (« vas-y teste toi-même »).
- **Tout changement de code passe par une branche** : skill `branche-et-pr`
  (`.claude/skills/branche-et-pr/SKILL.md`). Worktree dans
  `.claude/worktrees/`, commits et push sur la branche, pull request, CI, puis
  proposition de merge (squash) avec le lien de la PR. Le merge n'a lieu
  qu'après l'accord de l'utilisateur.
- **Sur master, pas de commit** sans demande explicite (retouches de doc
  seules). Rappeler à la fin qu'il faut relancer claude-hub pour voir les
  changements (l'instance ouverte garde l'ancien code).
- Si `python3` est refusé par les permissions, le dire immédiatement plutôt que
  de livrer du code non vérifié sans le signaler.
