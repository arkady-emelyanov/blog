+++
title = "AI lab, part 10: NVIDIA Run:ai"
date = 2026-10-13
draft = true
description = "NVIDIA open-sourced Run:ai, so I ran it on the emulated GB200s, including its driver-enforced GPU fractions: two half-GPU workloads sharing one emulated GB200, each held to its own memory limit."

[extra]
# Series navigation and table of contents are placed in the body (below).
toc = false
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/10-runai/card.png"
+++

<!-- series_intro -->

<h3>Table of contents</h3>

<!-- toc -->

## Overview

NVIDIA has open-sourced Run:ai, its AI workload and GPU orchestration platform, as part of the DSX AI Factory platform. That makes it something you can run without a licence, which makes it something the lab can run, so I did.

With `scheduler: k3s` and `runai: true` in `local.yml`, `make provision` and `make configure` install Run:ai 3.0.20 on the lab's Kubernetes cluster and register it with itself as a cluster named `nvl8`. Projects, departments, quotas, fair share, preemption and workloads behave as they would on a real GPU cluster, scheduled by KAI onto the emulated GB200s from [Part 1](@/ai-lab/01-intro/index.md).

What I want to write about is not the installation, which is Helm charts and patience. It is what Run:ai checks for when it asks "is there an NVIDIA GPU stack under me?", because the answer turned out to be much smaller than I expected, and because answering it honestly surfaced two bugs in the lab that had nothing to do with Run:ai at all.

Then there are GPU fractions, which are the reason most people know the product. Run:ai 3.0 will only give you NVIDIA's driver-enforced kind, so getting them working meant running NVIDIA's real GPU fractioning, unmodified, against GPUs that do not exist. Two half-GPU workloads now share one emulated GB200, each held to half its memory, which is the last section.

## What gets installed

The charts come from NGC and the images from `nvcr.io/nvidia/runai-oss`, both public, so no NGC key is needed. Two halves land on the cluster:

- The control plane, in namespace `runai-backend` on the controller: the web UI, the REST API, Keycloak for users and SSO, PostgreSQL, NATS and Thanos.
- The cluster services, in namespace `runai`: the Run:ai operator, the agent and cluster sync, KAI Scheduler as `runai-scheduler`, KAI resource management for projects, node pools and queues, the workload controllers, and Run:ai's own Prometheus.

Run:ai's documented installation ends with an onboarding wizard in the UI, which is awkward to drive from Ansible. The role registers the cluster through the REST API instead and reads the installer's client secret back out, which is what Run:ai's own development setup does. The role then waits until the control plane reports the cluster `Connected`.

Alongside those go the prerequisites, as k3s `HelmChart` manifests like the lab's other add-ons: HAProxy as the ingress controller, kube-prometheus-stack, and NVIDIA's DCGM exporter.

## The GPU stack Run:ai actually checks for

I expected to have to emulate NVIDIA's GPU Operator, which is a large thing to fake. Reading what Run:ai actually requires, it comes down to this:

- GPUs advertised as `nvidia.com/gpu` and allocated by GPU UUID, which the lab's device plugin already does.
- Node labels like `nvidia.com/gpu.product`, `.count`, `.memory` and `.clique`, which NVIDIA's GPU Feature Discovery already provides in the lab. Run:ai's default node pool works out the NVLink domain from `nvidia.com/gpu.clique`.
- A Service labelled `app=nvidia-dcgm-exporter` serving `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED` and `DCGM_FI_DEV_FB_FREE`, with recording rules that join those metrics to the `kube_pod_info` of a pod whose name ends in `dcgm-exporter`.

That last one is the whole GPU-stack check. So rather than faking the exporter, I ran the real one: NVIDIA's DCGM exporter, unmodified, from the published image, against the lab's fake NVML. It works. It reports every GPU, and because it reads kubelet's pod-resources API it attributes each GPU to the pod using it, so Run:ai shows GPU utilisation and memory per workload exactly as it would on real hardware.

{% <admonition type="note" title="What it cannot report"> %}
There are no profiling metrics (`DCGM_FI_PROF_*`). DCGM's profiling module talks to the real driver, and nothing in the lab pretends otherwise. Utilisation and framebuffer figures come through NVML and are modelled; the profiling counters simply are not there.
{% </admonition> %}

## Two bugs it surfaced

Running somebody else's real binary against the emulation is a good test of the emulation, and it failed immediately.

### The fake libraries needed too new a glibc

