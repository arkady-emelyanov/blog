+++
title = "AI lab, part 3: Kubernetes"
date = 2026-09-30
description = "The same emulated GPU cluster on Kubernetes: GPU pods, Kueue topology-aware queueing, JobSet and monitoring."

[extra]
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/03-kubernetes/card.png"
+++

## Overview

[Part 2](@/ai-lab/02-slurm/index.md) ran the lab under Slurm. This part uses Kubernetes as the scheduler on the same hardware.

Slurm does GPU allocation, topology and queueing on its own. Kubernetes wasn't designed for batch GPU work, so it needs add-ons for them. The main one is [Kueue](https://github.com/kubernetes-sigs/kueue), which adds job queues and quotas for CPU, memory and GPUs.

I'll show which add-on does what, how GPUs and NVLink topology ([Part 1](@/ai-lab/01-intro/index.md#what-the-lab-is-made-of)) look in Kubernetes, how the same jobs run as pods, and how Kueue queues them.

To switch, set `scheduler: k3s` in `inventory/group_vars/all.yml`, then run `make down && make up` ([Part 1](@/ai-lab/01-intro/index.md#setup)). Commands that start with `bin/` run on the host, from the repository root. All other commands run on the login node as `joe` (`bin/ssh login`), whose kubeconfig and namespace are already set up. `bin/kubectl` runs `kubectl` on the host with the cluster-admin kubeconfig; commands that need admin rights use it.

## What Kubernetes sees

Kubernetes describes hardware with node **labels**, which pods and queues can select on. Two of them carry the NVLink topology, `nvidia.com/gpu.clique` and `accelerator.topograph.run/domain`:

```console
$ kubectl get nodes -L nvidia.com/gpu.clique,accelerator.topograph.run/domain
NAME            STATUS   ROLES           AGE     VERSION        GPU.CLIQUE                               DOMAIN
sched-control   Ready    control-plane   5m12s   v1.36.5+k3s1                                            
sched-worker1   Ready    <none>          4m56s   v1.36.5+k3s1   7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1   7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1
sched-worker2   Ready    <none>          4m56s   v1.36.5+k3s1   7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1   7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1
```

The two trays are worker nodes. The controller runs the control plane and add-ons, and is tainted so pods don't land on it. Both trays carry the same NVLink clique, the ID of the NVLink partition their GPUs are in (`<cluster UUID>.<clique ID>`, the same clique `1` Slurm's jobs reported in Part 2), and two different components publish it. `nvidia.com/gpu.clique` comes from NVIDIA's GPU Feature Discovery (GFD), which reads it from NVML. `accelerator.topograph.run/domain` comes from topograph, which rebuilds it from the live NVLink and InfiniBand fabric every minute.

```console
$ kubectl get nodes -o custom-columns='NODE:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu,PRODUCT:.metadata.labels.nvidia\.com/gpu\.product,MEM:.metadata.labels.nvidia\.com/gpu\.memory'
NODE            GPU      PRODUCT        MEM
sched-control   <none>   <none>         <none>
sched-worker1   4        NVIDIA-GB200   189471
sched-worker2   4        NVIDIA-GB200   189471
```

GFD adds about 25 more `nvidia.com/*` labels per tray. It reads them from NVML, so they're the labels a real GB200 tray would get. For example:

- `nvidia.com/gpu.family=blackwell`
- `nvidia.com/gpu.compute.major=10`
- `nvidia.com/cuda.driver-version.full=580.95.05`
- `nvidia.com/cuda.runtime-version.full=13.0`

topograph adds two labels for the InfiniBand network between trays ([Part 7](@/ai-lab/07-networking/index.md#the-compute-fabric-infiniband)):

- `fabric.topograph.run/tier-0`: the **leaf** switch the tray is plugged into
- `fabric.topograph.run/tier-1`: the **spine** switch that connects the leaves

Kubernetes has no job queue of its own: the scheduler places each pod as soon as it's created. **Kueue** adds queues, quotas and all-or-nothing admission for multi-pod jobs. A ClusterQueue holds the quota, and a LocalQueue in each user's namespace submits to it:

```console
$ kubectl get clusterqueues
NAME   COHORT   PENDING WORKLOADS
gpu             0

$ kubectl get localqueues
NAME      CLUSTERQUEUE   PENDING WORKLOADS   ADMITTED WORKLOADS
default   gpu            0                   0
gpu       gpu            0                   0
```

In Kubernetes, `joe` gets a namespace of their own, also called `joe`. Their kubeconfig on the login node sets it as the default namespace, so every pod or job they create without `-n` lands there. They can create pods and JobSets there and read nodes and queues, but they can't touch other namespaces or cluster-wide queue objects:

```console
$ kubectl auth can-i create jobsets.jobset.x-k8s.io
yes

$ kubectl auth can-i create pods -n kube-system
no
```

## How Kubernetes is configured for GPUs

- **k3s 1.36**: control plane on `sched-control`, agents on the trays, all in unprivileged Incus containers.
- **`fakedp` and CDI**: a device plugin advertising `nvidia.com/gpu`, 4 per tray. Each GPU is a CDI device in `/etc/cdi/fakegpu.json` that injects `/dev/nvidia<n>` and the emulated driver.
- **Node Feature Discovery and GPU Feature Discovery**: the real NVIDIA and upstream components, labelling trays from NVML.
- **topograph**: labels the NVLink domain and InfiniBand tiers every minute.
- **Kueue**: ClusterQueue `gpu` (8 CPUs, 18 GiB, 8 GPUs). In each user namespace, a LocalQueue `gpu` and a `default` one for workloads without a queue label. Topology-aware scheduling.
- **JobSet**: multi-pod jobs with stable DNS names for rank 0.

A **device plugin** is how Kubernetes learns about hardware it doesn't know natively. It advertises a resource such as `nvidia.com/gpu` on each node, and when a pod lands there, it tells the container runtime which devices to hand over. **CDI** (Container Device Interface) is the format for that hand-over: a spec file listing, per device, what to inject into the container.

The lab doesn't use NVIDIA's device plugin, because it needs a real driver installation. It uses its own small plugin, `fakedp`, which hands out GPUs by UUID through CDI:

```console
$ bin/ssh sched-worker1 'jq -c "{kind, devices: [.devices[].name]}" /etc/cdi/fakegpu.json'
{"kind":"nvidia.com/gpu","devices":["0","GPU-81501924-f170-f6d0-f2da-dc7595876881","1","GPU-8150a4ae-…", …]}
```

Kueue handles topology, like `topology/block` in Slurm. Its `Topology` object, `nvl`, orders the node labels from coarse to fine:

```console
$ bin/kubectl get topology nvl -o jsonpath='{.spec.levels}' | jq -c '[.[].nodeLabel]'
["fabric.topograph.run/tier-1","fabric.topograph.run/tier-0","accelerator.topograph.run/domain","kubernetes.io/hostname"]
```

A workload annotated with `kueue.x-k8s.io/podset-required-topology: accelerator.topograph.run/domain` is admitted only if all its pods fit inside one NVLink domain. The pods of a multi-pod job are admitted together, as a gang, or not at all. Without that, a DDP job could start half its ranks and hold their GPUs while waiting for the rest.

## Running jobs

### A single GPU pod

No Kueue and no JobSet, just a pod asking for two GPUs:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: gpu-pod
spec:
  restartPolicy: Never
  containers:
    - name: smi
      image: mirror.gcr.io/library/buildpack-deps:noble
      command: [nvidia-smi, -L]
      resources:
        limits: {nvidia.com/gpu: 2}
```

{% <admonition type="note" title="Container images"> %}
The image comes from `mirror.gcr.io`, Google's public copy of Docker Hub, which has no anonymous pull limits. Plain `docker.io` names work too: each tray pulls them through the same mirror.
{% </admonition> %}

```console
$ kubectl apply -f gpu-pod.yaml
pod/gpu-pod created

$ kubectl logs gpu-pod
GPU 0: NVIDIA GB200 (UUID: GPU-81501924-f170-f6d0-f2da-dc7595876881)
GPU 1: NVIDIA GB200 (UUID: GPU-8150a4ae-478e-5534-f66c-621a95f85f59)
```

The pod sees exactly two GPUs, here GPUs 0 and 1 of `sched-worker1`. Whichever GPUs it gets, they're numbered from 0 inside the pod, just as with the real driver. `nvidia-smi` and CUDA agree on what the pod has, because CDI only injects the allocated device nodes. The pod has no queue label, but Kueue still takes it: it goes to the LocalQueue named `default` in `joe`'s namespace (a queue, not the `default` namespace) and is admitted against the same ClusterQueue quota as everything else, so nobody can grab GPUs by skipping the queue:

```console
$ kubectl get workloads | grep gpu-pod
pod-gpu-pod-357b8   default   gpu           True       True       4s
```

### Example jobs with JobSet

The repository's `examples/kubernetes/` has four example jobs, written as JobSets queued in Kueue. `submit` is a small helper: it fills in your uid, gid and home, gives the run a unique name, and with `--wait` collects the logs into `<name>.out`.

```console
$ cd examples/kubernetes

$ ./submit --wait nvl8-hello.yaml
nvl8-hello-393696

$ cat nvl8-hello-393696.out
[pod/nvl8-hello-393696-rank-0-4-74f9v/hello] rank 4 on sched-worker2 NVIDIA_VISIBLE_DEVICES=GPU-81477906-d580-de42-e868-ff62e9ef4317: NVIDIA GB200, GPU-81477906-d580-de42-e868-ff62e9ef4317, 1
…
[pod/nvl8-hello-393696-rank-0-3-qgclw/hello] rank 3 on sched-worker1 NVIDIA_VISIBLE_DEVICES=GPU-81502f39-9cad-de97-2723-3d7d71928458: NVIDIA GB200, GPU-81502f39-9cad-de97-2723-3d7d71928458, 1
```

That's eight pods, each holding one different GPU, all in clique 1. DDP works the same way: two pods with four GPUs each, and `torchrun` doing rendezvous on pod 0 through the JobSet's DNS name:

```console
$ ./submit --wait ddp-train.yaml
ddp-train-489951

$ grep -E "world=|step |rank 0/" ddp-train-489951.out
[pod/ddp-train-489951-node-0-0-tdt2z/torchrun] world=8 params=537M batch/rank=64 width=8192
[pod/ddp-train-489951-node-0-0-tdt2z/torchrun] step    1      9.3 ms     55220 samples/s      22 TFLOP/s/GPU
[pod/ddp-train-489951-node-0-0-tdt2z/torchrun] step   10      4.5 ms    114013 samples/s      46 TFLOP/s/GPU
[pod/ddp-train-489951-node-0-0-tdt2z/torchrun] step   20      4.3 ms    117940 samples/s      47 TFLOP/s/GPU
[pod/ddp-train-489951-node-0-0-tdt2z/torchrun] step   30      4.9 ms    105525 samples/s      42 TFLOP/s/GPU
[pod/ddp-train-489951-node-0-0-tdt2z/torchrun] step   40      4.3 ms    119061 samples/s      48 TFLOP/s/GPU
[pod/ddp-train-489951-node-0-0-tdt2z/torchrun] step   50      5.8 ms     87613 samples/s      35 TFLOP/s/GPU
[pod/ddp-train-489951-node-0-0-tdt2z/torchrun] rank 0/8 on ddp-train-489951-node-0-0 cuda:0 (NVIDIA GB200) peak mem 6.1 GiB
```

The default run is only 50 steps. After the first step, which includes warm-up, a step takes between 4.3 and 5.8 ms, in the same range as the same job under Slurm in Part 2. As there, the step time is simulated, and the loss values are not real.

## Operations

These are the same admin tasks as in Part 2, done the Kubernetes way: queueing jobs when the GPUs are busy, and taking a tray out of service.

### Queueing

Start a longer DDP run, `ddp-long.yaml`: a copy of `ddp-train.yaml` with `--steps 60000` added to the training script's arguments. Then submit `nvl8-hello` behind it. Kueue suspends the second JobSet and says why:

```console
$ ./submit ddp-long.yaml
ddp-long-153244

$ ./submit nvl8-hello.yaml
nvl8-hello-828000

$ kubectl get workloads
NAME                             QUEUE   RESERVED IN   ADMITTED   FINISHED   AGE
jobset-ddp-long-153244-03f59     gpu     gpu           True                  48s
jobset-ddp-train-489951-e427a    gpu     gpu           True       True       2m43s
jobset-nvl8-hello-393696-8ffa7   gpu     gpu           True       True       3m4s
jobset-nvl8-hello-828000-ef6fa   gpu                   False                 8s

$ kubectl get workload jobset-nvl8-hello-828000-ef6fa \
    -o jsonpath='{.status.conditions[?(@.type=="QuotaReserved")].message}'
couldn't assign flavors to pod set rank: insufficient unused quota for nvidia.com/gpu in flavor gb200, 8 more needed
```

No pods are created while the workload is suspended, so nothing sits half-scheduled holding GPUs. When the DDP run finishes, `nvl8-hello` is admitted and completes.

### Cordoning a tray

Cordoning marks a node unschedulable while leaving its running pods alone, the Kubernetes counterpart of a Slurm drain. It's the Kubernetes half of the power-cycle runbook from [Part 4](@/ai-lab/04-bmc-redfish/index.md). Cordoning needs admin rights, so it runs on the host:

```console
$ bin/kubectl cordon sched-worker2
```

Then, as `joe` on the login node, submit `nvl8-hello` and ask Kueue why it waits:

```console
$ ./submit nvl8-hello.yaml
nvl8-hello-460341

$ kubectl get workload jobset-nvl8-hello-460341-daffc -o jsonpath='{.status.conditions[?(@.type=="QuotaReserved")].message}'
couldn't assign flavors to pod set rank: topology "nvl" allows to fit only 4 out of 8 pod(s)
```

Kueue sees that only one tray's worth of GPUs is schedulable inside the domain and holds the whole gang. After `bin/kubectl uncordon sched-worker2` on the host, the JobSet is admitted and completes. You'll see the same message again in [Part 5](@/ai-lab/05-nvlink/index.md), where splitting the NVLink domain has the same effect as cordoning.

## Monitoring

Cluster state reaches Prometheus through kube-state-metrics (NodePort 30808): nodes and their allocatable GPUs, pods' GPU requests, Jobs and their status. Recording rules turn it into the same `sched_*` series that Slurm's exporter feeds in Part 2 (GPUs allocated, GPUs per user, jobs and nodes by state), so the Grafana dashboards don't need to know which scheduler is running. With a 60,000-step DDP run holding all 8 GPUs and `nvl8-hello` queued behind it:

```
# GPUs allocated
sched_gpus_alloc
8

# GPUs per user
sched_user_gpus
{namespace="joe", user="joe"} 8

# jobs that haven't completed
sched_jobs{state!="COMPLETED"}
RUNNING 1, PENDING 1

# nodes by state
sched_nodes
READY 2

# GPU utilisation per tray (4 GPUs × ~86 %)
sum by (instance) (nvidia_smi_utilization_gpu_ratio)
3.46, 3.44

# processes on GPUs
sum by (instance) (nvidia_smi_compute_apps)
4 per tray
```

A Job that Kueue keeps suspended counts as `PENDING`, so the queue is visible next to the allocation. In Grafana's *Scheduler & NVLink fabric* dashboard, the **Jobs by state** panel shows `nvl8-hello` as a pending band (yellow) under the running DDP job (blue). The panel is stacked, so the top line at 2 is both jobs together. The short pending dip at the start is the DDP job itself, in the moment before Kueue admitted it:

{{< figure src="panel-jobs-by-state.png" alt="Jobs by state: the DDP job running, nvl8-hello pending underneath until the GPUs free up" caption="Jobs by state panel" />}}

The pods' GPU work is visible on the trays as it would be for any process. This is the same 60,000-step run:

```console
$ bin/ssh sched-worker1 nvidia-smi --query-gpu=index,utilization.gpu,memory.used,power.draw,temperature.gpu --format=csv,noheader
0, 86 %, 7850 MiB, 888.75 W, 69
1, 87 %, 7850 MiB, 875.70 W, 69
…

$ bin/ssh sched-worker1 nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
17606, /shared/venv/bin/python, 7338 MiB
…

$ bin/kubectl -n joe exec ddp-long-153244-node-0-0-hkv4r -- nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
16, /shared/venv/bin/python, 7338 MiB
…
```

The same worker appears as PID 17606 on the tray and PID 16 inside the pod: each viewer sees the process under the PID from its own namespace, as with the real driver. [Part 6](@/ai-lab/06-observability/index.md) walks through both dashboards and what to read from each graph.

## Next in the series

[Part 4: BMCs and Redfish](@/ai-lab/04-bmc-redfish/index.md) goes below the scheduler, to the management controllers that power trays on and off and switch NVLinks.
