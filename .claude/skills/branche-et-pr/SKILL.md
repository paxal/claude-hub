---
name: branche-et-pr
description: Circuit de tout changement de code de claude-hub (fonctionnalité, correction de bug, refonte) — worktree et branche à partir d'origin/master, commits et push sur la branche, pull request, attente de la CI, puis proposition de merge (squash) avec le lien de la PR. À utiliser dès qu'on s'apprête à modifier le code, avant la première modification (après le feu vert sur la conception). Pas pour une retouche de doc seule, qui reste sur master.
---

# Travailler sur une branche, avec une pull request

Tout changement de code passe par un worktree, une branche poussée et une pull
request. L'invocation de cette skill vaut accord pour commiter et pousser **sur la
branche** et pour ouvrir la PR. Le merge, lui, attend toujours l'accord de
l'utilisateur. Une retouche de doc seule (README, `docs/`, AGENTS.md) reste sur
master, avec un commit seulement sur demande.

## 1. Démarrer

Après le feu vert sur la conception, avant le premier changement :

```sh
git fetch origin
git status --short                     # dans la copie principale
git rev-parse master origin/master
```

- Les modifications non commitées de la copie principale ne suivent pas dans le
  worktree. Si elles concernent la tâche, demander quoi en faire ; sinon, le
  signaler en une ligne et ne pas y toucher (ni stash, ni reset).
- Si `master` a des commits non poussés, le signaler : la branche part
  d'`origin/master`.
- Nom : court, en kebab-case ASCII, d'après la tâche (`notes-markdown`,
  `fuite-widgets`). Il sert pour le dossier et la branche.

```sh
git worktree add --no-track -b <nom> .claude/worktrees/<nom> origin/master
```

`--no-track` : sans lui, la branche suivrait `origin/master`, et un `git push`
nu viserait master.

Puis `EnterWorktree(path: "<racine>/.claude/worktrees/<nom>")` : la session
travaille désormais dans le worktree. Si elle y est déjà (session reprise), on
continue dedans.

## 2. Travailler

- Tous les chemins (Read/Edit/Write, commandes) sont ceux du worktree : rien ne
  s'écrit dans la copie principale, où l'utilisateur peut travailler en même
  temps.
- `make` et `tests/piloter.sh` se lancent depuis le worktree : ils font tourner
  le code du worktree (`piloter.sh` et `bin/claude-hub` partent de leur propre
  dossier).
- Le stash est commun à tous les worktrees : pas de `git stash` nu. Pour mettre
  du travail de côté, un commit temporaire sur la branche.
- Commits sur la branche au fil du travail, en français, dans le style du dépôt
  (titre court, puis le pourquoi et le comment), terminés par la ligne
  `Co-Authored-By` demandée par Claude Code. Ils seront réunis au merge.
- Doc à jour dans la même branche (README, `docs/`, tableau des modules de
  `docs/developpement.md`, AGENTS.md si on a appris quelque chose).

## 3. Envoyer et ouvrir la PR

Écrire d'abord le corps de la PR dans un fichier du scratchpad (outil Write) :

```markdown
<résumé : quoi et pourquoi>

## Tests
<ce qui a été testé, et ce qui ne l'a pas été>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

puis :

```sh
git push -u origin <nom>
gh pr create --base master --head <nom> --title "<titre>" --body-file <fichier>
```

- Pas de heredoc (`--body-file -`) : dans une session isolée dans un worktree,
  Claude Code peut refuser une commande gh dont le texte cite des commandes git.
- Titre et corps en français ; le titre servira de titre au commit sur master.
- Le dépôt est **public** : ni jeton, ni chemin ou contenu de conversation
  privé, ni données de l'utilisateur dans la PR.
- `gh pr create` affiche l'URL de la PR : la garder pour la proposition.

## 4. Attendre la CI

GitHub Actions lance `make test` sur chaque PR (`.github/workflows/tests.yml`,
quelques minutes : installation des paquets).

Suivre le run du dernier commit poussé, en commandes simples et séparées (le
garde-fou du worktree refuse un `$(git …)` imbriqué) :

```sh
git rev-parse HEAD
gh run list --branch <nom> --commit <sha> --limit 1 --json databaseId,status --jq '.[0]'
gh run watch <id> --exit-status --interval 15 >/dev/null 2>&1; echo "exit $?"
gh run view <id> --json conclusion --jq .conclusion
```

- `gh run watch` en arrière-plan (`run_in_background`) : la CI peut dépasser le
  délai d'une commande.
- Pas `gh pr checks --watch` : tant que le run est en file d'attente, il répond
  « no checks reported » avec un code de sortie 0, et l'on croirait la CI finie.
- Si `gh run list` ne renvoie rien, le run n'est pas encore créé : relancer un
  peu plus tard.
- Un nouveau push annule le run en cours (`cancel-in-progress`) : suivre celui
  du nouveau commit.
- En cas d'échec : lire le journal (`gh run view <id> --log-failed`), corriger sur
  la branche, commiter, pousser, attendre de nouveau.

## 5. Proposer le merge

Donner le lien de la PR dans le message, puis demander avec `AskUserQuestion`,
**le lien dans la question elle-même** (le dialogue peut masquer le texte qui le
précède) : « La PR #N (<titre>) est prête, CI verte : <url>. La merger dans
master ? », avec les options « Merger (squash) » et « Pas maintenant ».

Si la CI a échoué sans qu'on puisse la corriger, ou si une partie n'a pas pu être
testée, le dire dans la question.

## 6. Si l'utilisateur accepte

Merge en squash : un commit par changement, master reste linéaire. Le message du
commit (détaillé, comme un commit du dépôt, terminé par la ligne
`Co-Authored-By`) s'écrit lui aussi dans un fichier du scratchpad.

```sh
gh pr merge <N> --squash --subject "<titre> (#N)" --body-file <fichier>
gh pr view <N> --json state --jq .state     # doit afficher MERGED
git push origin --delete <nom>
```

Pas de `--delete-branch` : depuis un worktree, gh essaie d'extraire `master`, déjà
extrait dans la copie principale, et échoue.

Puis `ExitWorktree(action: "keep")` pour revenir dans la copie principale
(ExitWorktree ne supprime pas un worktree créé avec `git worktree add`), et :

```sh
git worktree remove .claude/worktrees/<nom>
git branch -D <nom>       # -d refuse après un squash : normal, la PR est MERGED
git pull --ff-only        # si la copie principale est sur master
```

Si `git pull --ff-only` refuse à cause de modifications locales de
l'utilisateur : ni stash, ni reset, ni checkout ; le dire et le laisser faire.
Vérifier `git log --oneline -3` et `git worktree list`, puis rappeler de relancer
claude-hub pour voir le changement.

## 7. Si l'utilisateur refuse ou remet à plus tard

Le worktree et la branche restent. Rappeler le lien de la PR. Les corrections
suivantes se font dans le même worktree : un commit et un `git push` mettent la
PR à jour, puis on repasse par les étapes 4 et 5.
