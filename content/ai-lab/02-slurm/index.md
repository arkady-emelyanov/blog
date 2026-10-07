+++
title = "AI lab, part 2: Slurm"
date = 2026-09-29
description = "Running the emulated GB200 cluster with Slurm: GPU scheduling, jobs from srun to PyTorch DDP, drains, quotas and monitoring."

[extra]
# Series navigation and table of contents are placed in the body (below).
toc = false
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/02-slurm/card.png"
+++

<!-- series_intro -->

<h3>Table of contents</h3>

<!-- toc -->

## Overview

[Part 1](@/ai-lab/01-intro/index.md) introduced the lab and NVLink, NVIDIA's GPU-to-GPU interconnect: one NVLink domain modelled on GB200 NVL72 and scaled down to two trays of four emulated GB200 GPUs (NVL8, the lab's own name, not an NVIDIA product), running on a single Linux machine. This post covers the default scheduler, Slurm 23.11.

[Slurm](https://github.com/SchedMD/slurm) is an open-source job scheduler, common on HPC and GPU clusters. You submit a job, Slurm finds free resources for it, runs it and records what it used. A Slurm cluster has three kinds of nodes:

- **Login node**: where users log in and submit jobs. In the lab: `sched-login`
- **Controller**: runs `slurmctld`, which decides where each job runs. In the lab: `sched-control`
- **Compute nodes**: run `slurmd`, which starts the jobs. In the lab: the two trays

I'll show how Slurm is configured for GPUs, then run jobs from a one-liner up to PyTorch DDP (training one model on several GPUs at once) across both trays, do some routine operations, and watch it all in Prometheus.

Commands that start with `bin/` run on the host, from the repository root. All other commands run on the login node as `joe`, the lab's regular user from [Part 1](@/ai-lab/01-intro/index.md#first-contact); `bin/ssh login` gets you there.

## Looking at the cluster

```console
$ sinfo
PARTITION AVAIL  TIMELIMIT  NODES  STATE NODELIST
gpu*         up   infinite      2   idle sched-worker[1-2]

$ sinfo -N -o "%N %G %c %m %d %T"
NODELIST GRES CPUS MEMORY TMP_DISK STATE
sched-worker1 gpu:gb200:4 4 7500 51200 idle
sched-worker2 gpu:gb200:4 4 7500 51200 idle
```

There is one partition with two nodes, one per tray. Each node has:

- four GPUs of type `gb200`
- 4 cores
- 7.5 GB of schedulable memory
- 50 GB of node-local scratch

Slurm treats GPUs as a **generic resource** (GRES), written as `gpu:gb200:4`. Jobs ask for GPUs the same way they ask for CPUs and memory. These are the resource lines of `ddp-train.sbatch`, one of the lab's example jobs, which asks for two nodes with four GPUs each:

```bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=4
#SBATCH --cpus-per-task=4
```

`scontrol show node` gives the full picture, including what's allocated right now:

```console
$ scontrol show node sched-worker1
NodeName=sched-worker1 Arch=x86_64 CoresPerSocket=4
   …
   Gres=gpu:gb200:4
   RealMemory=7500 AllocMem=0 FreeMem=9109 Sockets=1 Boards=1
   State=IDLE ThreadsPerCore=1 TmpDisk=51200 Weight=1 Owner=N/A MCS_label=N/A
   Partitions=gpu
   CfgTRES=cpu=4,mem=7500M,billing=4,gres/gpu=4
   AllocTRES=
```

`CfgTRES` is what the node offers and `AllocTRES` what jobs hold right now, empty on an idle node. TRES (trackable resources) is the name Slurm uses for anything it counts and accounts for, GPUs included.

Accounting is enforced, so a user without an **association** (the link between a user, an account and a cluster, which limits hang off) can't submit anything. `joe` belongs to the account `lab`:

```console
$ sacctmgr -n show assoc user=joe format=cluster,account,user,qos
      nvl8        lab        joe               normal
```

## How Slurm is configured for GPUs

The configuration is short. These are the lines that matter for GPUs, taken from `/etc/slurm/slurm.conf` on `sched-control`:

```
SelectType=select/cons_tres
SelectTypeParameters=CR_Core_Memory
DefMemPerCPU=1875
GresTypes=gpu
TopologyPlugin=topology/block
TmpFS=/scratch
AccountingStorageEnforce=associations,limits,qos
AccountingStorageTRES=gres/gpu
ProctrackType=proctrack/linuxproc
TaskPlugin=task/none
NodeName=sched-worker1,sched-worker2 … RealMemory=7500 TmpDisk=51200 Gres=gpu:gb200:4
```

- **`cons_tres`** schedules CPUs, memory and GPUs as separate trackable resources, so two jobs can share a tray.
- **`gres.conf`** maps each GPU to its device node and tells Slurm that every pair is connected by 18 NVLinks, matching what `nvidia-smi topo -m` reports:

  ```
  NodeName=sched-worker1,sched-worker2 Name=gpu Type=gb200 File=/dev/nvidia0 Links=-1,18,18,18
  …
  ```

- **`topology/block`** is about NVLink partitions. An NVLink domain can be split into partitions, and GPUs in different partitions can't reach each other over NVLink. Right after setup, all 8 GPUs are in one partition. `topology/block` keeps a job's nodes in one partition when the job fits, so its GPUs talk over NVLink instead of the slower network. Slurm calls such a group of nodes a block, and reads the blocks from `topology.conf`. NVIDIA's [topograph](https://github.com/dsx-ai-factory/topograph) writes that file every minute from the current NVLink state (more on that in [Part 5](@/ai-lab/05-nvlink/index.md)):

  ```console
  $ scontrol show topology
  BlockName=block001 BlockIndex=0 Nodes=sched-worker[1-2]
  ```

- **`AccountingStorageTRES=gres/gpu`** records GPU usage per job, user and account.
- **`proctrack/linuxproc` and `task/none`** have a lab-specific reason: the trays are unprivileged containers, so Slurm has no cgroups to confine jobs. GPU isolation therefore comes from `CUDA_VISIBLE_DEVICES`, which the emulated CUDA driver honours.

## Running jobs

### GPU binding

Slurm calls assigning specific GPUs to a task *binding*, set with the `--gpu-bind` option of [`srun`](https://slurm.schedmd.com/srun.html). Slurm tells each task which GPUs it may use through the `CUDA_VISIBLE_DEVICES` variable. Ask for 8 tasks with one GPU each and print the variable in every task:

```console
$ srun -N2 --ntasks-per-node=4 --gpus-per-task=1 bash -c 'echo $(hostname) $CUDA_VISIBLE_DEVICES' | sort
sched-worker1 0
sched-worker1 1
sched-worker1 2
sched-worker1 3
sched-worker2 0
sched-worker2 1
sched-worker2 2
sched-worker2 3
```

The four tasks on each tray got GPUs 0, 1, 2 and 3, so no two tasks share a GPU.

### CUDA only sees the GPUs the job asked for

Ask for two GPUs and count them from PyTorch:

```console
$ srun -N1 --gpus-per-node=2 bash -c 'echo CVD=$CUDA_VISIBLE_DEVICES;
    /shared/venv/bin/python -c "import torch; print(torch.cuda.device_count())"'
CVD=0,1
2
```

`nvidia-smi` is different: inside the same job it still lists all four GPUs on the tray. Real `nvidia-smi` ignores `CUDA_VISIBLE_DEVICES` too. On a real cluster, Slurm usually hides the other GPUs with cgroups, but the lab's trays are containers without cgroup control, so all four stay visible. Keep that in mind if you write tooling that counts GPUs.

### A batch job across the domain

The repository has example jobs in `examples/`. Copy them to `joe`'s home on the login node with `bin/scp`, which works like `scp` with the lab's addresses and keys filled in:

```
bin/scp -r examples login:
```

The Slurm examples include `nvl8-hello.sbatch`, which runs eight tasks with one GPU each. In a distributed job each task has a number, its **rank**, from 0 to 7 here; ranks are how the tasks address each other. A GPU's **clique** is NVIDIA's ID for the NVLink partition it belongs to: GPUs with the same clique ID can talk to each other over NVLink. Every task reports its rank, its GPU's UUID and its clique:

```console
$ cd examples/slurm && sbatch --wait nvl8-hello.sbatch
Submitted batch job 14

$ cat nvl8-hello-14.out
job 14 on sched-worker[1-2]: 8 tasks
0: rank 0 on sched-worker1 CUDA_VISIBLE_DEVICES=0: NVIDIA GB200, GPU-81501924-f170-f6d0-f2da-dc7595876881, 1, scratch  198G
1: rank 1 on sched-worker1 CUDA_VISIBLE_DEVICES=1: NVIDIA GB200, GPU-8150a4ae-478e-5534-f66c-621a95f85f59, 1, scratch  198G
…
7: rank 7 on sched-worker2 CUDA_VISIBLE_DEVICES=3: NVIDIA GB200, GPU-814804a5-291e-ba91-a4e9-01fb69ea739b, 1, scratch  198G
```

The `scratch` column is the size `df` reports for `/scratch`, which is the host's filesystem, not the 50 GB that Slurm advertises as `TmpDisk`. All eight GPUs are in clique `1`, which is the same NVLink partition, so traffic between any two ranks can stay on NVLink. [Part 5](@/ai-lab/05-nvlink/index.md) splits the domain and shows what Slurm does when a job can't fit in one partition.

### PyTorch DDP on 8 GPUs

DDP (distributed data parallel) is the most common way to train on several GPUs: every GPU holds a copy of the model, trains on its own slice of the data, and after each step all GPUs average their gradients in an **all-reduce**. On this cluster, the all-reduce runs over NVLink. `ddp-train.sbatch` starts one `torchrun` per tray, with four ranks each, NCCL as the backend and rendezvous on the first node. It needs the frameworks venv (`make frameworks`), and extra arguments go straight to the training script:

```console
$ sbatch ddp-train.sbatch --steps 60000
Submitted batch job 11

$ grep -E "^step +(10|50|60000) |rank 0/" ddp-train-11.out
step   10      4.7 ms    108994 samples/s      44 TFLOP/s/GPU
step   50      4.1 ms    125887 samples/s      51 TFLOP/s/GPU
step 60000      4.3 ms    119404 samples/s      48 TFLOP/s/GPU
rank 0/8 on sched-worker1 cuda:0 (NVIDIA GB200) peak mem 7.1 GiB
```

The step time is simulated: it's roughly how long the matrix multiplications and the all-reduce would take on GB200s. Change `--width` or `--batch` and it changes about as it would on real hardware. The loss values are not real, because the emulated GPUs don't do the math.

## Operations

These are the routine admin tasks: checking who used which GPUs, taking a node out for maintenance, and limiting how many GPUs a user can hold.

### Accounting

Every job is recorded with its GPU allocation:

```console
$ sacct -X -o JobID,JobName,User,Account,AllocTRES%45,Elapsed,State
11            ddp-train       joe        lab  billing=8,cpu=8,gres/gpu=8,mem=15000M,node=2   00:04:22  COMPLETED
12           nvl8-hello       joe        lab  billing=8,cpu=8,gres/gpu=8,mem=15000M,node=2   00:00:01  COMPLETED
```

The job IDs differ from the ones above because the listings come from separate runs of the lab. GPU-hour reports and chargeback are built on this data.

### Draining a node for maintenance

Draining tells Slurm to let running jobs finish but start no new ones on the node, so you can work on it without killing anyone's job. Admin commands run as root on the controller, so run them from the host through `bin/ssh sched-control`:

```console
$ bin/ssh sched-control 'scontrol update nodename=sched-worker2 state=drain reason="maintenance: BMC firmware"'

$ bin/ssh sched-control sinfo -R
REASON               USER      TIMESTAMP           NODELIST
maintenance: BMC fir root      2026-10-05T19:31:07 sched-worker2
```

A job that needs both trays now waits, and Slurm says why:

```console
$ sbatch -J two-trays -N2 --gpus-per-node=4 --wrap "nvidia-smi -L"

$ squeue
JOBID PARTITION     NAME     USER ST  TIME  NODES NODELIST(REASON)
   17       gpu two-tray      joe PD  0:00      2 (Nodes required for job are DOWN, DRAINED or reserved for jobs in higher priority partitions)
```

The partitions in that message are **Slurm partitions**, not NVLink ones. A Slurm partition is a named group of nodes that jobs are submitted to, much like a queue; the lab has one, `gpu`.

`bin/ssh sched-control scontrol update nodename=sched-worker2 state=resume` brings the node back, and job 17 runs within seconds. In [Part 4](@/ai-lab/04-bmc-redfish/index.md) I power-cycle a tray through its BMC, which is the other half of this runbook.

### GPU quotas

Limits live on associations. Cap `joe` at four GPUs, on the host:

```console
$ bin/ssh sched-control sacctmgr -i modify user joe set GrpTRES=gres/gpu=4
```

Then, as `joe` on the login node, submit an 8-GPU job and a 4-GPU job:

```console
$ sbatch -J big -N2 --gpus-per-node=4 --wrap "sleep 5"

$ sbatch -J small -N1 --gpus-per-node=4 --wrap "sleep 5"

$ squeue -o "%.5i %.8j %.2t %.20R"
JOBID     NAME ST     NODELIST(REASON)
   18      big PD       (AssocGrpGRES)
   19    small  R        sched-worker1
```

The 8-GPU job waits on `AssocGrpGRES` while the 4-GPU job runs. `GrpTRES=gres/gpu=-1` removes the limit again. This is a cheap place to test a quota tool or a fair-share policy before you try it on a cluster people depend on.

## Monitoring

Slurm knows which GPUs a job holds, but not whether the job is using them. While the 60,000-step DDP job runs, the tray looks busy:

```console
$ bin/ssh sched-worker1 nvidia-smi --query-gpu=index,utilization.gpu,memory.used,power.draw,temperature.gpu --format=csv
index, utilization.gpu [%], memory.used [MiB], power.draw [W], temperature.gpu
0, 86 %, 7850 MiB, 863.87 W, 70
1, 86 %, 7850 MiB, 871.89 W, 69
…

$ bin/ssh sched-worker1 nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
pid, process_name, used_memory [MiB]
4375, /shared/venv/bin/python, 7338 MiB
…
```

Compare that with the idle numbers from Part 1 (about 140 W, 32 °C, 0 %).

Prometheus (`http://10.107.111.10:9090`) collects the same GPU values through the GPU exporter. It also collects job and node data from the Slurm exporter, and GPU allocations from a small collector. Recording rules combine these into `sched_*` series that look the same whichever scheduler is running, and the dashboards are built on those. In Kubernetes mode, the same series come from Kubernetes instead ([Part 3](@/ai-lab/03-kubernetes/index.md)).

Here are some of them while the DDP job runs, with `nvl8-hello` submitted after it and waiting for free GPUs:

```
# GPUs allocated
sched_gpus_alloc
8

# GPUs per user
sched_user_gpus
{user="joe"} 8

# jobs by state
sched_jobs
RUNNING 1, PENDING 1

# GPU utilisation per tray (4 GPUs × ~86 %)
sum by (instance) (nvidia_smi_utilization_gpu_ratio)
3.45, 3.48

# all-reduce traffic over NVLink
sum(rate(nvlink_gpu_tx_bytes_total[1m]))
~3.6 TB/s
```

### Lab overview dashboard

Open Grafana (`http://10.107.111.10:3000`) and the **Lab overview** dashboard:

{{< figure src="lab-overview.png" alt="Lab overview dashboard: GPU utilisation, memory, power, temperature and processes rising together while the DDP job runs" caption="Lab overview dashboard during the DDP run" />}}

All eight GPUs move together:

- **GPU utilisation** jumps to about 86-88 % when the job starts and drops to 0 when it ends.
- **GPU memory used** goes to about 7 GiB per GPU and stays there until the job ends.
- **GPU power** goes from about 140 W to 860-900 W per GPU.
- **GPU temperature** climbs to 69-70 °C over about a minute. When the job ends, it drops straight back to idle: the epilog resets the job's GPUs ([Part 8](@/ai-lab/08-gpu-handover/index.md)).
- **Processes on GPUs** shows 4 per tray, one per GPU.
- **NVL8 domain power** is the total for all eight GPUs: about 7.1 kW.

The panels at the bottom come from the BMCs; [Part 6](@/ai-lab/06-observability/index.md) covers them.

### Scheduler & NVLink fabric dashboard

This dashboard shows allocated versus total GPUs, GPUs per user and jobs by state. In **Jobs by state**, the DDP job is running (blue) and `nvl8-hello` is pending (yellow) until the GPUs free up. The panel is stacked, so the top line at 2 is both jobs together:

{{< figure src="panel-jobs-by-state.png" alt="Jobs by state: one job running, one pending until the GPUs free up" caption="Jobs by state panel during the DDP run" />}}

Draining a node or submitting a job that can't run shows up here too, which makes the lab a convenient place to build alerts. [Part 6](@/ai-lab/06-observability/index.md) walks through both dashboards panel by panel.

## Next in the series

[Part 3: Kubernetes](@/ai-lab/03-kubernetes/index.md) uses Kubernetes (k3s with Kueue and JobSet) as the scheduler on the same hardware, and runs the same jobs as pods.
