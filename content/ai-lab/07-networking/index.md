+++
title = "AI lab, part 7: Networking"
date = 2026-10-03
description = "The networks of a GPU cluster, and what the lab emulates for each: NVLink, InfiniBand, the front end and out-of-band management."

[extra]
# Series navigation and table of contents are placed in the body (below).
toc = false
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/07-networking/card.png"
+++

<!-- series_intro -->

<h3>Table of contents</h3>

<!-- toc -->

## Overview

[Part 5](@/ai-lab/05-nvlink/index.md) covered NVLink, the fabric *inside* an NVLink domain. A real GPU cluster has several more networks, and this is the part of the lab that is emulated most thinly. This post goes through which networks a GB200-class system has, what the lab gives you for each, and what it doesn't model at all, so you know where its answers stop being meaningful. Outputs were captured in Kubernetes mode; the network side is the same in Slurm mode, minus the pod network.

## The networks of a GPU cluster

GPU-to-GPU traffic uses two networks. The **scale-up** network joins GPUs into one larger domain, so they work like a single big machine: on GB200 that's NVLink. The **scale-out** network connects those domains to each other: InfiniBand or Ethernet. Together with the networks for everything else, a GB200-class cluster has four:

- **NVLink**, the scale-up network, carries GPU-to-GPU traffic inside one NVLink domain. In the lab: emulated, with links, partitions, health and traffic counters ([Part 5](@/ai-lab/05-nvlink/index.md)).
- **The compute fabric** (InfiniBand or RoCE Ethernet), the scale-out network, carries GPU-to-GPU traffic *between* NVLink domains. In the lab: the switch topology, and traffic counters for NCCL traffic between NVLink partitions.
- **Front-end Ethernet** carries logins, scheduler, storage and datasets, container images and everything else. Larger systems often give storage its own network, so reading datasets doesn't compete with the rest. In the lab: real, the Incus bridge.
- **Out-of-band management** connects the BMCs. In the lab: it shares the Incus bridge.

{% <admonition type="note" title="On a real rack"> %}
These are physically separate networks, each with its own cards and cables. The lab can't emulate that: everything that isn't NVLink runs over one Linux bridge.
{% </admonition> %}

## The front end: one bridge for everything

Every container has one interface on `incusbr0`. Here's a tray in Kubernetes mode:

```console
$ bin/ssh sched-worker1 ip -br addr
lo               UNKNOWN        127.0.0.1/8 ::1/128
flannel.1        UNKNOWN        10.42.3.0/32 fe80::187f:3dff:fe7d:ff1c/64
cni0             UP             10.42.3.1/24 fe80::e85f:deff:feb9:d143/64
veth18e97112@if2 UP             fe80::a85d:a8ff:feeb:a10d/64
vethef2a26b5@if2 UP             fe80::384c:14ff:fe51:c925/64
eth0@if101       UP             10.107.111.21/24 metric 100 fd42:7625:9911:a275:216:3eff:fea5:2b24/64 fe80::216:3eff:fea5:2b24/64
```

`eth0` carries everything: SSH, the scheduler's control traffic, LDAP, S3 and JuiceFS, Prometheus scrapes, and the BMCs' Redfish. In Kubernetes mode `flannel.1` adds the pod network (a 10.42.x.0/24 per node) as VXLAN over the same `eth0`. That's where the DDP job's rendezvous happens: `torchrun` in pod 1 connects to pod 0 through the JobSet's DNS name ([Part 3](@/ai-lab/03-kubernetes/index.md#running-jobs)).

The rendezvous is a handful of small messages. The training traffic itself, the gradients exchanged in every all-reduce, never touches the Ethernet side (`eth0` and the pod network on top of it). With the 8-GPU DDP job running ([Part 6](@/ai-lab/06-observability/index.md)), Prometheus showed:

```
# Ethernet traffic from each tray
rate(node_network_transmit_bytes_total{device="eth0", instance=~"sched-worker.*"}[1m])
5-10 KB/s per tray

# NVLink traffic from each tray
sum by (host) (rate(nvlink_gpu_tx_bytes_total[1m]))
~1.75 TB/s per tray
```

{% <admonition type="note" title="Real hardware would do the same"> %}
NCCL, the library that runs the all-reduce, picks the fastest path between each pair of GPUs. GPUs in the same NVLink partition reach each other over NVLink, so NCCL never uses the network for them. Here both trays are in one NVLink domain, and all eight GPUs are in its one partition, so a real cluster would also keep the whole all-reduce on NVLink. The compute fabric only gets GPU traffic when GPUs can't reach each other over NVLink: between NVLink domains, or between partitions of one domain.
{% </admonition> %}

## The compute fabric: InfiniBand

InfiniBand is the network most GPU clusters use between NVLink domains. It was built for HPC and offers very low latency and RDMA, which lets one machine write straight into another's memory without involving either CPU. Each host connects through an **HCA** (host channel adapter), InfiniBand's name for a network card.

The switches are usually wired in two layers, **leaf** and **spine**:

- **Leaf switches** connect to the hosts: each HCA plugs into a leaf
- **Spine switches** connect the leaves to each other: every leaf has links to every spine

Traffic between two hosts on the same leaf stays on that leaf. Traffic between hosts on different leaves goes leaf, spine, leaf, so any two hosts are at most three switches apart. That's why schedulers care which leaf a node is on: a job whose nodes share a leaf has the shortest paths, and doesn't compete with other jobs for the spine links.

The lab emulates two parts of InfiniBand: the switch topology that cluster software reads, and the traffic NCCL sends between NVLink partitions. Start with the topology. Each tray has an emulated `ibnetdiscover`, which prints the fabric described in `/etc/fakeib.json`:

