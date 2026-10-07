# The Control Plane (blog)

Zola 0.23 site with the tabi theme (submodule), published to https://blog.emelianov.cloud by GitHub Actions. Posts: `content/ai-lab/NN-name/index.md`, about the lab in `~/Projects/cloud` (github.com/arkady-emelyanov/ai-lab).

## Writing

- Plain, procedural prose: steps in the order the reader runs them, short sentences, say what happens and what to do next. No rhetorical set-ups, punchlines or "teaching" bridge sentences. Part 1's Setup section is the model.
- First person singular ("I'll", "I ran"); address the reader as "you". Never "we" or "let's".
- Introduce every term, tool and `bin/` helper where it first appears, before the output that uses it. After an output, say what to look at.
- Say "emulated", not "fake" (`fake*` component names stay). Say "a single Linux machine".
- Link NVLink to Part 1 once per post. Link external tools and docs where they first appear, and verify the links.
- No reset or reboot timings.
- No em dashes, British spelling, no hard-wrapped Markdown.

## Formatting

- No Markdown tables (unreadable on phones): use bullet lists. For queries, the value first, then the PromQL on its own line (`\` line break).
- `##` for sections, `###` for subsections; no bold run-in labels as pseudo-headings.
- Console blocks: a blank line between consecutive commands.
- Side notes as tabi admonitions: `{% <admonition type="note" title="…"> %} … {% </admonition> %}`.
- Images with captions: `{{< figure src="x.png" alt="…" caption="…" />}}` (`templates/components/figure.html`). Grafana screenshots in the dark theme.

## Outputs and the lab

- Never invent or edit outputs. Capture them on the lab with the repository's `bin/` helpers, and re-capture when the lab changes.
- Other sessions run tests on the lab. Before anything disruptive (jobs, partitions, resets, power), check it's free. Don't run test suites unless asked. Leave the lab on the default partition, GPUs reset, no jobs.

## Repository

- `.bin/zola check` before committing. Cards: `tools/social-cards.py`; Part 1 overview: `tools/overview.py`.
- No unused files: delete, don't keep spares.
- Commit when asked, with clear messages. Never push without explicit approval.
