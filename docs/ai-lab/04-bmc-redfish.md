---
image: "ai-lab/images/social/04-bmc-redfish.png"
description: "Out-of-band management of GPU trays with Redfish BMCs: inventory, GPU sensors, NVLink faults and power cycles."
---

# AI lab, part 4: BMCs and Redfish, the out-of-band side of a GPU rack

*Series: [Intro](01-intro.md) · [Slurm](02-slurm.md) · [Kubernetes](03-kubernetes.md) · **BMC and Redfish** · [NVLink](05-nvlink.md) · [Observability](06-observability.md) · [Networking](07-networking.md)*

Parts [2](02-slurm.md) and [3](03-kubernetes.md) used the cluster the way its users do, through a scheduler. This part covers the operator's back door: the baseboard management controllers. We'll walk the Redfish tree, break NVLinks on purpose, power-cycle a tray, and see what the scheduler makes of it. The examples run in Slurm mode, but the BMCs are identical with Kubernetes.

## BMCs and Redfish in two paragraphs

A **BMC** is a small computer on every server board with its own network port. It stays up when the host is off or hung, and it can power the host on and off, report hardware inventory and health, and change firmware-level settings. In a GB200 rack every compute tray and every NVLink switch tray has one.

**Redfish** is the DMTF's REST/JSON API for BMCs, and it replaced IPMI. Everything is a resource under `/redfish/v1`: you read with `GET`, change settings with `PATCH`, and trigger actions with `POST`. Vendors add their own fields under `Oem`. NVIDIA's GB200 BMCs run a fork of OpenBMC's `bmcweb`, and the lab's `fakebmc` is modelled on it, `Oem.Nvidia` fields included.

The lab has three BMCs:

| BMC | Address | Manages |
|---|---|---|
| `sched-worker1-bmc`, `sched-worker2-bmc` | .31, .32 | a GPU tray: power, 4 GPUs, 18 NVLink ports each |
| `sched-nvswitch-bmc` | .33 | the switch tray: 2 NVSwitch chips × 72 ports |

The credentials are OpenBMC's defaults, `root` / `0penBmc`, and the certificates are self-signed. `bin/redfish <tray> <path> [curl args]` wraps `curl -k` with the credentials filled in.

## Observation

The service root is the one resource you can read without logging in:

```
$ curl -sk https://10.107.111.31/redfish/v1 | jq -c '{RedfishVersion, Systems, Chassis, Managers}'
{"RedfishVersion":"1.17.0","Systems":{"@odata.id":"/redfish/v1/Systems"},"Chassis":{"@odata.id":"/redfish/v1/Chassis"},"Managers":{"@odata.id":"/redfish/v1/Managers"}}
```

The usual three branches are there. *Systems* is what runs (host, CPUs, GPUs). *Chassis* is the physical enclosure. *Managers* is the BMC itself.

```
$ bin/redfish sched-worker1 /redfish/v1/Systems/System_0 | jq '{PowerState, Status, ProcessorSummary}'
{
  "PowerState": "On",
  "Status": { "Health": "OK", "State": "Enabled" },
  "ProcessorSummary": { "Count": 4 }
}
$ bin/redfish sched-worker1 /redfish/v1/Chassis/Chassis_0 | jq -c '{ChassisType, Model, PowerState}'
{"ChassisType":"Sled","Model":"NVIDIA GB200 compute tray","PowerState":"On"}
```

In the lab, the GPUs are processors of `System_0`. The BMC's UUID matches the one `nvidia-smi` reports inside the tray, so inventory tooling can join out-of-band and in-band data on it:

