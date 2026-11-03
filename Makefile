# Tâches courantes de claude-hub.
#
#   make                        tests unitaires
#   make test T=test_tags.py    un seul fichier (motif accepté : T='test_t*.py')
#   make test V=1               affiche le nom de chaque test
#   make clean                  supprime les fichiers générés

PYTHON ?= python3
T ?= test*.py
V ?=

# Pas de __pycache__, ni dans src/ et tests/ ni à côté des scripts distants.
export PYTHONDONTWRITEBYTECODE := 1

.PHONY: test clean

test:
	$(PYTHON) -m unittest discover -s tests -p '$(T)' $(if $(V),-v)

clean:
	find . -path ./.git -prune -o -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf build dist src/*.egg-info
