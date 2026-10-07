+++
title = "AI lab, part 1: a GB200 NVL72-style cluster, scaled down to your laptop"
date = 2026-09-28
description = "A GPU cluster modelled on NVIDIA GB200 NVL72, scaled down to two compute trays and emulated on a single Linux machine: what it is, what you can practise with it, and how to set it up."

[extra]
# Series navigation and table of contents are placed in the body (below).
toc = false
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/01-intro/card.png"
+++

<!-- series_intro -->

<h3>Table of contents</h3>

<!-- toc -->

## Overview

Most of the work in AI infrastructure isn't the GPUs themselves. It's everything around them:

- the scheduler that places an 8-GPU job inside one NVLink domain
- the BMC you use to power-cycle a tray that hangs
- the dashboard that shows who is holding 6 of the 8 GPUs
- the runbook for a broken NVLink

For systems and software engineers, this is hard to learn: you can't practise on a GB200 rack you don't have, and cloud GPU instances hide exactly these layers.

[ai-lab](https://github.com/arkady-emelyanov/ai-lab) is my attempt at a way around that. It's a complete GPU cluster that runs on a single Linux machine. The GPUs are emulated, but everything around them is real: the scheduler, the frameworks and the tools are the real software, unmodified. They run against a model of what a GB200 NVL72 rack, scaled down to two compute trays, shows to software (its APIs, topology, telemetry, timing and failures), not against the silicon. This is called software-in-the-loop testing. I call the scaled-down version NVL8, which is the lab's own name, not an NVIDIA product.

That works because most of those layers never use a GPU for computation:

- Slurm reads a GPU count from its configuration and sets `CUDA_VISIBLE_DEVICES` for each job.
- The monitoring exporter calls NVML, NVIDIA's management library.
- The BMC answers Redfish requests over HTTPS.

All of that works against GPUs that answer the right API calls, whether or not they compute anything. It's like a stub in a unit test: the GPU stack is replaced with an implementation of the same APIs, and the rest of the cluster runs against it unchanged.

{{< figure src="overview.png" alt="AI lab at a glance: the emulated NVL8 domain (NVLink switch tray, two GPU trays with 4 × GB200 each, BMCs, InfiniBand) plus login, control and storage instances, all inside a single Linux machine" caption="AI lab overview: the NVL8 domain and the platform instances" />}}

{% <admonition type="note" title="What the lab is not for"> %}
- Benchmarking or capacity planning from the lab's timings
- Checking numerical results or model accuracy
- Developing or tuning CUDA kernels
- Rehearsing hardware failures the lab doesn't model

[What is real and what is modelled](#what-is-real-and-what-is-modelled) below has the details.
{% </admonition> %}

## What the lab is made of

The hardware side is one **NVL8** NVLink domain: an 8-GPU slice of a GB200 NVL72-style domain, with two compute trays and one NVLink switch tray. [NVLink](https://www.nvidia.com/en-us/data-center/nvlink/) is NVIDIA's GPU-to-GPU interconnect, and an NVLink domain is a set of GPUs that can all reach each other over it, even across trays, without going through the network. It's the unit a scheduler has to respect when it places a multi-GPU job. The lab's domain has:

- 2x GPU compute trays with 4x GB200 GPUs each
- an NVLink switch tray with 2x NVSwitch chips (144 ports)
- a Redfish BMC for every tray
- an InfiniBand fabric with a leaf and a spine switch

On top of that hardware runs the software of a real GPU cluster:

- **Slurm** or **Kubernetes** (k3s with Kueue) for scheduling
- OpenLDAP for users
- a shared filesystem, per-user S3 buckets and node-local scratch for storage
- Prometheus and Grafana for monitoring

Everything runs in Incus containers, set up by Ansible behind a `Makefile`. These are the same components a real cluster runs, except the Kubernetes device plugin, which the lab replaces with its own ([Part 3](@/ai-lab/03-kubernetes/index.md)).

{% <admonition type="note" title="Hardware parameters"> %}
The emulated hardware is set by variables. `inventory/group_vars/all.yml` has the defaults, the GB200 values used throughout this series. To change one, add it to `local.yml` and run `make configure`, which applies it without rebuilding the lab:

- **GPU:** name, memory, NVLinks per GPU and their speed, power limit, and the compute and memory throughput the timing model uses
- **Driver and firmware:** driver, CUDA and VBIOS versions, and the BMC, HMC and NVSwitch firmware, per tray if needed
- **InfiniBand:** the link rate, from SDR to XDR
- **NVLink domain:** the cluster UUID and the default clique ID

The lab's size (trays, GPUs per tray, switches) is fixed. "Hardware parameters" in the lab's [`docs/platform.md`](https://github.com/arkady-emelyanov/ai-lab/blob/main/docs/platform.md) links to every variable.
{% </admonition> %}

## What is real and what is modelled

Each GPU tray has `/dev/nvidia0-3` and stub versions of NVIDIA's software: the CUDA driver, NVML, cuBLAS, cuDNN, NCCL and `nvidia-smi`. The stubs implement the real APIs, so PyTorch and Ray run unmodified on top of them. Everything above the stubs is real:

- **Slurm, k3s, Kueue, JobSet, topograph and GPU Feature Discovery** are real, with real configuration, scheduling, accounting and topology-aware placement. A few things differ from a production cluster. Slurm jobs aren't confined by cgroups, because the trays are containers that share the host kernel. Slurm is version 23.11, which doesn't have the newer options for placing jobs across blocks (`--segment` and `BlockSizes`). And k3s gets its GPUs from the lab's own device plugin rather than NVIDIA's.
- **PyTorch, Ray and your applications** are real and unmodified. They initialise, set up process groups and handle the errors the APIs below them return. Only their numerical results are meaningless.
- **Prometheus, Grafana, LDAP and the storage** are real.

The hardware and NVIDIA's software are modelled:

- **CUDA, cuBLAS and cuDNN** report the GPU's properties, manage memory up to the GPU's size (and run out of memory beyond it), and time every operation. Kernels don't run, and tensors hold zeros.
- **NVML and `nvidia-smi`** report the GPU's identity, NVLink state, NVLink partition, NUMA layout and processes, and support GPU reset. Utilisation, power and temperature follow a model of the load, not measured curves.
- **NCCL** creates communicators and times collectives over NVLink inside a partition and over InfiniBand across partitions. No data is exchanged, so a dead peer or a broken link doesn't make a collective fail as it would on real hardware.
- **NVLink and NVSwitch** have 18 links per GPU to two 72-port switches, partitions that take effect at GPU reset, links that can be disabled, and fabric telemetry. There is a single NVL8 domain, enough to exercise every interface but not to study a large cluster.
- **The Grace CPUs** aren't emulated. The trays run on the host's x86-64 cores, so `scontrol show node` reports `Arch=x86_64` where a GB200 tray would report `aarch64`.
- **The BMCs** follow the GB200 Redfish layout, with power actions that really stop and start the tray, GPU sensors and firmware inventory. IPMI and BlueField DPUs aren't modelled.
- **InfiniBand** has a topology that tools like topograph can discover, byte counters on the network cards, and its 400 Gb/s link rate in NCCL timing. No packets are sent.

Timing is a behavioural model, not a prediction. Each operation's duration is derived from NVIDIA's published GB200 figures (compute rate, memory, NVLink and InfiniBand bandwidth) and applied to its size. The figures are simplified: one tensor rate covers all low-precision types, and some rates are estimated. Jobs take a plausible time and put a plausible load on the GPUs and links, but the figures aren't calibrated against hardware and don't predict real GB200 performance. The full list is under "What is real and what is modelled" in the lab's [README](https://github.com/arkady-emelyanov/ai-lab).

`nvidia-smi topo -m` shows how the GPUs in one tray are connected to each other. Here it runs on the first GPU tray, `sched-worker1`:

```console
$ bin/ssh sched-worker1 nvidia-smi topo -m
GPU0	GPU1	GPU2	GPU3	CPU Affinity	NUMA Affinity	GPU NUMA ID
GPU0	X	NV18	NV18	NV18	0-1	0		2
GPU1	NV18	X	NV18	NV18	0-1	0		10
GPU2	NV18	NV18	X	NV18	6-7	1		18
GPU3	NV18	NV18	NV18	X	6-7	1		26
```

Read it as a table: each row and each column is one of the tray's four GPUs, and each cell says how that pair is connected.

- `X`: the GPU itself
- `NV18`: the two GPUs are connected through 18 NVLinks, which carry their traffic together. At 50 GB/s per link, that's 900 GB/s in each direction
- `CPU Affinity`: the CPU cores closest to the GPU. The lab splits each tray's cores between its two emulated Grace CPUs, so GPUs 0-1 get the first half and GPUs 2-3 the second; the numbers depend on which host cores the tray was given
- `NUMA Affinity`: the NUMA node of the CPU closest to the GPU. A GB200 tray has two Grace CPUs, so GPUs 0-1 sit next to node 0 and GPUs 2-3 next to node 1
- `GPU NUMA ID`: the NUMA node of the GPU's own memory (2, 10, 18 and 26). On GB200, the Grace CPU and the GPU can read and write each other's memory directly, so Linux treats each GPU's memory as another NUMA node: memory without CPUs, further from the CPU than its own

The table shows the lab's current state, so it changes when the hardware does. Disable two of a GPU's NVLinks through the BMC, and `NV18` becomes `NV16` for that GPU ([Part 4](@/ai-lab/04-bmc-redfish/index.md)).

A change travels through the whole stack the way it would on a real cluster. This is what happens when one tray is moved into an NVLink partition of its own ([Part 5](@/ai-lab/05-nvlink/index.md)):

1. The partition controller moves the tray's GPUs to the new partition. As on GB200, they keep their old clique until they're reset.
2. After a GPU reset, NVML reports the new clique (`nvidia-smi --query-gpu=fabric.cliqueId`).
3. Within a minute, topograph rewrites Slurm's topology: one block per tray instead of one for both. With Kubernetes, it relabels the nodes instead.
4. Slurm prefers to keep a job inside one block. An 8-GPU job no longer fits in one, so it spans both.
5. The emulated NCCL sends the traffic between the two halves over InfiniBand instead of NVLink, and the same training job takes 2:39 instead of 1:17.
6. Prometheus shows the shift: InfiniBand traffic rises to about 120 GB/s per tray (240 GB/s for the domain), and NVLink traffic falls from 3.52 to 1.36 TB/s.

{{< figure src="/ai-lab/05-nvlink/panel-nvlink-vs-ib.png" alt="Grafana panel: NVLink traffic at about 3.5 TB/s and no InfiniBand traffic during the first run; during the second run NVLink falls to about 1.4 TB/s and InfiniBand rises to about 240 GB/s" caption="NVLink vs InfiniBand traffic: the same DDP job on one partition, then split across two" />}}

The times come from the timing model, so what matters is the chain of effects and their direction, not the exact numbers.

## What you can practise

- **Scheduler work.** Write and test job portals, quota tools, scheduler plugins and user CLIs against a real Slurm (GRES, accounting, `topology/block`) or a real Kubernetes setup (device plugin, GPU Feature Discovery, Kueue topology-aware scheduling, JobSet).
- **Monitoring and operations.** Build dashboards and alerts on live GPU, scheduler and NVLink metrics, and rehearse drains, power cycles, link failures and partition changes.
- **Hardware management tooling.** Point Redfish clients and BMC automation at three BMCs modelled on NVIDIA's OpenBMC fork, and drive an NMX-C-style partition controller over gRPC.
- **AI pipelines.** Run [PyTorch DDP](https://docs.pytorch.org/docs/stable/notes/ddp.html) (distributed data-parallel training) and [Ray](https://github.com/ray-project/ray) (distributed Python) end to end on 8 GPUs to test orchestration, data movement and failure handling. The numerics are meaningless, but the plumbing is real.
- **CI for infrastructure code.** `make up && make test` builds and verifies the whole cluster from scratch.

## Requirements

- x86-64 Linux with [Incus](https://linuxcontainers.org/incus/), plus `make`, Python 3, Go, `jq` and git
- Your user in the `incus-admin` group
- **RAM:** 16 GiB free (the running lab uses about 10 GiB)
- **CPU:** 4 cores or more (with enough cores, `make up` gives each GPU tray cores of its own, so the trays don't slow each other down)
- **Disk:** about 25 GB under `/var/lib/incus`, including a 5.6 GB PyTorch/Ray virtualenv

You don't need a GPU, an NVIDIA driver or a cloud account.

## Setup

Clone the repository and check the host first:

```
git clone https://github.com/arkady-emelyanov/ai-lab.git && cd ai-lab
make check
```

If something is missing, `make check` prints the command for you to run. Usually that's adding your user to a group, an AppArmor rule for Incus DNS, or raising a couple of kernel limits. Run `make check` again until it passes:

```console
$ make check
host ready
```

Prepare the host side (Ansible venv, secrets, Go builds, and your settings file `local.yml`):

```
make init
```

Choose the scheduler. `local.yml` holds your settings, and one line in it picks the scheduler:

```yaml
scheduler: slurm        # or: k3s
```

Then build the cluster:

```
make up            # create and configure the cluster (~15 minutes)
make frameworks    # PyTorch + Ray on the shared volume (optional, several GB)
make test          # end-to-end checks
```

To switch the scheduler on a built cluster, run `make down`, change the line, then `make up`. Volumes survive the rebuild, so homes, the frameworks venv and object data are kept. The two modes never run side by side, but everything below the scheduler (GPUs, BMCs, fabric, storage, monitoring) is identical.

The containers install their packages from Ubuntu's main mirror. If it's slow or times out from your network, set a closer [Ubuntu mirror](https://launchpad.net/ubuntu/+archivemirrors) in `local.yml` and run `make up` again. It continues where it stopped:

```yaml
apt_mirror: http://mirrors.ocf.berkeley.edu/ubuntu
```

{% <admonition type="tip" title="Setting up with a coding agent"> %}
If you use an AI coding agent such as Claude Code, Codex or Cursor, you can ask it to set up the lab. The repository's [`AGENTS.md`](https://github.com/arkady-emelyanov/ai-lab/blob/main/AGENTS.md) tells it how: check the host without changing it, ask you once for the commands that need root and why, then build. For a running lab, the [ai-lab skill](https://github.com/arkady-emelyanov/ai-lab/blob/main/skills/ai-lab/SKILL.md) teaches the agent to run jobs, check GPUs, change NVLink partitions and query metrics, and to leave the lab as it found it. Claude Code picks the skill up automatically in the repository.
{% </admonition> %}

## First contact

When `make up` finishes, you have nine containers, each with a pinned address:

```console
$ incus list --all-projects -c ns4 -f compact
         NAME          STATE           IPV4
  sched-control       RUNNING  10.107.111.10 (eth0)
  sched-login         RUNNING  10.107.111.11 (eth0)
  sched-nvswitch      RUNNING  10.107.111.34 (eth0)
  sched-nvswitch-bmc  RUNNING  10.107.111.33 (eth0)
  sched-storage       RUNNING  10.107.111.12 (eth0)
  sched-worker1       RUNNING  10.107.111.21 (eth0)
  sched-worker1-bmc   RUNNING  10.107.111.31 (eth0)
  sched-worker2       RUNNING  10.107.111.22 (eth0)
  sched-worker2-bmc   RUNNING  10.107.111.32 (eth0)
```

These map one to one onto the overview picture: the two GPU trays are `sched-worker1` and `sched-worker2`, the switch tray is `sched-nvswitch`, and every tray has its own `-bmc` container.

The lab has one regular user, `joe`, an OpenLDAP account that works on every node. The rest of the series uses `joe` for everything a cluster user would do.

The repository's `bin/` folder has small helper scripts for working with the lab from the host. `bin/ssh` logs in to a container with the lab's SSH keys, and `bin/redfish` sends a request to a tray's BMC. They find each container's address in Incus, so you never type an IP address:

```
bin/ssh login                    # login node as joe
bin/ssh root@sched-worker1       # any instance as root, with the generated admin key
bin/redfish sched-worker1 /redfish/v1/Systems/System_0   # a tray's BMC over Redfish (Part 4)
```

A tray looks like any other GPU node:

```console
$ bin/ssh sched-worker1 nvidia-smi
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.95.05          Driver Version: 580.95.05          CUDA Version: 13.0     |
+-----------------------------------------+------------------------+----------------------+
…
|   0  NVIDIA GB200                    On | 0000:18:00.0       Off |                    0 |
| N/A   32C    P8            138W / 1200W |     512MiB / 189471MiB |      0%      Default |
…
```

All of these values come from the stub NVML, including the driver version, the 189,471 MiB of memory and the 1,200 W power limit. The trays idle at about 140 W and 32 °C. Start a job and those numbers climb, and the same values show up in Prometheus (`http://10.107.111.10:9090`) and Grafana (`http://10.107.111.10:3000`, user `admin`, password in `.secrets/grafana.pass`). Keep the **Lab overview** dashboard open while you work through the rest of the series.

## Next in the series

[Part 2: Slurm](@/ai-lab/02-slurm/index.md) covers the scheduler: how Slurm is configured for GPUs, running jobs from `srun` up to PyTorch DDP across both trays, draining and resuming nodes, and watching it all in Prometheus.