```
$ bin/redfish sched-worker1 /redfish/v1/Systems/System_0/Processors/GPU_0 | jq '{Model, UUID, SerialNumber, Oem}'
{
  "Model": "NVIDIA GB200",
  "UUID": "81501924-f170-f6d0-f2da-dc7595876881",
  "SerialNumber": "16523240298025",
  "Oem": {
    "Nvidia": {
      "@odata.type": "#NvidiaProcessor.v1_4_0.NvidiaGPU",
      "FabricClique": { "CliqueId": 1, "ClusterUUID": "7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91" },
      "PCIeBusId": "00000000:18:00.0"
    }
  }
}
$ bin/ssh sched-worker1 nvidia-smi -L | head -1
GPU 0: NVIDIA GB200 (UUID: GPU-81501924-f170-f6d0-f2da-dc7595876881)
```

> **Don't hard-code these paths.** The lab uses a simplified layout. On real GB200 compute-tray BMCs the GPUs sit behind the HMC under `/redfish/v1/Systems/HGX_Baseboard_0/Processors`, with an `HGX_GPU_<n>` chassis per GPU and an `HGX_BMC_0` manager. On the switch tray, the fabric is `MGX_NVLinkFabric_0` (the lab's is `NVLinkFabric_0`) and the switches have `MGX_NVSwitch_<n>` chassis. `make test-bmc-conformance` checks for the real layout and fails on every difference:
>
> ```
> FAILED tests/bmc/test_conformance.py::test_gpus_live_under_the_hgx_baseboard[sched-worker1]
> FAILED tests/bmc/test_conformance.py::test_gpus_live_under_the_hgx_baseboard[sched-worker2]
> FAILED tests/bmc/test_conformance.py::test_each_gpu_has_a_chassis_with_its_uuid[sched-worker1]
> FAILED tests/bmc/test_conformance.py::test_each_gpu_has_a_chassis_with_its_uuid[sched-worker2]
> FAILED tests/bmc/test_conformance.py::test_tray_has_host_bmc_and_hmc_managers[sched-worker1]
> FAILED tests/bmc/test_conformance.py::test_tray_has_host_bmc_and_hmc_managers[sched-worker2]
> FAILED tests/bmc/test_conformance.py::test_switch_tray_identifies_as_nvswitch
> FAILED tests/bmc/test_conformance.py::test_switch_fabric_name - AssertionErro...
> ```
>
> Discover resources by following `Members` links from the collections instead of building paths, and your tooling will work against both.

Each GPU has 18 NVLink ports, and each port has a pending-settings object (`@Redfish.Settings`). That's the standard Redfish pattern for changes that only take effect at the next reset:

```
$ bin/redfish sched-worker1 /redfish/v1/Systems/System_0/Processors/GPU_2/Ports/NVLink_3 \
    | jq -c '{LinkState, LinkStatus, CurrentSpeedGbps, Settings: ."@Redfish.Settings".SettingsObject}'
{"LinkState":"Enabled","LinkStatus":"LinkUp","CurrentSpeedGbps":200,"Settings":{"@odata.id":"/redfish/v1/Systems/System_0/Processors/GPU_2/Ports/NVLink_3/Settings"}}
```

The switch tray BMC describes the other end of the cable. Every switch port knows which tray, GPU and link it's connected to:

```
$ bin/redfish sched-nvswitch /redfish/v1/Fabrics/NVLinkFabric_0/Switches/NVSwitch_0/Ports/NVLink_19 \
    | jq -c '{LinkStatus, RemoteEndpoint: .Oem.Nvidia.RemoteEndpoint}'
{"LinkStatus":"LinkUp","RemoteEndpoint":{"BMC":"sched-worker1-bmc","GPU":"GPU_2","GPUUUID":"81502f39-9cad-de97-2723-3d7d71928458","Host":"sched-worker1","Port":"NVLink_2"}}
```

The tray BMCs also carry **GPU sensors**, which is how a BMC reports temperature and power when the host OS can't. On a real tray the BMC reads them from the GPUs over its own sideband bus. In the lab the BMC reads the same GPU state NVML does and applies the same model, so the two agree. Captured during a training run:

