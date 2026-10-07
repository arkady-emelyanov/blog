+++
title = "AI lab, part 5: NVLink partitions, fabric health and telemetry"
date = 2026-10-02
description = "NVLink partitions, fabric health and telemetry, and how a partition change reaches the scheduler."

[extra]
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/05-nvlink/card.png"
+++

[Part 4](@/ai-lab/04-bmc-redfish/index.md) broke individual NVLinks through the BMCs. This part steps back to the whole fabric. I'll cover what an NVLink domain and its partitions are, how to query and change them through the lab's partition controller, how a partition change ends up in the scheduler, and what the fabric looks like in Prometheus. The examples run in Slurm mode; the Kubernetes counterpart is noted where it differs.

## The concepts

On a GB200 NVL72 rack each GPU has 18 NVLinks, one to each of the rack's 18 NVSwitch chips (9 switch trays × 2 chips). The lab models a single switch tray, so each GPU uses 9 links on each of its 2 chips. Together, GPUs and switches form one **NVLink domain**: any GPU can reach any other at NVLink speed, even across trays, without touching the network. That's why a 72-GPU rack can train like a single big machine.

A domain is usually shared, so the switch trays can cut it into **partitions**, isolated groups of GPUs that can only talk to each other. They work much like VLANs on an Ethernet switch. NVIDIA's NMX Controller (NMX-C) manages them. Every GPU reports its partition to software as a **clique ID** (`nvidia-smi --query-gpu=fabric.cliqueId`).

Schedulers care because a job whose ranks sit in different partitions can't use NVLink between them. The scheduler therefore needs to know the partition layout and keep jobs inside one partition. Partitions are configured on the switch side, not in the scheduler, so the layout has to be passed along. Most of this post is about how that happens.

