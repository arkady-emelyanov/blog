+++
title = "AI lab, part 5: NVLink partitions"
date = 2026-10-02
description = "NVLink partitions, fabric health and telemetry, and how a partition change reaches the scheduler."

[extra]
# Series navigation and table of contents are placed in the body (below).
toc = false
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/05-nvlink/card.png"
+++

<!-- series_intro -->

<h3>Table of contents</h3>

<!-- toc -->

## Overview

[Part 4](@/ai-lab/04-bmc-redfish/index.md) broke individual NVLinks through the BMCs. This part steps back to the whole fabric. I'll explain what an NVLink domain and its partitions are, then show how to query and change them through the lab's partition controller, how a partition change reaches the scheduler, and what the fabric looks like in Prometheus. The examples run in Slurm mode; the Kubernetes counterpart is noted where it differs.

## The concepts

On a GB200 NVL72 rack each GPU has 18 NVLinks, one to each of the rack's 18 NVSwitch chips (9 switch trays × 2 chips). The lab models a single switch tray, so each GPU uses 9 links on each of its 2 chips. Together, GPUs and switches form one **NVLink domain**: any GPU can reach any other at NVLink speed, even across trays, without touching the network. That's why a 72-GPU rack can train like a single big machine.

An NVLink domain is usually shared by several users or teams, so the switch trays can cut it into **partitions**, isolated groups of GPUs that can only talk to each other. They work much like VLANs on an Ethernet switch. NVIDIA's NMX Controller (NMX-C) manages them. Every GPU reports its partition to software as a **clique ID** (`nvidia-smi --query-gpu=fabric.cliqueId`).

Schedulers care because a job whose ranks sit in different partitions can't use NVLink between them. The scheduler therefore needs to know the partition layout and keep jobs inside one partition. Partitions are configured on the switch side, not in the scheduler, so the layout has to be passed along. Most of this post is about how that happens.

