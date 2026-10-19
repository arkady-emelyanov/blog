# The blog: Zola 0.23 with the tabi theme (a submodule), published to
# blog.emelianov.cloud by GitHub Actions.
#
# The zola binary lives in .bin/ and the Python environment the card generator
# needs in .venv/. Neither is in git, so a fresh clone sets them up itself.

ZOLA ?= .bin/zola
PYTHON ?= .venv/bin/python
PORT ?= 1111

.PHONY: help run check build cards

help:
	@echo "Targets:"
	@echo "  run     Serve the site on http://127.0.0.1:$(PORT), rebuilding as files change"
	@echo "  check   Validate the content and every link, without writing anything"
	@echo "  build   Write the published site to public/"
	@echo "  cards   Regenerate the AI lab social cards from each post's front matter"
	@echo
	@echo "PORT=<n> serves on another port; drafts/ is outside content/ and is never built."

run:
	$(ZOLA) serve --port $(PORT)

check:
	$(ZOLA) check

build:
	$(ZOLA) build

cards:
	$(PYTHON) tools/social-cards.py
