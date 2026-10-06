+++
title = "AI lab, part 7: networking, and where the emulation stops"
date = 2026-10-04
description = "The networks of a GPU cluster, what the lab emulates for each, and where the emulation stops."

[extra]
social_media_card = "card.png"
+++

[Part 5](@/ai-lab/05-nvlink/index.md) covered NVLink, the fabric *inside* an NVLink domain. A real GPU cluster has several more networks, and this is the part of the lab that is emulated most thinly. This post goes through which networks a GB200-class system has, what the lab gives you for each, and what it doesn't model at all, so you know where its answers stop being meaningful. Outputs were captured in Kubernetes mode; the network side is the same in Slurm mode, minus the pod network.

## The networks of a GPU cluster

| Network | Carries | In the lab |
|---|---|---|
| NVLink | GPU-to-GPU traffic inside one NVLink domain | emulated: links, partitions, health, traffic counters ([Part 5](@/ai-lab/05-nvlink/index.md)) |
| Compute fabric (InfiniBand or RoCE Ethernet) | GPU-to-GPU traffic *between* NVLink domains: the scale-out network | **topology only** |
| Front-end Ethernet | logins, scheduler, storage, container images, everything else | real: the Incus bridge |
| Out-of-band management | BMCs | shares the Incus bridge |

On a real rack these are separate wires. GB200 compute trays carry ConnectX adapters for the compute fabric and BlueField-3 DPUs for the front end, and the BMCs sit on their own management network. In the lab, everything that isn't NVLink runs over one Linux bridge.

## The front end: one bridge for everything

Every container has one interface on `incusbr0`. Here's a tray in Kubernetes mode:

```
$ bin/ssh sched-worker1 ip -br addr
lo               UNKNOWN        127.0.0.1/8 ::1/128
flannel.1        UNKNOWN        10.42.2.0/32 fe80::dc1c:1fff:fe03:ee5/64
cni0             UP             10.42.2.1/24 fe80::a806:acff:fead:4f65/64
veth…@if2        UP             …
eth0@if355       UP             10.107.111.21/24 metric 100 …
```

`eth0` carries everything: SSH, the scheduler's control traffic, LDAP, S3 and JuiceFS, Prometheus scrapes, and the BMCs' Redfish. In Kubernetes mode `flannel.1` adds the pod network (a 10.42.x.0/24 per node) as VXLAN over the same `eth0`. That's where the DDP job's rendezvous happens: `torchrun` in pod 1 connects to pod 0 through the JobSet's DNS name ([Part 3](@/ai-lab/03-kubernetes/index.md#simple-jobs)).

The rendezvous is a handful of small messages. The training traffic itself, the gradients exchanged in every all-reduce, never touches the Ethernet side (`eth0` and the pod network on top of it). With the 8-GPU DDP job running ([Part 6](@/ai-lab/06-observability/index.md)), Prometheus showed:

| Query | Reading |
|---|---|
| `rate(node_network_transmit_bytes_total{device="eth0", instance=~"sched-worker.*"}[1m])` | about 6 KB/s per tray |
| `sum by (host) (rate(nvlink_gpu_tx_bytes_total[1m]))` | about 1.76 TB/s per tray |

That's correct for this topology, not just an emulation shortcut. Both trays are in one NVLink domain, so a real NCCL would route the all-reduce over NVLink as well. The compute fabric only carries GPU traffic *between* NVLink domains, and the lab has only one.

## The compute fabric: InfiniBand as topology

What the lab does emulate is the part of InfiniBand that cluster software reads: the switch topology. Each tray has a fake `ibnetdiscover` that prints an NDR fabric with one spine, one leaf and four HCAs per tray, described in `/etc/fakeib.json`:

```
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

Two switches, eight HCAs (`mlx5_0`–`mlx5_3` on each tray, one per GPU, 4×NDR = 400 Gb/s each), and four leaf-to-spine uplinks. The format is the real one, so the real consumer can parse it. That consumer is NVIDIA's [topograph](https://github.com/dsx-ai-factory/topograph): every minute it runs `ibnetdiscover` on the trays, combines the switch tree with the NVLink cliques from NVML, and hands the result to the scheduler. In Kubernetes mode it becomes node labels, one per tier:

```
$ bin/kubectl get nodes -l nvidia.com/gpu.present=true \
    -L accelerator.topograph.run/domain,fabric.topograph.run/tier-0,fabric.topograph.run/tier-1