{% <admonition type="note" title="Partitions smaller than a tray"> %}
A partition can hold part of a tray, down to a single GPU. NVML and the tray BMC report that per GPU, but the schedulers can't use it: a Slurm block and Kubernetes labels are per node. topograph, the tool that passes partitions to the scheduler (more on it below), keeps one NVLink domain per node ([`HostInfo`](https://github.com/dsx-ai-factory/topograph/blob/03cde87/pkg/topology/domain.go)). When a node's GPUs report two cliques, its run fails with "ambiguous NVL partition IDs" ([`ParseNvidiaSMIOutput`](https://github.com/dsx-ai-factory/topograph/blob/03cde87/pkg/accelerator/nvidia_smi.go)), and the last good topology stays in place without any other warning. Keep partitions to whole trays.
{% </admonition> %}

In the lab, the domain is 8 GPUs, and the switch tray has two NVSwitch chips with 72 ports each. `sched-nvswitch` runs `fakenmxc`, an NMX-C-style controller. It speaks gRPC on port 9370 and serves fabric metrics on port 9372.

{% <admonition type="note" title="The controller's API"> %}
NVIDIA's `.proto` for NMX-C is proprietary, so `fakenmxc` has its own, and tools written against it need a different client for a real NMX-C. The behaviour follows NVIDIA's [GB200 NVL Partition User's Guide](https://docs.nvidia.com/multi-node-nvlink-systems/partition-guide-v1-2.pdf): the same partition operations (create, delete, add and remove GPUs), the default partition 32766, and a GPU reset after a partition change. The status codes for errors, such as `NMX_ST_GPU_IN_USE`, are the lab's own: the guide only names success and two control-plane errors.
{% </admonition> %}

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

## Health: a GPU with links down

Disable two of GPU 2's links on `sched-worker1` from the switch side, through the switch tray's BMC as in [Part 4](@/ai-lab/04-bmc-redfish/index.md). Ports 19 and 20 on `NVSwitch_0` are that GPU's links 2 and 4. With them down, the controller marks the GPU `no-nvlink`:

```console
$ for p in 19 20; do
    bin/redfish sched-nvswitch /redfish/v1/Fabrics/MGX_NVLinkFabric_0/Switches/NVSwitch_0/Ports/NVLink_$p \
      -X PATCH -d '{"LinkState": "Disabled"}'
  done

$ bin/nvlink gpus sched-worker1
TRAY           GPU  UUID                                      PARTITION  CLIQUE  RESET  NVLINKS  HEALTH
sched-worker1  0    GPU-81501924-f170-f6d0-f2da-dc7595876881  32766      1       -      18       healthy
sched-worker1  1    GPU-8150a4ae-478e-5534-f66c-621a95f85f59  32766      1       -      18       healthy
sched-worker1  2    GPU-81502f39-9cad-de97-2723-3d7d71928458  32766      1       -      16       no-nvlink
sched-worker1  3    GPU-8151bac3-f1cc-3cd0-015f-475439ee933a  32766      1       -      18       healthy
```

The same appears in the controller's metrics:

```console
$ curl -s http://10.107.111.34:9372/metrics | grep -E '^nvlink_gpu_(active_links|healthy)\{.*host="sched-worker1"' | grep 'gpu="2"'
nvlink_gpu_active_links{gpu="2",host="sched-worker1",slot="1",uuid="GPU-81502f39-9cad-de97-2723-3d7d71928458"} 16
nvlink_gpu_healthy{gpu="2",host="sched-worker1",slot="1",uuid="GPU-81502f39-9cad-de97-2723-3d7d71928458"} 0
```

`no-nvlink` is `NMX_GPU_HEALTH_NO_NVLINK` in the API. It follows NVIDIA's partition guide (§6.2, Access Link): when an access link between a GPU and an NVSwitch fails, the controller marks the GPU `NO_NVLINK`, and on real hardware the workload in its partition runs into errors. The partition itself stays healthy, as the guide describes:

```console
$ bin/nvlink partitions
ID     NAME     GPUS  STATE   HEALTH   MEMBERS
32766  default  8     ACTIVE  healthy  sched-worker1:0-3 sched-worker2:0-3
```

`min(nvlink_gpu_healthy) == 0` makes a natural first alert. Set both ports back to `"Enabled"` to restore the GPU.

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
sched-worker2: GPUs 0,1,2,3 keep their old clique until reset: bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0,1,2,3  (GPUs must be idle; with Slurm the epilog resets a job's GPUs when it ends)

$ bin/nvlink partitions
ID     NAME     GPUS  STATE   HEALTH   MEMBERS
32766  default  4     ACTIVE  healthy  sched-worker1:0-3

in no partition: sched-worker2:0-3
```

`bin/nvlink` warns that the GPUs keep their old clique until they're reset. As on GB200, a GPU takes a new partition only at a GPU reset or a reboot; [Part 8](@/ai-lab/08-gpu-handover/index.md) explains why. So NVML still reports clique 1:

```console
$ bin/ssh sched-worker2 nvidia-smi --query-gpu=index,fabric.cliqueId --format=csv
index, fabric.clique_id
0, 1
1, 1
2, 1
3, 1
```

Create the new partition. The GPUs belong to it now, but their reset is still pending:

```console
$ bin/nvlink create tray2 --id 7 sched-worker2
partition created: 7 tray2
sched-worker2: GPUs 0,1,2,3 keep their old clique until reset: bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0,1,2,3  (GPUs must be idle; with Slurm the epilog resets a job's GPUs when it ends)

$ bin/nvlink gpus sched-worker2
TRAY           GPU  UUID                                      PARTITION  CLIQUE  RESET    NVLINKS  HEALTH
sched-worker2  0    GPU-81477906-d580-de42-e868-ff62e9ef4317  7          1       pending  18       healthy
sched-worker2  1    GPU-8147ef90-7f0c-fc1e-c070-d80c4a3d939f  7          1       pending  18       healthy
sched-worker2  2    GPU-81488ef0-5668-4b3a-b971-f8fb582a0fb2  7          1       pending  18       healthy
sched-worker2  3    GPU-814804a5-291e-ba91-a4e9-01fb69ea739b  7          1       pending  18       healthy
```

Reset the tray's GPUs. They have to be idle, and the reset needs root:

```console
$ bin/ssh sched-worker2 nvidia-smi --gpu-reset
GPU 00000000:18:00.0 was successfully reset.
GPU 00000000:2A:00.0 was successfully reset.
GPU 00000000:3A:00.0 was successfully reset.
GPU 00000000:5D:00.0 was successfully reset.
All done.

$ bin/ssh sched-worker2 nvidia-smi --query-gpu=index,fabric.cliqueId --format=csv
index, fabric.clique_id
0, 7
1, 7
2, 7
3, 7
```

### How the scheduler finds out

Nobody tells Slurm about this directly. Every minute, topograph on the controller collects `ibnetdiscover` and the NVML clique from every tray, and regenerates `topology.conf` when the result changes. It reads the clique from NVML, so it only sees the change after the reset. I polled `scontrol show topology` every 10 seconds after the reset, and the change landed within the minute:

```
22:35:28  GPUs reset
22:35:28  BlockName=block001 BlockIndex=0 Nodes=sched-worker[1-2]
22:35:39  BlockName=block001 BlockIndex=0 Nodes=sched-worker[1-2]
22:35:49  BlockName=block001 BlockIndex=0 Nodes=sched-worker1
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

### The surprise: an 8-GPU job still runs

You might expect `nvl8-hello` (2 nodes × 4 GPUs) to wait now. It doesn't:

```console
$ sbatch --wait nvl8-hello.sbatch && cat nvl8-hello-7.out
Submitted batch job 7
job 7 on sched-worker[1-2]: 8 tasks
0: rank 0 on sched-worker1 CUDA_VISIBLE_DEVICES=0: NVIDIA GB200, GPU-81501924-f170-f6d0-f2da-dc7595876881, 1, scratch  198G
…
4: rank 4 on sched-worker2 CUDA_VISIBLE_DEVICES=0: NVIDIA GB200, GPU-81477906-d580-de42-e868-ff62e9ef4317, 7, scratch  198G
…
```

Ranks 0-3 are in clique 1 and ranks 4-7 in clique 7. Slurm's `topology/block` *prefers* to keep a job inside one block, but a job larger than any block is allowed to span several. Newer Slurm versions can control this (`--segment`, `BlockSizes`), but the lab runs Slurm 23.11, which can't.

The job runs, but it's slower. The two halves can't reach each other over NVLink, so NCCL sends the traffic between them over InfiniBand, which is much slower: each GPU has a 400 Gb/s (50 GB/s) InfiniBand link, against hundreds of GB/s over NVLink. The lab's emulated NCCL does the same: inside each partition it uses NVLink, and between partitions it uses InfiniBand. Here is the same DDP job, 16,000 steps, first on one partition, then split across two:

```console
$ grep -E "^step +(10|50|16000) |rank 0/" ddp-train-6.out
step   10      4.3 ms    119665 samples/s      48 TFLOP/s/GPU
step   50      4.1 ms    125795 samples/s      51 TFLOP/s/GPU
step 16000      4.4 ms    115490 samples/s      47 TFLOP/s/GPU
rank 0/8 on sched-worker1 cuda:0 (NVIDIA GB200) peak mem 7.1 GiB

$ grep -E "^step +(10|50|16000) |rank 0/" ddp-train-8.out
step   10      9.8 ms     52224 samples/s      21 TFLOP/s/GPU
step   50      9.3 ms     55250 samples/s      22 TFLOP/s/GPU
step 16000      9.8 ms     52000 samples/s      21 TFLOP/s/GPU
rank 0/8 on sched-worker1 cuda:0 (NVIDIA GB200) peak mem 7.1 GiB
```

Steps take more than twice as long, and the whole job took 2:39 instead of 1:17. During the split run, Prometheus showed the traffic moving to InfiniBand:

```
# InfiniBand traffic from each tray
sum by (instance) (rate(node_infiniband_port_data_transmitted_bytes_total[1m]))
~120 GB/s per tray (0 on one partition)

# NVLink traffic for the whole domain
sum(rate(nvlink_gpu_tx_bytes_total[1m]))
~1.36 TB/s (3.52 TB/s on one partition)
```

The "NVLink vs InfiniBand" panel of the Scheduler & NVLink fabric dashboard shows the same shift. I ran the job again with 30,000 steps, first on one partition, then split:

{{< figure src="panel-nvlink-vs-ib.png" alt="Grafana panel: NVLink traffic at about 3.5 TB/s and no InfiniBand traffic during the first run; during the second run NVLink falls to about 1.4 TB/s and InfiniBand rises to about 240 GB/s" caption="NVLink vs InfiniBand panel: the same DDP job on one partition, then split across two" />}}

During the first run, all the traffic goes over NVLink. During the split run, NVLink traffic drops to well under half, and InfiniBand carries the traffic between the trays: about 240 GB/s for the domain, 120 GB/s from each tray. The split run also lasts more than twice as long.

The scheduler sees none of this: to Slurm both jobs are equally healthy. Only the step time and the InfiniBand counters show that the job is split.

Kubernetes behaves differently. The lab's JobSets require all pods to be in one NVLink domain (`kueue.x-k8s.io/podset-required-topology`), so Kueue keeps the same job waiting instead of splitting it. Slurm runs the job split and slower, Kueue doesn't run it at all. Which is better depends on the job.

Merge back by deleting the partition, adding the tray to the default partition again, and resetting its GPUs. The default block returns within a minute:

```console
$ bin/nvlink delete tray2
partition deleted; its GPUs are in no partition
sched-worker2: GPUs 0,1,2,3 keep their old clique until reset: bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0,1,2,3  (GPUs must be idle; with Slurm the epilog resets a job's GPUs when it ends)

$ bin/nvlink add default sched-worker2
GPUs added: 32766 default
sched-worker2: GPUs 0,1,2,3 keep their old clique until reset: bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0,1,2,3  (GPUs must be idle; with Slurm the epilog resets a job's GPUs when it ends)

$ bin/ssh sched-worker2 nvidia-smi --gpu-reset
GPU 00000000:18:00.0 was successfully reset.
GPU 00000000:2A:00.0 was successfully reset.
GPU 00000000:3A:00.0 was successfully reset.
GPU 00000000:5D:00.0 was successfully reset.
All done.

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

With the 8-GPU DDP job from [Part 2](@/ai-lab/02-slurm/index.md) running on one partition (`sbatch ddp-train.sbatch --steps 60000`), Prometheus shows the all-reduce traffic:

```
# NVLink traffic from each tray
sum by (host) (rate(nvlink_gpu_tx_bytes_total[1m]))
~1.76 TB/s per tray

# traffic per NVSwitch chip (each GPU spreads its links over both)
rate(nvswitch_tx_bytes_total[1m])
~1.76 TB/s per chip

# switch ports down
sum(nvswitch_ports) - sum(nvswitch_ports_up)
0

# all GPUs healthy
min(nvlink_gpu_healthy)
1
```

The Grafana dashboard **Scheduler & NVLink fabric** puts these next to the scheduler panels: unhealthy GPUs, switch ports down, GPUs per partition, NVLink and NVSwitch throughput, and an InfiniBand row for traffic between partitions. Disable a switch port as in Part 4 while DDP runs, and the *switch ports down* panel and the GPU's active-link count change within one scrape.

## Ideas to build on this

- **Partition-aware admission.** Reject or re-route jobs that would span cliques. That covers the Slurm 23.11 gap above, and the lab shows what it costs: twice the step time.
- **Tenant isolation workflows.** Create a partition per tenant, assign GPUs, verify with NVML, and tear it down, all as code against the gRPC API.
- **Fabric alerts.** GPUs with links down, switch ports down, and InfiniBand traffic from a job that should stay on NVLink, wired to the same Prometheus as the scheduler metrics.

## Next in the series

[Part 6: Observability](@/ai-lab/06-observability/index.md) puts all of this on dashboards: which layers of a GPU cluster to monitor, and how to read the graphs.
