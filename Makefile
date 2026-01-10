PYTHON ?= ./venv/bin/python
VENV ?= ./venv
VENV_PYTHON := $(VENV)/bin/python
FILES := cmus_status_scrobbler.py tests.py test_cmus_status_scrobbler.py

.PHONY: test typecheck format check venv

test:
	$(PYTHON) -m unittest $(FILES)

typecheck:
	$(PYTHON) -m mypy --strict --check-untyped-defs $(FILES)
	$(PYTHON) -m pyright $(FILES)

format:
	$(PYTHON) -m yapf -i $(FILES)

check: typecheck test

venv:
	python3 -m venv $(VENV)
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