NAME            STATUS   ROLES    AGE    VERSION        DOMAIN                                   TIER-0               TIER-1
sched-worker1   Ready    <none>   137m   v1.36.5+k3s1   7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1   S-2c5eab036832a42e   S-2c5eab0306555424
sched-worker2   Ready    <none>   137m   v1.36.5+k3s1   7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1   S-2c5eab036832a42e   S-2c5eab0306555424
```

Kueue's `nvl` topology orders these as spine → leaf → NVLink domain → node, so a workload can require or prefer any of those levels; the lab's examples require one NVLink domain. In Slurm mode, topograph writes `topology.conf` instead ([Part 5](@/ai-lab/05-nvlink/index.md#how-the-scheduler-finds-out)). With one leaf and one spine, every tray shares every tier, so the switch levels never separate anything here. The emulator only knows one leaf: switch and HCA names and the HCA count are configurable (`ib_spine`, `ib_leaf`, `ib_hcas_per_node`), but a multi-leaf fabric would need changes to `fakeib` itself.

## What isn't there

The fabric exists only as `ibnetdiscover`'s output. The trays have no `/sys/class/infiniband`, no `ibstat`, no `perfquery`, and no RDMA devices:

```
$ bin/ssh sched-worker1 ls /sys/class/infiniband
ls: cannot access '/sys/class/infiniband': No such file or directory
```

The list of things the lab doesn't model is long, and worth knowing before you build on it:

- **RDMA and GPUDirect.** No verbs devices, no RDMA traffic, no GPUDirect RDMA or GPUDirect Storage. NCCL's InfiniBand transport, and anything that tunes it (`NCCL_IB_HCA`, adaptive routing, rail alignment), has nothing to act on. The fake NCCL doesn't use a network at all.
- **Fabric management.** No subnet manager, no UFM, no `ibdiagnet`, no port counters or link errors, so the scale-out layer of [Part 6](@/ai-lab/06-observability/index.md) has nothing to scrape. There are no partition keys either, so multi-tenant fabric isolation can't be tested.
- **In-network computing.** No SHARP, so no switch-offloaded reductions.
- **Ethernet compute fabrics.** No RoCE and no Spectrum-X; the compute fabric is InfiniBand-shaped only.
- **BlueField DPUs.** No DPUs and nothing DOCA-based: no DPU-offloaded storage, networking or security, no host isolation enforced by the DPU, and no DPU BMC to manage. On real GB200 trays the front end goes through BlueField-3; in the lab it's a plain Linux interface.
- **Separate networks.** Front end, storage, out-of-band management and the pod network all share one bridge, so "the management network is down while the cluster is fine" can't be rehearsed, and neither can a congested storage network.
- **Scale.** One NVLink domain, one leaf, one spine. Real clusters have hundreds of leaves and the topology questions that come with them.
- **Network faults.** There's no way to fail an InfiniBand link the way the BMCs fail an NVLink ([Part 4](@/ai-lab/04-bmc-redfish/index.md)). Network faults can only be imitated at the Linux level, for example with `tc` on the bridge.

## What you can still do with it

- Test **topology-aware placement** end to end: real `ibnetdiscover` format, real topograph, real Slurm `topology.conf` and Kueue labels. Combined with NVLink partitions ([Part 5](@/ai-lab/05-nvlink/index.md)), that's enough to see the scheduler's view change; a multi-leaf fabric is a natural extension of `fakeib`.
- Write and test **parsers and inventory tools** for `ibnetdiscover` output against a stable, known fabric.
- Exercise the **front-end network**: storage throughput on JuiceFS, the Kubernetes pod network and its DNS, LDAP and S3 from jobs.
- Make **wrong assumptions fail cheaply**. If your tooling expects `/sys/class/infiniband` or `ibstat`, the lab tells you right away, and that's a reminder to test that part on real hardware.

That's the series. The lab is at [github.com/arkady-emelyanov/ai-lab](https://github.com/arkady-emelyanov/ai-lab), and issues and pull requests are welcome.