```
$ bin/redfish sched-worker1 /redfish/v1/Chassis/Chassis_0/Sensors | jq -r '.Members[]."@odata.id"'
/redfish/v1/Chassis/Chassis_0/Sensors/GPU_0_TEMP_0
/redfish/v1/Chassis/Chassis_0/Sensors/GPU_0_Power_0
…
/redfish/v1/Chassis/Chassis_0/Sensors/Total_GPU_Power_0

$ bin/redfish sched-worker1 /redfish/v1/Systems/System_0/Processors/GPU_0/EnvironmentMetrics \
    | jq -c '{Temp: .TemperatureCelsius.Reading, Power: .PowerWatts.Reading}'
{"Temp":69,"Power":889.878}
$ bin/ssh sched-worker1 nvidia-smi -i 0 --query-gpu=temperature.gpu,power.draw --format=csv
temperature.gpu, power.draw [W]
69, 870.59 W
```

The temperature matches exactly. The power readings differ by about 20 W because the two commands ran a moment apart and a busy GPU's power jitters from sample to sample (a minute earlier, one `nvidia-smi` call read the same tray's four GPUs at 865–889 W). `Total_GPU_Power_0` sums the tray's four GPUs (3557.758 W at that moment). A Redfish exporter polls these sensors and the power state into Prometheus; [Part 6](06-observability.md) shows what that looks like during a power cycle.

## Operations

### Disable NVLinks and reset the tray

Stage two links of GPU 2 as disabled. The live port is unchanged until the reset:

```
$ for l in 3 4; do
    bin/redfish sched-worker1 /redfish/v1/Systems/System_0/Processors/GPU_2/Ports/NVLink_$l/Settings \
      -X PATCH -d '{"LinkState": "Disabled"}'
  done
$ bin/redfish sched-worker1 …/GPU_2/Ports/NVLink_3 | jq -c '{LinkState, LinkStatus}'
{"LinkState":"Enabled","LinkStatus":"LinkUp"}

$ bin/redfish sched-worker1 /redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset \
    -X POST -d '{"ResetType": "ForceRestart"}'
```

About 20 seconds later the tray is back up, and both the BMC and the GPU's own view agree:

```
$ bin/redfish sched-worker1 …/GPU_2/Ports/NVLink_3 | jq -c '{LinkState, LinkStatus, Status}'
{"LinkState":"Disabled","LinkStatus":"LinkDown","Status":{"Health":"Warning","State":"Disabled"}}

$ bin/ssh sched-worker1 nvidia-smi topo -m
	GPU0	GPU1	GPU2	GPU3	CPU Affinity	NUMA Affinity	GPU NUMA ID
GPU0	X	NV18	NV16	NV18	0-3	0		N/A
GPU1	NV18	X	NV16	NV18	0-3	0		N/A
GPU2	NV16	NV16	X	NV16	0-3	0		N/A
GPU3	NV18	NV18	NV16	X	0-3	0		N/A
…
```

Slurm didn't notice anything. The tray restarted well within `SlurmdTimeout` (300 s), so it stayed `idle` throughout. On real hardware you'd drain first, and the lab lets you find out what happens when somebody doesn't. Set the links back to `"Enabled"` and reset again to restore `NV18`.

### Take a switch port down

Switch-side changes apply immediately, with no reset. Port 19 on `NVSwitch_0` is GPU 2's link 2:

```
$ bin/redfish sched-nvswitch /redfish/v1/Fabrics/NVLinkFabric_0/Switches/NVSwitch_0/Ports/NVLink_19 \
    -X PATCH -d '{"LinkState": "Disabled"}'
$ bin/ssh sched-worker1 'nvidia-smi topo -m | grep ^GPU2; nvidia-smi nvlink -s -i 2 | grep "Link 2:"'
GPU2	NV17	NV17	X	NV17	0-3	0		N/A
	 Link 2: <inactive>
```

One emulation gap shows up here: the tray BMC still reports its end of that link as `LinkUp`. On real hardware, a link that's down is down at both ends, so the GPU's port would report `LinkDown` too. The lab's `fakebmc` doesn't propagate switch-side changes to the tray BMC, and the conformance tier catches it:

