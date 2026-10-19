# Local workflow for the blog. Zola's version and checksum come from the publish
# workflow, so the local build always matches the one GitHub Actions runs.
WORKFLOW := .github/workflows/publish.yml
ZOLA_VERSION := $(shell sed -n 's/^ *ZOLA_VERSION: *//p' $(WORKFLOW))
ZOLA_SHA256 := $(shell sed -n 's/^ *ZOLA_SHA256: *//p' $(WORKFLOW))
ZOLA := .bin/zola
PORT ?= 1111

.PHONY: help deps serve drafts check build clean

help:
	@echo "make deps     Zola $(ZOLA_VERSION) into .bin/ and the tabi theme submodule"
	@echo "make serve    preview the published posts on http://127.0.0.1:$(PORT) (live reload)"
	@echo "make drafts   preview including drafts (draft = true)"
	@echo "make check    zola check, including drafts (run before committing)"
	@echo "make build    build the site into public/, as CI does"
	@echo "make clean    remove public/"

deps: $(ZOLA) themes/tabi/theme.toml

$(ZOLA): $(WORKFLOW)
	@mkdir -p .bin
	curl -sSfL -o .bin/zola.tar.gz "https://github.com/getzola/zola/releases/download/v$(ZOLA_VERSION)/zola-v$(ZOLA_VERSION)-x86_64-unknown-linux-gnu.tar.gz"
	echo "$(ZOLA_SHA256)  .bin/zola.tar.gz" | sha256sum -c -
	tar -xzf .bin/zola.tar.gz -C .bin zola
	rm .bin/zola.tar.gz
	touch $(ZOLA)

themes/tabi/theme.toml:
	git submodule update --init

serve: deps
	$(ZOLA) serve --port $(PORT)

drafts: deps
	$(ZOLA) serve --drafts --port $(PORT)

check: deps
	$(ZOLA) check --drafts

build: deps
	$(ZOLA) build

clean:
	rm -rf public