In the lab, the domain is 8 GPUs, the switch tray has two NVSwitch chips with 72 ports each, and `sched-nvswitch` runs `fakenmxc`, an NMX-C-style controller. It speaks gRPC on port 9370 (with its own `.proto`, since NVIDIA's is proprietary) and serves fabric metrics on port 9372.

## Looking at the fabric

From the tray, NVML shows the fabric registration of every GPU:

```console
$ bin/ssh sched-worker1 nvidia-smi --query-gpu=index,fabric.clusterUuid,fabric.cliqueId --format=csv
index, fabric.cluster_uuid, fabric.clique_id
0, 7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91, 1
…
```

From the switch side, the repository's `bin/nvlink` helper asks the partition controller (more on the helper below):

```console
$ bin/nvlink domain
domain      nvl8 (7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91)
state       CONFIGURED
trays       2 compute, 1 switch
GPUs        8 x 18 NVLinks
partitions  default 32766, at most 32765
```

Out of the box, all 8 GPUs are in the **default partition**, ID 32766:

```console
$ bin/nvlink partitions
ID     NAME     GPUS  STATE   HEALTH   MEMBERS
32766  default  8     ACTIVE  healthy  sched-worker1:0-3 sched-worker2:0-3
```

Every GPU has all 18 links active, 9 to each of the two NVSwitch chips:

```console
$ bin/nvlink topology
TRAY           GPU  ACTIVE  NVSWITCH_0  NVSWITCH_1
sched-worker1  0    18/18   9           9
sched-worker1  1    18/18   9           9
sched-worker1  2    18/18   9           9
sched-worker1  3    18/18   9           9
sched-worker2  0    18/18   9           9
sched-worker2  1    18/18   9           9
sched-worker2  2    18/18   9           9
sched-worker2  3    18/18   9           9
```

`bin/nvlink` is a thin client over the controller's gRPC API: `--json` prints the raw responses, and `bin/grpcurl -plaintext 10.107.111.34:9370 describe nmxlab.v1.NMXController` lists every RPC.

## Health: a degraded GPU

Remember the two NVLinks disabled through the tray BMC in Part 4? With them down, the lab's controller rates that GPU as degraded:

```console
$ bin/nvlink gpus sched-worker1
TRAY           GPU  UUID                                      PARTITION  CLIQUE  NVLINKS  HEALTH
sched-worker1  0    GPU-81501924-f170-f6d0-f2da-dc7595876881  32766      1       18       healthy
sched-worker1  1    GPU-8150a4ae-478e-5534-f66c-621a95f85f59  32766      1       18       healthy
sched-worker1  2    GPU-81502f39-9cad-de97-2723-3d7d71928458  32766      1       16       degraded
sched-worker1  3    GPU-8151bac3-f1cc-3cd0-015f-475439ee933a  32766      1       18       healthy
```

The same appears in metrics as `nvlink_gpu_active_links{host="sched-worker1",gpu="2"} 16` and `nvlink_gpu_healthy{…} 0`. `min(nvlink_gpu_healthy) == 0` makes a natural first alert.

`degraded` (`NMX_GPU_HEALTH_DEGRADED_BANDWIDTH` in the API) is the lab's rating for a GPU with some links down. NVIDIA's [GB200 NVL Partition User's Guide](https://docs.nvidia.com/multi-node-nvlink-systems/partition-guide-v1-2.pdf) (§6.2) documents an access-link failure as marking the GPU `NO_NVLINK`, with the partition's workload running into errors, and the real health enum also has a `DEGRADED_BW` value.

## Operations: split the domain

A typical reason to split a domain is to give a tenant its own GPUs. Give tray 2 its own partition. A GPU belongs to at most one partition, so creating the partition straight away fails:

```console
$ bin/nvlink create tray2 --id 7 sched-worker2
nvlink: CreatePartition: NMX_ST_GPU_IN_USE: GPU sched-worker2:0 is in partition 32766; remove it there first
```

Take the tray out of the default partition first:

```console
$ bin/nvlink remove default sched-worker2
GPUs removed: 32766 default

$ bin/nvlink partitions
ID     NAME     GPUS  STATE   HEALTH   MEMBERS
32766  default  4     ACTIVE  healthy  sched-worker1:0-3

in no partition: sched-worker2:0-3

$ bin/ssh sched-worker2 nvidia-smi --query-gpu=index,fabric.cliqueId --format=csv
index, fabric.clique_id
0, 0
1, 0
2, 0
3, 0
```

Clique `0` means the GPU is in no partition. On real hardware that GPU now has no NVLink peers at all. Create the new partition:

```console
$ bin/nvlink create tray2 --id 7 sched-worker2
partition created: 7 tray2

$ bin/nvlink partitions
ID     NAME     GPUS  STATE   HEALTH   MEMBERS
7      tray2    4     ACTIVE  healthy  sched-worker2:0-3
32766  default  4     ACTIVE  healthy  sched-worker1:0-3

$ bin/ssh sched-worker2 nvidia-smi --query-gpu=index,fabric.cliqueId --format=csv
index, fabric.clique_id
0, 7
1, 7
2, 7
3, 7
```

### How the scheduler finds out

Nobody tells Slurm about this directly. Every minute, topograph on the controller collects `ibnetdiscover` and the NVML clique from every tray, and regenerates `topology.conf` when the result changes. I polled `scontrol show topology` every 10 seconds after creating the partition, and the change landed about 60 seconds later:

```
12:38:21  partition 7 created
12:38:31  BlockName=block001 BlockIndex=0 Nodes=sched-worker[1-2]
…
12:39:23  BlockName=block001 BlockIndex=0 Nodes=sched-worker1
          BlockName=block002 BlockIndex=1 Nodes=sched-worker2
```

```console
$ bin/ssh sched-control cat /etc/slurm/topology.conf
# block001=7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1
BlockName=block001 Nodes=sched-worker1
# block002=7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.7
BlockName=block002 Nodes=sched-worker2
```

Each block is named after the cluster UUID and clique it was built from. In Kubernetes mode the same pipeline relabels the nodes instead (`accelerator.topograph.run/domain=<uuid>.<clique>`, plus GPU Feature Discovery's `nvidia.com/gpu.clique`). [Part 3](@/ai-lab/03-kubernetes/index.md) shows those labels.

{% <admonition type="note" title="Partitions smaller than a tray"> %}
`bin/nvlink` also takes single GPUs (`sched-worker1:2`, `sched-worker1:2-3`), so a partition can hold part of a tray, down to one GPU. NVML and the tray BMC follow that per GPU, but the schedulers can't use it. topograph keeps one NVLink domain per node ([`HostInfo`](https://github.com/dsx-ai-factory/topograph/blob/03cde87/pkg/topology/domain.go)), because a Slurm block and Kubernetes labels are per node. When a node's GPUs report two cliques, its run fails with "ambiguous NVL partition IDs" ([`ParseNvidiaSMIOutput`](https://github.com/dsx-ai-factory/topograph/blob/03cde87/pkg/accelerator/nvidia_smi.go)), and the last good topology stays in place without any other warning. Keep partitions to whole trays.
{% </admonition> %}

### The surprise: an 8-GPU job still runs

You might expect `nvl8-hello` (2 nodes × 4 GPUs) to wait now. It doesn't:

```console
$ sbatch --wait nvl8-hello.sbatch && cat nvl8-hello-21.out
job 21 on sched-worker[1-2]: 8 tasks
0: rank 0 on sched-worker1 CUDA_VISIBLE_DEVICES=0: NVIDIA GB200, GPU-81501924-…, 1, scratch  198G
…
4: rank 4 on sched-worker2 CUDA_VISIBLE_DEVICES=0: NVIDIA GB200, GPU-81477906-…, 7, scratch  198G
…
```

Ranks 0–3 are in clique 1 and ranks 4–7 in clique 7. Slurm's `topology/block` *prefers* to keep a job inside one block, but a job larger than any block is allowed to span several. Newer Slurm versions can control this (`--segment`, `BlockSizes`), but the lab runs Slurm 23.11, which can't.

On real hardware, the two halves of this job would talk over the network instead of NVLink, and the job would run slower. The lab doesn't show that: its emulated NCCL ignores cliques, so the job runs at the same speed.

Kubernetes behaves differently. The lab's JobSets require all pods to be in one NVLink domain (`kueue.x-k8s.io/podset-required-topology`), so Kueue keeps the same job waiting instead of splitting it. Slurm runs the job split, Kueue doesn't run it at all. Which is better depends on the job.

Merge back by deleting the partition, which leaves its GPUs in no partition, and adding the tray to the default partition again. The default block returns within a minute:

```console
$ bin/nvlink delete tray2
partition deleted; its GPUs are in no partition

$ bin/nvlink add default sched-worker2
GPUs added: 32766 default

$ bin/nvlink partitions
ID     NAME     GPUS  STATE   HEALTH   MEMBERS
32766  default  8     ACTIVE  healthy  sched-worker1:0-3 sched-worker2:0-3
```

## Telemetry

The controller also reads per-GPU NVLink byte counters that the emulated CUDA stack keeps for NCCL collectives and GPU-to-GPU copies, and exposes them alongside the link and partition state:

```console
$ curl -s http://10.107.111.34:9372/metrics | grep -E '^(nvlink_domain_info|nvlink_partition_gpus|nvswitch_ports_up)'
nvlink_domain_info{cluster_uuid="7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91",domain="nvl8"} 1
nvlink_partition_gpus{default="true",name="default",partition_id="32766"} 8
nvswitch_ports_up{host="sched-nvswitch",switch="NVSwitch_0"} 72
nvswitch_ports_up{host="sched-nvswitch",switch="NVSwitch_1"} 72
```

With the 8-GPU DDP job from [Part 2](@/ai-lab/02-slurm/index.md) running (`sbatch ddp-train.sbatch --steps 60000`), Prometheus shows the all-reduce traffic:

- NVLink traffic from each tray, about 1.68 TB/s:\
  `sum by (host) (rate(nvlink_gpu_tx_bytes_total[1m]))`
- Traffic per NVSwitch chip, also about 1.68 TB/s, because each GPU spreads its links over both chips:\
  `rate(nvswitch_tx_bytes_total[1m])`
- Switch ports down, `0`:\
  `sum(nvswitch_ports) - sum(nvswitch_ports_up)`
- All GPUs healthy, `1`:\
  `min(nvlink_gpu_healthy)`

The Grafana dashboard **Scheduler & NVLink fabric** puts these next to the scheduler panels: unhealthy GPUs, switch ports down, GPUs per partition, and NVLink and NVSwitch throughput. Disable a switch port as in Part 4 while DDP runs, and the *switch ports down* panel and the GPU's active-link count change within one scrape.

## Ideas to build on this

- **Partition-aware admission.** Reject or re-route jobs that would span cliques. That covers the Slurm 23.11 gap above, and you can test it on the lab.
- **Tenant isolation workflows.** Create a partition per tenant, assign GPUs, verify with NVML, and tear it down, all as code against the gRPC API.
- **Fabric alerts.** Degraded GPUs, ports down and partition drift, wired to the same Prometheus as the scheduler metrics.

## Next in the series

[Part 6: Observability](@/ai-lab/06-observability/index.md) puts all of this on dashboards: which layers of a GPU cluster to monitor, and how to read the graphs.