```
E   AssertionError: assert 'LinkUp' == 'LinkDown'
FAILED tests/bmc/test_conformance.py::test_link_down_shows_at_both_ends - Ass...
```

For link state from the switch side, trust the switch BMC or NVML in the lab, not the tray BMC.

### Power off without draining (what not to do)

```
$ bin/redfish sched-worker2 /redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset \
    -X POST -d '{"ResetType": "ForceOff"}'
$ bin/redfish sched-worker2 /redfish/v1/Systems/System_0 | jq -c '{PowerState}'
{"PowerState":"Off"}
$ sinfo -N -o "%N %T"
NODELIST STATE
sched-worker1 idle
sched-worker2 idle
```

The tray is off, but Slurm still sees it as `idle`. Prometheus is quicker: `up{job="gpu", instance="sched-worker2:9835"}` drops to `0` within seconds. A two-tray job submitted now gets placed on the dead node and fails:

```
$ sbatch -J blind -N2 --gpus-per-node=4 --wrap "srun hostname"
Submitted batch job 20
$ sacct -X -n -j 20 -o JobID,JobName,State
20                blind     FAILED
```

Within a minute topograph also drops the unreachable tray from `topology.conf` (`BlockName=block001 Nodes=sched-worker1`). `ResetType: On` brings it back, and both the node and the block recover on their own.

### The proper power cycle, with a Redfish session

Automation should use a session token rather than sending the password on every request. You `POST` credentials once, get back `X-Auth-Token`, and `DELETE` the session when you're done:

```bash
BMC=https://10.107.111.32
TOKEN=$(curl -sk -D - -o /dev/null -X POST $BMC/redfish/v1/SessionService/Sessions \
  -H 'Content-Type: application/json' -d '{"UserName":"root","Password":"0penBmc"}' \
  | awk -F': ' 'tolower($1)=="x-auth-token"{print $2}' | tr -d '\r')
rf() { curl -sk -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' "$@"; }

bin/ssh sched-control 'scontrol update nodename=sched-worker2 state=drain reason="power cycle"'
rf -X POST $BMC/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset -d '{"ResetType":"GracefulShutdown"}'
until [ "$(rf $BMC/redfish/v1/Systems/System_0 | jq -r .PowerState)" = Off ]; do sleep 2; done
rf -X POST $BMC/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset -d '{"ResetType":"On"}'
until bin/ssh sched-worker2 systemctl is-active slurmd 2>/dev/null | grep -qx active; do sleep 3; done
bin/ssh sched-control 'scontrol update nodename=sched-worker2 state=resume'
```

I ran exactly this script against the lab. The node goes `drained` → `idle*` (resumed, waiting for slurmd to check in) → `idle` within about 20 seconds. Finish by deleting the session (`rf -X DELETE $BMC/redfish/v1/SessionService/Sessions/<id>`). The Kubernetes version swaps `drain`/`resume` for `kubectl cordon`/`uncordon` and is covered in [Part 3](03-kubernetes.md).

## What to build against it

- A **Redfish inventory collector** that joins BMC GPU UUIDs with `nvidia-smi` and the scheduler's node list.
- A **link-health check** that cross-checks the tray BMC, the switch BMC and NVML, so that a stale reading or a partial failure seen by only one source doesn't go unnoticed.
- A **maintenance runbook as code** (drain → power cycle → verify → resume) with the failure paths tested: what if the tray never comes back?
- Redfish client libraries: `make test-bmc` in the repo runs the lab's own pytest suite, and `make test-bmc-conformance` lists where `fakebmc` differs from real GB200 BMCs.

## Next in the series

[Part 5: NVLink](05-nvlink.md) moves from single links to the whole fabric: NVLink partitions, the controller that manages them, fabric telemetry, and how a partition change rewrites the scheduler's topology.
