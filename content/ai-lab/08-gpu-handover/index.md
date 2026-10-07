+++
title = "AI lab, part 8: handing GPUs over between tenants"
date = 2026-10-06
description = "Why GPUs are reset between tenants and after NVLink partition changes, and how the lab does it with nvidia-smi --gpu-reset and a Slurm epilog."

[extra]
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/08-gpu-handover/card.png"
+++

When a GPU moves from one tenant to another, two things have to happen before the next job starts. The GPU's memory and state must be cleared, so the next tenant can't read what the previous one left behind. And if the GPU moved to another NVLink partition ([Part 5](@/ai-lab/05-nvlink/index.md)), it must start using the new one. On GB200 both happen at the same moment: a GPU reset.

This part shows the reset in the lab: how a partition change waits for it, how Slurm runs it after every job, and what happens when it fails. Commands that start with `bin/` run on the host from the repository root.

{% <admonition type="note" title="Emulated GPUs"> %}
The emulated GPUs don't need a reset, but the lab models the reset behaviour.
{% </admonition> %}

## Why GPUs are reset

NVIDIA's [GB200 NVL Partition User's Guide](https://docs.nvidia.com/multi-node-nvlink-systems/partition-guide-v1-2.pdf) recommends a reset for every GPU added to a partition (§4.1.4, Partition APIs):

> When a GPU is added to a partition (using the CreatePartition or AddGpusToPartition API), we recommend that you issue a driver reset to the added GPU. This clears the GPU's registers and memory and stale partition routing information (if the GPU was in a partition earlier). […] The reset is specifically useful in multi-tentant partition use cases where a GPU transitions from one partition/tenant to another.

Until that reset, the GPU keeps reporting its old partition. NVIDIA's [Mission Control guide](https://docs.nvidia.com/mission-control/docs/systems-administration-guide/2.2.0/nvlink-partition-management.html) says it plainly after a partition change: "We need to reset the GPUs (or reboot the nodes) in order for the Clique ID to update."

The lab follows both rules. A GPU takes its new partition's clique only at a reset or a tray reboot, and each tray resets its GPUs when it boots.

## A partition change waits for a reset

Give tray 2 its own partition, as in Part 5. `bin/nvlink` now reminds you that the GPUs need a reset:

```console
$ bin/nvlink remove default sched-worker2
GPUs removed: 32766 default
sched-worker2: GPUs 0,1,2,3 keep their old clique until reset: bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0,1,2,3  (GPUs must be idle; with Slurm the epilog resets a job's GPUs when it ends)

$ bin/nvlink create tray2 --id 7 sched-worker2
partition created: 7 tray2
sched-worker2: GPUs 0,1,2,3 keep their old clique until reset: bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0,1,2,3  (GPUs must be idle; with Slurm the epilog resets a job's GPUs when it ends)
```

The GPUs belong to partition 7, but they still report clique 1:

```console
$ bin/ssh sched-worker2 nvidia-smi --query-gpu=index,fabric.cliqueId --format=csv
index, fabric.clique_id
0, 1
1, 1
2, 1
3, 1
```

`nvidia-smi -q` shows why. The **GPU Recovery Action** field says the GPU is waiting for a reset:

```console
$ bin/ssh sched-worker2 'nvidia-smi -i 0 -q | grep -E "GPU Recovery Action|CliqueId"'
    GPU Recovery Action                   : GPU_RESET
        CliqueId                          : 1
```

The partition guide (§10.4, GPU Recovery State) describes `GPU_RESET` as "A reset is required. Do not restart the application without completing a GPU reset." `bin/nvlink` shows the same per GPU in the `RESET` column, and the tray BMC from [Part 4](@/ai-lab/04-bmc-redfish/index.md) still reports the old clique too:

```console
$ bin/nvlink gpus sched-worker2
TRAY           GPU  UUID                                      PARTITION  CLIQUE  RESET    NVLINKS  HEALTH
sched-worker2  0    GPU-81477906-d580-de42-e868-ff62e9ef4317  7          1       pending  18       healthy
sched-worker2  1    GPU-8147ef90-7f0c-fc1e-c070-d80c4a3d939f  7          1       pending  18       healthy
sched-worker2  2    GPU-81488ef0-5668-4b3a-b971-f8fb582a0fb2  7          1       pending  18       healthy
sched-worker2  3    GPU-814804a5-291e-ba91-a4e9-01fb69ea739b  7          1       pending  18       healthy

$ bin/redfish sched-worker2 /redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0 | jq -c '.Oem.Nvidia.FabricClique'
{"CliqueId":1,"ClusterUUID":"7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91"}
```

### Resetting the GPUs

`nvidia-smi --gpu-reset` resets the GPUs; `-i` picks single ones. It needs root, and it refuses a GPU that a process is still using. With a Python process holding GPU 0:

```console
$ bin/ssh sched-worker2 nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
pid, process_name, used_memory [MiB]
1860, /shared/venv/bin/python, 20 MiB

$ bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0
Unable to reset GPU 00000000:18:00.0 because it's being used by some other process (e.g. CUDA application). Please first kill all processes using this GPU.
Some GPUs could not be reset.
```

Stop the process, and the reset goes through:

```console
$ bin/ssh sched-worker2 nvidia-smi --gpu-reset
GPU 00000000:18:00.0 was successfully reset.
GPU 00000000:2A:00.0 was successfully reset.
GPU 00000000:3A:00.0 was successfully reset.
GPU 00000000:5D:00.0 was successfully reset.
All done.
```

Now the GPUs report clique 7, nothing is pending, and the BMC agrees:

```console
$ bin/ssh sched-worker2 nvidia-smi --query-gpu=index,fabric.cliqueId --format=csv
index, fabric.clique_id
0, 7
1, 7
2, 7
3, 7

$ bin/ssh sched-worker2 'nvidia-smi -i 0 -q | grep -E "GPU Recovery Action|CliqueId"'
    GPU Recovery Action                   : None
        CliqueId                          : 7

$ bin/redfish sched-worker2 /redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0 | jq -c '.Oem.Nvidia.FabricClique'
{"CliqueId":7,"ClusterUUID":"7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91"}
```

The scheduler only learns about the new partition at this point. Within a minute of the reset, topograph splits the trays into two blocks:

```console
$ bin/ssh sched-control cat /etc/slurm/topology.conf
# block001=7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.1
BlockName=block001 Nodes=sched-worker1
# block002=7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91.7
BlockName=block002 Nodes=sched-worker2
```

## Slurm resets GPUs after every job

Resetting by hand works for a partition change, but GPUs change hands far more often: every time a job ends and the next one starts. Slurm has a hook for that, the **Epilog**: a script that `slurmd` runs as root on each of a job's nodes after the job ends. Two details from [Slurm's documentation](https://slurm.schedmd.com/prolog_epilog.html) make it a good place for a handover:

- `SLURM_JOB_GPUS` holds the job's GPU indexes, so the script knows which GPUs to reset.
- "If the Epilog fails (returns a non-zero exit code), this will result in the node being set to a DRAIN state." A GPU that can't be cleaned takes its node out of service, so no other job lands on it.

The lab's epilog resets the job's GPUs, then checks that no process is left on them and no recovery action is pending:

```console
$ bin/ssh sched-worker1 cat /etc/slurm/epilog.sh
#!/bin/bash
# Slurm Epilog (root, on the job's nodes). A non-zero exit drains the node.
[ -n "$SLURM_JOB_GPUS" ] || exit 0
log() { logger -t slurm-epilog "job $SLURM_JOB_ID: $*"; }
if ! out=$(nvidia-smi --gpu-reset -i "$SLURM_JOB_GPUS" 2>&1); then
    log "GPU reset of $SLURM_JOB_GPUS failed: $out"
    exit 1
fi
busy=$(nvidia-smi -i "$SLURM_JOB_GPUS" --query-compute-apps=pid --format=csv,noheader | wc -l)
pending=$(nvidia-smi -i "$SLURM_JOB_GPUS" -q | grep "GPU Recovery Action" | grep -vc ": None$")
if [ "$busy" -ne 0 ] || [ "$pending" -ne 0 ]; then
    log "GPUs $SLURM_JOB_GPUS not clean after reset: $busy processes, $pending recovery actions"
    exit 1
fi
log "GPUs $SLURM_JOB_GPUS reset"
```

The same reset completes a partition change, so you don't have to reset by hand if a job will run on those GPUs anyway. Put tray 2 back into the default partition, without a reset:

```console
$ bin/nvlink delete tray2
partition deleted; its GPUs are in no partition
sched-worker2: GPUs 0,1,2,3 keep their old clique until reset: bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0,1,2,3  (GPUs must be idle; with Slurm the epilog resets a job's GPUs when it ends)

$ bin/nvlink add default sched-worker2
GPUs added: 32766 default
sched-worker2: GPUs 0,1,2,3 keep their old clique until reset: bin/ssh sched-worker2 nvidia-smi --gpu-reset -i 0,1,2,3  (GPUs must be idle; with Slurm the epilog resets a job's GPUs when it ends)

$ bin/nvlink gpus sched-worker2
TRAY           GPU  UUID                                      PARTITION  CLIQUE  RESET    NVLINKS  HEALTH
sched-worker2  0    GPU-81477906-d580-de42-e868-ff62e9ef4317  32766      7       pending  18       healthy
sched-worker2  1    GPU-8147ef90-7f0c-fc1e-c070-d80c4a3d939f  32766      7       pending  18       healthy
sched-worker2  2    GPU-81488ef0-5668-4b3a-b971-f8fb582a0fb2  32766      7       pending  18       healthy
sched-worker2  3    GPU-814804a5-291e-ba91-a4e9-01fb69ea739b  32766      7       pending  18       healthy
```

Run a job on the tray as `joe`. It still sees the old clique, 7:

```console
$ bin/ssh login 'srun -w sched-worker2 --gpus=4 nvidia-smi --query-gpu=index,fabric.cliqueId --format=csv,noheader'
0, 7
1, 7
2, 7
3, 7
```

When the job ends, the epilog resets its GPUs and logs it to the tray's journal. The GPUs are back in clique 1:

```console
$ bin/ssh sched-worker2 journalctl -t slurm-epilog -o cat -n 1
job 26: GPUs 0,1,2,3 reset

$ bin/nvlink gpus sched-worker2
TRAY           GPU  UUID                                      PARTITION  CLIQUE  RESET  NVLINKS  HEALTH
sched-worker2  0    GPU-81477906-d580-de42-e868-ff62e9ef4317  32766      1       -      18       healthy
sched-worker2  1    GPU-8147ef90-7f0c-fc1e-c070-d80c4a3d939f  32766      1       -      18       healthy
sched-worker2  2    GPU-81488ef0-5668-4b3a-b971-f8fb582a0fb2  32766      1       -      18       healthy
sched-worker2  3    GPU-814804a5-291e-ba91-a4e9-01fb69ea739b  32766      1       -      18       healthy
```

### When the handover fails

A process that Slurm doesn't know about can block the reset. Start one on `sched-worker1` that holds GPU 0, as in the previous section, then run a one-GPU job on that tray. Slurm gives the job GPU 0, because it doesn't see the other process:

```console
$ bin/ssh login 'srun -w sched-worker1 --gpus=1 bash -c "echo \$CUDA_VISIBLE_DEVICES"'
0
```

The job itself works. The epilog after it can't reset the GPU, logs why, and exits non-zero:

```console
$ bin/ssh sched-worker1 journalctl -t slurm-epilog -o cat -n 1
job 27: GPU reset of 0 failed: Unable to reset GPU 00000000:18:00.0 because it's being used by some other process (e.g. CUDA application). Please first kill all processes using this GPU.
Some GPUs could not be reset.
```

Slurm drains the node, so no other job lands on that GPU:

```console
$ bin/ssh login sinfo -R
REASON               USER      TIMESTAMP           NODELIST
Epilog error         slurm     2026-10-07T02:58:49 sched-worker1

$ bin/ssh login 'sinfo -N -o "%N %T"'
NODELIST STATE
sched-worker1 drained
sched-worker2 idle
```

To recover, find and stop the process, reset the GPU (`bin/ssh sched-worker1 nvidia-smi --gpu-reset -i 0`), and resume the node (`bin/ssh sched-control 'scontrol update nodename=sched-worker1 state=resume'`).

The handover is on by default. `gpu_handover_reset: false` in `inventory/group_vars/all.yml` turns it off.

## Kubernetes has no such hook

Kubernetes runs nothing on the node after a pod ends that could reset its GPUs, so in Kubernetes mode the lab has no handover. The GPUs are reset when a tray boots, and after a partition change you reset idle GPUs by hand with the same `nvidia-smi --gpu-reset`.

## What to build on this

- **A handover check of your own.** Extend the epilog with the checks your cluster needs, and test what happens when one fails.
- **Partition changes as a workflow.** Change the partition, wait for the jobs on those GPUs to end (or drain the nodes), let the epilog reset them, and verify the new cliques in NVML and the scheduler's topology.
- **Alerts on stuck resets.** The partition controller exports `nvlink_gpu_reset_pending`, which is 1 while a GPU waits for its reset. A GPU that stays pending is one the scheduler still sees in its old partition.

That's the series. The lab is at [github.com/arkady-emelyanov/ai-lab](https://github.com/arkady-emelyanov/ai-lab), and issues and pull requests are welcome.
