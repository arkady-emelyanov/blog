---
description: "NVLink partitions, fabric health and telemetry, and how a partition change reaches the scheduler."
---

# AI lab, part 5: NVLink partitions, fabric health and telemetry

*Series: [Intro](01-intro.md) · [Slurm](02-slurm.md) · [Kubernetes](03-kubernetes.md) · [BMC and Redfish](04-bmc-redfish.md) · **NVLink** · [Observability](06-observability.md) · [Networking](07-networking.md)*

> Independent personal project, not affiliated with or endorsed by NVIDIA. It emulates NVIDIA hardware interfaces in software for research and education.

[Part 4](04-bmc-redfish.md) broke individual NVLinks through the BMCs. This part steps back to the whole fabric. We'll cover what an NVLink domain and its partitions are, how to query and change them through the lab's partition controller, how a partition change ends up in the scheduler, and what the fabric looks like in Prometheus. The examples run in Slurm mode; the Kubernetes counterpart is noted where it differs.

## The concepts

On a GB200 NVL72 rack each GPU has 18 NVLinks, one to each of the rack's 18 NVSwitch chips (9 switch trays × 2 chips). The lab models a single switch tray, so each GPU uses 9 links on each of its 2 chips. Together, GPUs and switches form one **NVLink domain**: any GPU can reach any other at NVLink speed, even across trays, without touching the network. That's why a 72-GPU rack can train like a single big machine.

A domain is usually shared, so the switch trays can cut it into **partitions**, isolated groups of GPUs that can only talk to each other. NVIDIA's NMX Controller (NMX-C) manages them. Every GPU reports its partition to software as a **clique ID** (`nvidia-smi --query-gpu=fabric.cliqueId`).

Schedulers care because a job whose ranks sit in different partitions can't use NVLink between them. The scheduler therefore needs to know the partition layout and keep jobs inside one partition.