The DCGM exporter reported `ERROR_LIBRARY_NOT_FOUND` and would not load the fake NVML at all. The cause was not in the exporter. The lab's fake GPU libraries are built on Ubuntu 24.04, and that build had quietly made them require glibc 2.38: under `_GNU_SOURCE`, glibc redirects `strtol`, `sscanf` and friends to their C23 variants as `__isoc23_*`, and it binds `dlopen`, `dlsym`, `dladdr` and `pthread_once` to their 2.34 versions.

Which meant the libraries could not load inside any image built on an older distribution. That is most CUDA and NVIDIA images, which are Ubuntu 22.04 with glibc 2.35. The emulation had a hard dependency on the build host's distribution and nobody had noticed, because everything that had used it so far ran on the trays themselves.

The fix is a header included before every source file, which turns the C23 redirection off using glibc's own switch, `__GLIBC_USE_C2X_STRTOL`, and binds those four symbols back to their original versions with `.symver`. Every glibc exports those. The newest symbol version the libraries now need is 2.33, from `stat`, and a test walks every built library to check it stays that way.

### The device plugin stopped being watched

The second one had been sitting there for a day and a half. After a tray reboot, both trays reported zero allocatable `nvidia.com/gpu` for thirty-one hours, and nobody noticed until a Run:ai workload would not schedule.

What happened is that kubelet accepted the device plugin's registration while kubelet was still starting, and then never opened the `ListAndWatch` stream through which the plugin reports its devices. The plugin re-registered only when kubelet's socket was recreated, and the socket was never recreated, so it waited forever holding a registration that kubelet had effectively forgotten.

It now counts how many `ListAndWatch` streams kubelet has open and registers again when none has been open for thirty seconds, which also covers a stream that ends without kubelet restarting. I checked it against a stand-in kubelet that accepts registrations and then never watches, and the plugin re-registers every thirty seconds as it should.

## The k3s details that cost an evening

Run:ai starts around thirty pods at once, which found three sharp edges in the lab's Kubernetes setup:

- **Volumes on the controller never bound.** local-path provisioner creates a volume with a helper pod that runs on the target node, and the controller carries a `CriticalAddonsOnly:NoExecute` taint, so the helper pod was evicted the moment it appeared and the claim failed with "helper pod not found". The helper pod now tolerates the taint.
- **"pull QPS exceeded".** kubelet limits image pulls to five per second, and a chart starting dozens of pods at once tripped it. This looks exactly like a registry rate limit and is not one: it is local. Setting `registry-qps=0` fixes it, and each image is still pulled once by containerd through the registry mirrors.
- **Pods stuck on "failed to reserve container name"** after a k3s restart that landed in the middle of creating a container.

There was also a provisioning gap worth fixing: `make provision` used to apply CPU and memory limits only when it created an instance, so changing them did nothing to a running lab. It now applies changed limits to running instances and restarts k3s there, because kubelet reads the node's capacity only when it starts.

## Sizing and access

Run:ai's charts are written for production system nodes and request about 15 CPUs in total, with 13 GiB for the control plane alone. The lab lowers the requests for every service, and the memory requests for the control plane's, while keeping the limits. The controller is raised to 6 CPUs and 16 GiB. In practice the running control plane and cluster services use about 5 GiB.

The UI and API are served by HAProxy on the controller's ports 80 and 443, at `https://runai.<controller address>.nip.io`. nip.io is a public DNS service that resolves a name back to the address embedded in it, so the host's browser and the lab's pods both reach the controller by name with no hosts file entry. A certificate authority of the lab's own signs the certificate, and the services are told to trust it. If the lab has no internet access, set a domain of your own instead.

## GPU fractions

The feature Run:ai is best known for is sharing one GPU between several workloads, and that is where this got interesting. A workload asking for half a GPU sat Pending, with the scheduler saying the node was not ready for fractional GPU scheduling.

My plan had been to use KAI's own simple GPU sharing, which hands a fractional pod its GPU through `NVIDIA_VISIBLE_DEVICES` and enforces nothing. That turns out to be unreachable: Run:ai 3.0's operator sets KAI's sharing mode to `NvFractions` and the source comment says it is deliberately not configurable. The only fractions Run:ai will give you are NVIDIA's driver-enforced ones, which are a separate project: an NRI plugin called `fractiond`, an MPS daemon, and a driver that holds each container to an `NVIDIA_GPU_MEMORY_LIMIT`.

So the question became whether NVIDIA's real GPU fractioning would run, unmodified, against GPUs that do not exist. It does.

### What the emulation had to supply

The lab had to stand in for the three things fractioning expects underneath it: the driver, the container toolkit and the GPU Operator.