```console
$ bin/ssh sched-worker1 sudo ibnetdiscover
…
Switch	65 "S-2c5eab0306555424"		# "MF0;LAB-IBSPINE-01:MQM9701/U1" enhanced port 0 lid 1 lmc 0
[1]	"S-2c5eab036832a42e"[9]		# "MF0;LAB-IBLEAF-01:MQM9701/U1" lid 2 4xNDR
…
Switch	65 "S-2c5eab036832a42e"		# "MF0;LAB-IBLEAF-01:MQM9701/U1" enhanced port 0 lid 2 lmc 0
[1]	"H-e09d7303b4f1abf5"[1](e09d7303b4f1abf5) 		# "sched-worker1 mlx5_0" lid 100 4xNDR
[2]	"H-e09d7303e640df18"[1](e09d7303e640df18) 		# "sched-worker1 mlx5_1" lid 101 4xNDR
…
[8]	"H-e09d730383c7a290"[1](e09d730383c7a290) 		# "sched-worker2 mlx5_3" lid 107 4xNDR
[9]	"S-2c5eab0306555424"[1]		# "MF0;LAB-IBSPINE-01:MQM9701/U1" lid 1 4xNDR
…
```

The fabric has a leaf switch and a spine switch above it. Each tray connects to the leaf with four HCAs, from `mlx5_0` to `mlx5_3`, so every GPU has its own 400 Gb/s link (4×NDR). The leaf connects to the spine over four uplinks.

`ibnetdiscover` prints all this in its real format, so real tools can read it. The tool that matters here is NVIDIA's [topograph](https://github.com/dsx-ai-factory/topograph). Every minute it runs `ibnetdiscover` on the trays, combines the switch tree with the NVLink cliques from NVML, and passes the result to the scheduler. In Kubernetes mode the result becomes node labels:

```console
$ bin/kubectl get nodes -l nvidia.com/gpu.present=true \
    -L accelerator.topograph.run/domain,fabric.topograph.run/tier-0,fabric.topograph.run/tier-1
NAME            STATUS   ROLES    AGE    VERSION        DOMAIN                                   TIER-0               TIER-1
sched-worker1   Ready    <none>   30m    v1.36.5+k3s1   7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1   S-2c5eab036832a42e   S-2c5eab0306555424
sched-worker2   Ready    <none>   30m    v1.36.5+k3s1   7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1   S-2c5eab036832a42e   S-2c5eab0306555424
```

Kueue uses these labels as levels, from the largest to the smallest: spine, leaf, NVLink domain, node. A job can ask to stay within any of them; the lab's examples ask for one NVLink domain. In Slurm mode, topograph writes the same information to `topology.conf` instead ([Part 5](@/ai-lab/05-nvlink/index.md#how-the-scheduler-finds-out)).

In the lab both trays sit under the same leaf and spine, so only the NVLink level can ever separate them. The emulator can't build a fabric with more leaves.

## What isn't there

What the lab doesn't model:

- **InfiniBand devices and tools.** The trays have no InfiniBand devices, so there's no `/sys/class/infiniband`, `ibstat` or `perfquery`. Only `ibnetdiscover` works, and it prints the emulated topology.
- **RDMA and GPUDirect.** Without devices there's no RDMA, GPUDirect RDMA or GPUDirect Storage, and NCCL's InfiniBand settings, such as `NCCL_IB_HCA`, have nothing to act on. The emulated NCCL only counts the bytes it would send between NVLink partitions.
- **Fabric management.** No subnet manager, UFM or `ibdiagnet`, and no link state or error counters. [Part 6](@/ai-lab/06-observability/index.md) can show InfiniBand traffic, but not faults. There are no partition keys either, so you can't test fabric isolation between tenants.
- **In-network computing.** No SHARP, so no reductions offloaded to the switches.
- **Ethernet compute fabrics.** No RoCE and no Spectrum-X.
- **BlueField DPUs.** No DPUs and no DOCA, so nothing is offloaded to a DPU and there's no DPU BMC. The front end is a plain Linux interface.
- **Separate networks.** The front end, storage, management and the pod network share one bridge, so you can't rehearse a management network outage or a congested storage network.
- **Scale.** The lab is a single NVLink domain under a single leaf and spine, while real clusters have hundreds of leaves.
- **Network faults.** You can't fail an InfiniBand link the way the BMCs fail an NVLink ([Part 4](@/ai-lab/04-bmc-redfish/index.md)). You can only imitate network faults on the Linux bridge, for example with `tc`.

## What you can still do with it

- **Topology-aware placement.** The `ibnetdiscover` output, topograph, Slurm's `topology.conf` and Kueue's labels are all real, so you can test how the scheduler places jobs. Change the NVLink partitions ([Part 5](@/ai-lab/05-nvlink/index.md)) and watch its view change.
- **Traffic between partitions.** Split the NVLink domain, run a job across it, and watch NCCL's traffic move to InfiniBand in Prometheus.
- **Parsers and inventory tools** for `ibnetdiscover` output, against a fabric that doesn't change under you.
- **The front-end network.** JuiceFS storage, the Kubernetes pod network and its DNS, and LDAP and S3 from jobs.
- **Wrong assumptions.** If your tools expect `/sys/class/infiniband` or `ibstat`, they fail right away in the lab. That's your reminder to test that part on real hardware.

## Next in the series

[Part 8: GPU handover](@/ai-lab/08-gpu-handover/index.md) covers what happens to a GPU between tenants: the reset that clears it and applies a new NVLink partition, and how Slurm runs it after every job.
