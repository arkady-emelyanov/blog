# AI lab

[ai-lab](https://github.com/arkady-emelyanov/ai-lab) is a complete GPU cluster that runs on one Linux machine: one NVIDIA GB200-class NVL8 NVLink domain, emulated in Incus containers, with Slurm or Kubernetes, BMCs, an NVLink switch tray, storage and monitoring around it. The GPUs are fake; everything around them is real.

> Independent personal project, not affiliated with or endorsed by NVIDIA. It emulates NVIDIA hardware interfaces in software for research and education.

1. [Intro](01-intro.md): what the lab is, what you can do with it, and how to set it up.
2. [Slurm](02-slurm.md): GPU scheduling, jobs from `srun` to PyTorch DDP, drains, quotas and monitoring.
3. [Kubernetes](03-kubernetes.md): the same hardware with k3s, Kueue and JobSet.
4. [BMC and Redfish](04-bmc-redfish.md): out-of-band management, NVLink faults and power cycles.
5. [NVLink](05-nvlink.md): partitions, fabric health and telemetry, and how they reach the scheduler.
6. [Observability](06-observability.md): what to monitor on a GPU cluster, layer by layer, and how to read the graphs.
7. [Networking](07-networking.md): the networks of a GPU cluster and where the emulation stops.
