.PHONY: install context test demo metrics

install:
	python -m pip install -e ".[dev]"

# Regenerate manifest + sync CLAUDE.md/GEMINI.md from AGENTS.md.
context:
	./scripts/refresh-context.sh

test:
	pytest

# Whole-MVP acceptance: 3-agent handoff chain over this repo, with token totals.
demo:
	pigeon demo

metrics:
	pigeon metrics
