# The Control Plane (blog)

Zola 0.23 site with the tabi theme (submodule), published to https://blog.emelianov.cloud by GitHub Actions.

## Writing

- No staccato (telegraphic) fragments or counted lists in prose, such as "One domain, one leaf, one spine." or "Two switches, eight HCAs, four uplinks.": write full sentences that connect the facts.
- First person singular ("I'll", "I ran"); address the reader as "you". Never "we" or "let's".
- Plain, procedural prose: steps in the order the reader runs them, short sentences, say what happens and what to do next. No rhetorical set-ups, punchlines or "teaching" bridge sentences.
- Introduce every term, tool and helper where it first appears, before the output that uses it. After an output, say what to look at.
- No em or en dashes (`—`, `–`): use a plain hyphen for ranges ("5-10 KB/s"), commas or colons elsewhere. British spelling, no hard-wrapped Markdown.

## Formatting

- No Markdown tables (unreadable on phones): use bullet lists.
- List items that are fragments get no trailing commas, semicolons or full stops; items that are full sentences start with a capital and end with a full stop.
- `##` for sections, `###` for subsections; no bold run-in labels as pseudo-headings.
- Console blocks: a blank line between consecutive commands.
- Every ai-lab post starts, right after the front matter, with the series navigation and the table of contents, then `## Overview`; its `[extra]` has `toc = false` (tabi's own table of contents would sit above the navigation):

  ```
  <!-- series_intro -->

  <h3>Table of contents</h3>

  <!-- toc -->

  ## Overview
  ```

  The series is `content/ai-lab/_index.md` (`extra.series`, part order); the "All parts" heading with its row of links ("Part 1 · Part 2 · …", full titles on hover) comes from `templates/components/series_page.html` and `static/css/layout.css`.
- Side notes as tabi admonitions: `{% <admonition type="note" title="…"> %} … {% </admonition> %}`.
- Images with captions: `{{< figure src="x.png" alt="…" caption="…" />}}` (`templates/components/figure.html`). Grafana screenshots in the dark theme.


## Repository

- `.bin/zola check` before committing.
- No unused files: delete, don't keep spares.
- Commit when asked, with clear messages. Never push without explicit approval.
