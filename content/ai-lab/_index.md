+++
title = "AI lab"
description = "A GPU cluster emulated on a single Linux machine for systems engineers without GB200 hardware: setup, Slurm, Kubernetes, BMCs, NVLink, observability and networking."
template = "series.html"
sort_by = "slug"

[extra]
series = true
social_media_card = "card.png"

# Series navigation in each post (tabi reads these from the series section).
[extra.series_intro_templates]
default = "This article is part $SERIES_PAGE_INDEX of the $SERIES_HTML_LINK series."

[extra.series_outro_templates]
next_only = "Next: $NEXT_HTML_LINK"
middle = "Previous: $PREV_HTML_LINK · Next: $NEXT_HTML_LINK"
prev_only = "Previous: $PREV_HTML_LINK"
+++

[ai-lab](https://github.com/arkady-emelyanov/ai-lab) is a complete GPU cluster that runs on a single Linux machine: one NVIDIA GB200-class NVL8 NVLink domain, emulated in Incus containers, with Slurm or Kubernetes, BMCs, an NVLink switch tray, storage and monitoring around it. The GPUs are fake; everything around them is real.
