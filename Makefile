PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

.PHONY: help install install-dev install-test list-templates run dry-run test clean

help:
	@echo "Targets:"
	@echo "  make install        Create venv and install runtime deps"
	@echo "  make install-dev    Install in editable mode (creates 'aircover-pipeline' command)"
	@echo "  make install-test   Install test deps (pytest)"
	@echo "  make list-templates List coaching templates available to your account"
	@echo "  make run            Run the pipeline. Pass args via ARGS, e.g.:"
	@echo "                        make run ARGS='--start 2026-01-01 --end 2026-03-31 \\"
	@echo "                                       --template-id <id> --output-dir ./out'"
	@echo "  make dry-run        Same as 'run' but appends --dry-run"
	@echo "  make test           Run unit tests"
	@echo "  make clean          Remove venv, __pycache__, output dirs"

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)/bin/python
	$(PIP) install -r requirements.txt

install-dev: $(VENV)/bin/python
	$(PIP) install -e .

install-test: $(VENV)/bin/python
	$(PIP) install -e ".[test]"

test: install-test
	$(VENV)/bin/pytest tests/ -v

list-templates: $(VENV)/bin/python
	$(PY) aircover_pipeline.py --list-templates

run: $(VENV)/bin/python
	$(PY) aircover_pipeline.py $(ARGS)

dry-run: $(VENV)/bin/python
	$(PY) aircover_pipeline.py $(ARGS) --dry-run

clean:
	rm -rf $(VENV) __pycache__ *.pyc out output build dist *.egg-info