- **A memory limit the driver enforces.** A container given part of a GPU receives `NVIDIA_GPU_MEMORY_LIMIT` in MiB, which on real hardware the driver enforces through MPS memory accounting. The fake driver now holds each process to it per GPU: `cuMemAlloc` and `cuMemCreate` fail with out of memory beyond it, and `cuMemGetInfo` reports the limit as the total, with the limit less what the process holds as free. Without the variable a process still gets the whole GPU.
- **An MPS control daemon.** Fractioning's `mpsd` supervises `nvidia-cuda-mps-control`, and its readiness probe is simply that the control socket exists. The lab ships a stand-in that listens on it, logs, takes commands from clients and from stdin, and stops on `quit`. It starts no MPS servers, because the fake driver runs every process's work itself.
- **Per-process utilisation.** Fractioning's `metricsd` reads `nvmlDeviceGetProcessUtilization` through go-nvml. That call was a generated no-op returning success, and go-nvml asks for the sample count with a NULL buffer expecting `NVML_ERROR_INSUFFICIENT_SIZE`, so it silently got no samples and nobody noticed. Busy time is kept per GPU and split evenly between its processes.
- **A driver version fractioning accepts**, which is r615, so the lab reports 615.20.01 with CUDA 13.4 when Run:ai is enabled.
- **NRI in the trays' containerd, and CDI specifications under NVIDIA's own names**, so that `fractiond` can deliver a fake GPU the way it would deliver a real one.
- **The GPU Operator's NRI plugin**, which the lab's device plugin now doubles as, handing fractioning's own daemons the `management.nvidia.com/gpu=all` device they ask for by annotation. Like NVIDIA's container toolkit it honours only management devices, and only in named namespaces.
- **A ClusterPolicy**, a minimal custom resource that nothing reconciles, describing the stack and its versions, plus the GPU Operator's node label `nvidia.com/gpu.deploy.client=true`, without which fractioning's DaemonSets select no nodes at all.

### Two things that made it harder than it needed to be

Run:ai 3.0.20 bundles GPU fractioning 0.1.6, which can only reach GPUs through NVIDIA's container runtime and fails with "no runtime for nvidia is configured". Version 0.1.7 adds the GPU Operator's NRI path, which is the lab's route in, so the lab pins those four images to 0.1.7 through chart values. The bundled chart differs from 0.1.7's by one optional CRD field, so nothing needs repackaging. Worth knowing if you go looking: 0.1.7 is published, but its changelog still says Unreleased.

There is also an ordering trap. KAI's operator only watches ClusterPolicy if that CRD existed when the operator started, so adding it to a running installation does nothing until the KAI operator is restarted. A fresh install never sees this.

### What two half-GPUs look like

Two workloads submitted through the Run:ai API with `gpuPortionRequest` of 0.5 were scheduled by KAI onto the same physical GPU, GPU 3 of the first tray. Each container got `NVIDIA_GPU_MEMORY_LIMIT=94735`, which is half of that GPU's 189471 MiB. Inside each, CUDA reported 94735 MiB as the total GPU memory. Allocating 90% of it succeeded; asking for a further 20% failed with `CUDA_ERROR_OUT_OF_MEMORY`. On the tray, `nvidia-smi` showed both processes on that one GPU, holding 85261 MiB each.

That is now part of `make test`, which submits the two workloads and checks all of it.

{% <admonition type="note" title="What a fraction is not, here"> %}
The limit is per process, which is what MPS enforces on real hardware too. Compute is not divided: each fraction runs at the whole GPU's modelled speed, so two halves finish as fast as one whole, which a real GPU would never do. Per-process utilisation is an even split of the GPU's busy time, exact for a whole GPU and an even share for fractions. Neither SM-level sharing nor MIG is modelled. The lab validates that fractional scheduling, admission and memory enforcement work end to end, not what sharing a GPU costs you.
{% </admonition> %}

## What to build on this

- **Run a real GPU-stack consumer against your own emulation.** The DCGM exporter found a glibc dependency that a year of lab-internal use had not, and NVIDIA's fractioning daemons found an NVML call that returned success while returning nothing. Both were invisible until somebody else's binary asked.
- **Watch for allocatable going to zero.** Thirty-one hours is a long time for two trays to advertise no GPUs. An alert on `nvidia.com/gpu` allocatable per node would have caught it the same minute.
- **Read what a platform actually requires before emulating it.** I was ready to fake the GPU Operator, and the real requirement was one Service, three metrics and a label convention.
- **Fractional scheduling is testable without fractional hardware.** Everything that decides whether a half-GPU workload is admitted, placed and held to its memory limit runs above the driver, which means it runs here.