In the lab, the domain is 8 GPUs, the switch tray has two NVSwitch chips with 72 ports each, and `sched-nvswitch` runs `fakenmxc`, an NMX-C-style controller. It speaks gRPC on port 9370 (with its own `.proto`, since NVIDIA's is proprietary) and serves fabric metrics on port 9372.

## Observation

From the tray, NVML shows the fabric registration of every GPU:

```
$ bin/ssh sched-worker1 nvidia-smi --query-gpu=index,fabric.clusterUuid,fabric.cliqueId --format=csv
index, fabric.cluster_uuid, fabric.clique_id
0, 7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91, 1
…
```

From the switch side, use `bin/grpcurl` (built on first use). Every client starts with `Hello`:

```
$ nmx() { bin/grpcurl -plaintext -d "$2" 10.107.111.34:9370 nmxlab.v1.NMXController/$1; }
$ nmx Hello '{"gateway_id": "me"}'
{ "majorVersion": 1, "domainUuid": "7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91" }

$ nmx GetDomainProperties '{"gateway_id": "me"}'
{
  "domainUuid": "7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91",
  "domainName": "nvl8",
  "computeNodeCount": 2,
  "switchNodeCount": 1,
  "gpuCount": 8,
  "nvlinksPerGpu": 18,
  "defaultPartitionId": 32766,
  "maxPartitions": 32765
}
```

Out of the box, all 8 GPUs are in the **default partition**, ID 32766:

```
$ nmx GetPartitionInfoList '{"gateway_id": "me"}' | jq -c '.partitions[] | {partitionId, partitionName, gpus: (.gpuUids|length), state}'
{"partitionId":32766,"partitionName":"default","gpus":8,"state":"ACTIVE"}
```

`GetTopologyInfo` lists all 144 GPU-to-switch connections, and `bin/grpcurl -plaintext 10.107.111.34:9370 describe nmxlab.v1.NMXController` lists every RPC.

## Health: a degraded GPU

Remember the two NVLinks disabled through the tray BMC in Part 4? While they were down, the lab's controller rated that GPU as degraded (output trimmed to GPU 2):

```
$ nmx GetGpuInfoList '{"gateway_id": "me", "slot_ids": [1]}'
{ "uuid": "GPU-81502f39-…", "hostname": "sched-worker1", "location": {"slotId": 1, "gpuId": 2},
  "partitionId": 32766, "cliqueId": 1, "activeNvlinks": 16, "health": "NMX_GPU_HEALTH_DEGRADED_BANDWIDTH" }
```

The same appears in metrics as `nvlink_gpu_active_links{host="sched-worker1",gpu="2"} 16` and `nvlink_gpu_healthy{…} 0`. `min(nvlink_gpu_healthy) == 0` makes a natural first alert.

`DEGRADED_BANDWIDTH` is the lab's rating for a GPU with some links down. NVIDIA's [GB200 NVL Partition User's Guide](https://docs.nvidia.com/multi-node-nvlink-systems/partition-guide-v1-2.pdf) (§6.2) documents an access-link failure as marking the GPU `NO_NVLINK`, with the partition's workload running into errors, and the real health enum also has a `DEGRADED_BW` value.

## Operations: split the domain

Give tray 2 its own partition. A GPU belongs to at most one partition, so you take it out of the default partition first:

```
$ gpus='[{"slot_id":2,"gpu_id":0},{"slot_id":2,"gpu_id":1},{"slot_id":2,"gpu_id":2},{"slot_id":2,"gpu_id":3}]'
$ nmx RemoveGpusFromPartition "{\"gateway_id\": \"me\", \"partition_id\": 32766, \"locations\": $gpus}"
$ bin/ssh sched-worker2 nvidia-smi --query-gpu=index,fabric.cliqueId --format=csv
index, fabric.clique_id
0, 0
…
```

Clique `0` means the GPU is in no partition. On real hardware that GPU now has no NVLink peers at all. Create the new partition:

```
$ nmx CreatePartition "{\"gateway_id\": \"me\", \"partition_name\": \"tray2\", \"partition_id\": 7, \"locations\": $gpus}"
{ "message": "partition created", "partition": { "partitionId": 7, "partitionName": "tray2", … "state": "ACTIVE" } }
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

```
$ bin/ssh sched-control cat /etc/slurm/topology.conf
# block001=7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1
BlockName=block001 Nodes=sched-worker1
# block002=7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.7
BlockName=block002 Nodes=sched-worker2
```

Each block is named after the cluster UUID and clique it was built from. In Kubernetes mode the same pipeline relabels the nodes instead (`accelerator.topograph.run/domain=<uuid>.<clique>`, plus GPU Feature Discovery's `nvidia.com/gpu.clique`). [Part 3](03-kubernetes.md) shows those labels.

### The surprise: an 8-GPU job still runs

You might expect `nvl8-hello` (2 nodes × 4 GPUs) to wait now. It doesn't:

```
$ sbatch --wait nvl8-hello.sbatch && cat nvl8-hello-21.out
job 21 on sched-worker[1-2]: 8 tasks
0: rank 0 on sched-worker1 CUDA_VISIBLE_DEVICES=0: NVIDIA GB200, GPU-81501924-…, 1, scratch  198G
…
4: rank 4 on sched-worker2 CUDA_VISIBLE_DEVICES=0: NVIDIA GB200, GPU-81477906-…, 7, scratch  198G
…
```

Ranks 0–3 are in clique 1 and ranks 4–7 in clique 7. Slurm's `topology/block` *prefers* to keep a job inside one block, but a job larger than any block is allowed to span several. Newer Slurm releases add `--segment` and `BlockSizes` to control that; the lab runs Ubuntu's Slurm 23.11, which has neither. On real hardware, this job's NCCL traffic between the halves would fall back to the network. The lab doesn't model that: the fake NCCL ranks don't communicate and ignore cliques, so the job's timing is unchanged.

Kubernetes behaves differently. The lab's JobSets set `kueue.x-k8s.io/podset-required-topology` on the NVLink domain label, so Kueue keeps the same job queued rather than splitting it. The same partition change gives you two different scheduler behaviours, which makes the lab a good place to study them.

Merge back with `DeletePartition` (its GPUs land in no partition) followed by `AddGpusToPartition` into 32766. The default block returns within a minute.

## Telemetry

The controller reads per-GPU NVLink byte counters that the fake CUDA stack keeps for NCCL collectives and GPU-to-GPU copies, and exposes them alongside the link and partition state:

```
$ curl -s http://10.107.111.34:9372/metrics | grep -E '^(nvlink_domain_info|nvlink_partition_gpus|nvswitch_ports_up)'
nvlink_domain_info{cluster_uuid="7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91",domain="nvl8"} 1
nvlink_partition_gpus{default="true",name="default",partition_id="32766"} 8
nvswitch_ports_up{host="sched-nvswitch",switch="NVSwitch_0"} 72
nvswitch_ports_up{host="sched-nvswitch",switch="NVSwitch_1"} 72
```

With the 8-GPU DDP job from [Part 2](02-slurm.md) running (`sbatch ddp-train.sbatch --steps 60000`), Prometheus shows the all-reduce traffic:

| Query | Reading |
|---|---|
| `sum by (host) (rate(nvlink_gpu_tx_bytes_total[1m]))` | ~1.68 TB/s from each tray |
| `rate(nvswitch_tx_bytes_total[1m])` | ~1.68 TB/s per NVSwitch chip; each GPU spreads its links over both |
| `sum(nvswitch_ports) - sum(nvswitch_ports_up)` | `0` ports down |
| `min(nvlink_gpu_healthy)` | `1` |

The Grafana dashboard **Scheduler & NVLink fabric** puts these next to the scheduler panels: unhealthy GPUs, switch ports down, GPUs per partition, and NVLink and NVSwitch throughput. Disable a switch port as in Part 4 while DDP runs, and the *switch ports down* panel and the GPU's active-link count change within one scrape.

## Ideas to build on this

- **Partition-aware admission.** Reject or re-route jobs that would span cliques. That covers the Slurm 23.11 gap above, and you can test it on the lab.
- **Tenant isolation workflows.** Create a partition per tenant, assign GPUs, verify with NVML, and tear it down, all as code against the gRPC API.
- **Fabric alerts.** Degraded GPUs, ports down and partition drift, wired to the same Prometheus as the scheduler metrics.

## Next in the series

[Part 6: Observability](06-observability.md) puts all of this on dashboards: which layers of a GPU cluster to monitor, and how to read the graphs.
