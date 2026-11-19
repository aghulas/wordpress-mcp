# wordpress-mcp

<!-- ci:debut — bloc déployé par l'outil deployer_ci.py du dépôt agrégateur, ne pas modifier ici -->
## Travailler à plusieurs sessions sur ce dépôt

Plusieurs sessions Claude interviennent sur `wordpress-mcp`. Pour qu'elles ne se gênent pas :

1. **Ne jamais modifier ni committer sur `main`**, et ne rien modifier dans `~/dev/wordpress-mcp` : ce
   dossier reste la copie de `main` (c'est lui que lance Claude Desktop).
2. **Une tâche = une branche `claude/<sujet>` dans son propre dossier de travail** :
   ```bash
   git -C ~/dev/wordpress-mcp fetch origin
   git -C ~/dev/wordpress-mcp worktree add ~/dev/_sessions/wordpress-mcp-<sujet> -b claude/<sujet> origin/main
   cd ~/dev/_sessions/wordpress-mcp-<sujet>
   ```
   Si le dépôt a des tests : `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`, puis
   `.venv/bin/python -m pytest -q` avant chaque push.
3. **Travail prêt = pull request** : `git push -u origin claude/<sujet>` puis `gh pr create --fill`
   (`--draft` tant que ce n'est pas fini, `gh pr ready` ensuite). Le CI (tests, skills, secrets) tourne ; s'il
   est vert, la pull request est **fusionnée et la branche supprimée automatiquement**. S'il est rouge :
   corriger sur la même branche et repousser.
4. **Conflit avec une autre session** : `git fetch origin && git rebase origin/main`, relancer les tests,
   `git push --force-with-lease`. Ne jamais forcer sur `main`.
5. **Après la fusion** : `git -C ~/dev/wordpress-mcp pull --ff-only`, `git -C ~/dev/wordpress-mcp worktree remove
   ~/dev/_sessions/wordpress-mcp-<sujet>`, et relancer Claude Desktop si le code du serveur a changé.
6. **Skills** : modifier seulement la copie que `<dépôt agrégateur>/skills/index.json` désigne. Ne pas
   lancer `generer_catalogues.py --ecrire` et ne rien pousser dans un dépôt `*-marketplace` : le CI de
   l'agrégateur régénère et pousse les marketplaces après la fusion.
<!-- ci:fin -->
