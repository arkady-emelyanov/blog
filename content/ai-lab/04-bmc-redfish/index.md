+++
title = "AI lab, part 4: BMCs and Redfish, the out-of-band side of a GPU rack"
date = 2026-10-01
description = "Out-of-band management of GPU trays with Redfish BMCs: inventory, GPU sensors, NVLink faults and power cycles."

[extra]
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/04-bmc-redfish/card.png"
+++

Parts [2](@/ai-lab/02-slurm/index.md) and [3](@/ai-lab/03-kubernetes/index.md) used the cluster the way its users do, through a scheduler. A scheduler only knows what each tray's operating system reports, so a hung or powered-off tray can look healthy to it for minutes. This part covers the operator's back door: the baseboard management controllers (BMCs), which manage the hardware independently of the OS. I'll walk the Redfish tree, break NVLinks (NVIDIA's GPU-to-GPU links, introduced in [Part 1](@/ai-lab/01-intro/index.md#what-the-lab-is-made-of)) on purpose, and power-cycle a tray. The examples run in Slurm mode, but the BMCs are identical with Kubernetes. Commands that start with `bin/` run on the host from the repository root.

## BMCs and Redfish in two paragraphs

A **BMC** is a small computer on every server board with its own network port and its own power. Dell's iDRAC and HPE's iLO are BMCs. It stays up when the host is off or hung, and it can power the host on and off, report hardware inventory and health, and change firmware-level settings. In a GB200 rack every compute tray and every NVLink switch tray has one. A compute tray actually has two controllers: the tray's BMC, and the HGX Management Controller (HMC) on the GPU board, which looks after the GPUs. You only talk to the BMC, and it passes on what the HMC reports about the GPUs.

**Redfish** is the DMTF's REST/JSON API for BMCs, and it replaced IPMI. Everything is a resource under `/redfish/v1`: you read with `GET`, change settings with `PATCH`, and trigger actions with `POST`. Vendors add their own fields under `Oem`. NVIDIA's GB200 BMCs run a fork of OpenBMC's `bmcweb`, and the lab's `fakebmc` is modelled on it, `Oem.Nvidia` fields included.

The lab has three BMCs:

| BMC | Address | Manages |
|---|---|---|
| `sched-worker1-bmc`, `sched-worker2-bmc` | .31, .32 | a GPU tray: power, 4 GPUs, 18 NVLink ports each |
| `sched-nvswitch-bmc` | .33 | the switch tray: 2 NVSwitch chips × 72 ports |

The credentials are OpenBMC's defaults, `root` / `0penBmc`, and the certificates are self-signed.

## Reading a tray through its BMC

The service root is the one resource you can read without logging in. It links to everything else:

```console
$ curl -sk https://10.107.111.31/redfish/v1 | jq -c '{RedfishVersion, Systems, Chassis, Managers}'
{"RedfishVersion":"1.17.0","Systems":{"@odata.id":"/redfish/v1/Systems"},"Chassis":{"@odata.id":"/redfish/v1/Chassis"},"Managers":{"@odata.id":"/redfish/v1/Managers"}}
```

The usual three branches are there. *Systems* is what runs, *Chassis* is the physical hardware, and *Managers* are the controllers themselves.

Everything below the service root needs the credentials. The repository's `bin/redfish` helper takes a tray name and a path, looks up the tray's BMC address in Incus, and runs `curl -k` with the credentials filled in. Any extra arguments go to `curl`. The rest of this part uses it.

A GB200 compute tray has two systems:

```console
$ bin/redfish sched-worker1 /redfish/v1/Systems | jq -r '.Members[]."@odata.id"'
/redfish/v1/Systems/System_0
/redfish/v1/Systems/HGX_Baseboard_0
```

`System_0` is the tray's host, the Grace CPU running the OS. Power and reset live there. `HGX_Baseboard_0` is the GPU board, with the four GPUs as its processors:

```console
$ bin/redfish sched-worker1 /redfish/v1/Systems/System_0 | jq '{PowerState, Status}'
{
  "PowerState": "On",
  "Status": {
    "Health": "OK",
    "State": "Enabled"
  }
}

$ bin/redfish sched-worker1 /redfish/v1/Systems/HGX_Baseboard_0 | jq '{PowerState, Status, ProcessorSummary}'
{
  "PowerState": "On",
  "Status": {
    "Health": "OK",
    "State": "Enabled"
  },
  "ProcessorSummary": {
    "Count": 4
  }
}
```

The two controllers show up as two managers: `BMC_0` for the tray and `HGX_BMC_0` for the HMC.

```console
$ bin/redfish sched-worker1 /redfish/v1/Managers | jq -r '.Members[]."@odata.id"'
/redfish/v1/Managers/BMC_0
/redfish/v1/Managers/HGX_BMC_0

$ bin/redfish sched-worker1 /redfish/v1/Chassis/Chassis_0 | jq -c '{ChassisType, Model, PowerState}'
{"ChassisType":"RackMount","Model":"GB200 NVL","PowerState":"On"}
```

Each GPU's UUID on the BMC matches the one `nvidia-smi` reports inside the tray, so inventory tooling can join out-of-band and in-band data on it:

```console
$ bin/redfish sched-worker1 /redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0 | jq '{Model, UUID, SerialNumber, Oem}'
{
  "Model": "NVIDIA GB200",
  "UUID": "81501924-f170-f6d0-f2da-dc7595876881",
  "SerialNumber": "16523240298025",
  "Oem": {
    "Nvidia": {
      "@odata.type": "#NvidiaProcessor.v1_4_0.NvidiaGPU",
      "FabricClique": {
        "CliqueId": 1,
        "ClusterUUID": "7f3c2a10-5b4e-4d6a-9c1e-2b8f0e6d4a91"
      },
      "PCIeBusId": "00000000:18:00.0"
    }
  }
}

$ bin/ssh sched-worker1 nvidia-smi -L | head -1
GPU 0: NVIDIA GB200 (UUID: GPU-81501924-f170-f6d0-f2da-dc7595876881)
```

The GPUs also appear under *Chassis*, one `HGX_GPU_<n>` chassis per GPU, with the same UUID:

```console
$ bin/redfish sched-worker1 /redfish/v1/Chassis/HGX_GPU_0 | jq -c '{ChassisType, Model, UUID}'
{"ChassisType":"Component","Model":"NVIDIA GB200","UUID":"81501924-f170-f6d0-f2da-dc7595876881"}
```

The NVLinks are listed under each GPU processor, one port for each of the GPU's 18 NVLinks. A port has a pending-settings object (`@Redfish.Settings`). As with BIOS settings, a change is staged there and takes effect at the next reset:

```console
$ bin/redfish sched-worker1 /redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_2/Ports/NVLink_3 \
    | jq -c '{LinkState, LinkStatus, CurrentSpeedGbps, Settings: ."@Redfish.Settings".SettingsObject}'
{"LinkState":"Enabled","LinkStatus":"LinkUp","CurrentSpeedGbps":200,"Settings":{"@odata.id":"/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_2/Ports/NVLink_3/Settings"}}
```

The switch tray BMC describes the other end of the cable. Every switch port knows which tray, GPU and link it's connected to:

```console
$ bin/redfish sched-nvswitch /redfish/v1/Fabrics/MGX_NVLinkFabric_0/Switches/NVSwitch_0/Ports/NVLink_19 \
    | jq -c '{LinkStatus, RemoteEndpoint: .Oem.Nvidia.RemoteEndpoint}'
{"LinkStatus":"LinkUp","RemoteEndpoint":{"BMC":"sched-worker1-bmc","GPU":"GPU_2","GPUUUID":"81502f39-9cad-de97-2723-3d7d71928458","Host":"sched-worker1","Port":"NVLink_2"}}
```

The tray BMCs also report **GPU temperature and power**, even when the host OS is down. Each GPU's sensors are on its `HGX_GPU_<n>` chassis, and `Chassis_0` has the total power for the tray. A real BMC reads these values straight from the GPUs, without going through the host. The lab's BMC computes them the same way NVML does, so both show the same numbers. These readings were taken during a training run:

```console
$ bin/redfish sched-worker1 /redfish/v1/Chassis/HGX_GPU_0/Sensors | jq -r '.Members[]."@odata.id"'
/redfish/v1/Chassis/HGX_GPU_0/Sensors/HGX_GPU_0_TEMP_0
/redfish/v1/Chassis/HGX_GPU_0/Sensors/HGX_GPU_0_Power_0

$ bin/redfish sched-worker1 /redfish/v1/Chassis/HGX_GPU_0/EnvironmentMetrics \
    | jq -c '{Temp: .TemperatureCelsius.Reading, Power: .PowerWatts.Reading}'
{"Temp":69,"Power":870.639}

$ bin/ssh sched-worker1 nvidia-smi -i 0 --query-gpu=temperature.gpu,power.draw --format=csv
temperature.gpu, power.draw [W]
69, 892.33 W

$ bin/redfish sched-worker1 /redfish/v1/Chassis/Chassis_0/Sensors/Total_GPU_Power_0 | jq -c '{Reading, ReadingUnits}'
{"Reading":3541.377,"ReadingUnits":"W"}
```

The temperatures match. The power differs by about 20 W because the two commands ran a moment apart, and a busy GPU's power changes from second to second. `Total_GPU_Power_0` is the sum for all four GPUs.

Prometheus collects these sensors too, through a Redfish exporter. [Part 6](@/ai-lab/06-observability/index.md) shows them during a power cycle.

## Operations

This section changes the hardware through the BMCs: disabling NVLinks on a GPU tray, taking a port down on the switch tray, and powering a tray off and on.

### Disable NVLinks and reset the tray

Stage two links of GPU 2 as disabled. The live port is unchanged until the reset:

```console
$ for l in 3 4; do
    bin/redfish sched-worker1 /redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_2/Ports/NVLink_$l/Settings \
      -X PATCH -d '{"LinkState": "Disabled"}'
  done

$ bin/redfish sched-worker1 …/GPU_2/Ports/NVLink_3 | jq -c '{LinkState, LinkStatus}'
{"LinkState":"Enabled","LinkStatus":"LinkUp"}

$ bin/redfish sched-worker1 /redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset \
    -X POST -d '{"ResetType": "ForceRestart"}'
```

When the tray is back up, the BMC and the GPU's own view agree. GPU 2 now has 16 links to each peer instead of 18:

```console
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

To undo the change, set both links back to `"Enabled"` and reset the tray again. `nvidia-smi topo -m` then shows `NV18` everywhere.

### Take a switch port down

Each NVLink connects a GPU port to an NVSwitch port. The switch tray's BMC controls the switch end, and changes there apply immediately, with no reset. Port 19 on `NVSwitch_0` is GPU 2's link 2:

```console
$ bin/redfish sched-nvswitch /redfish/v1/Fabrics/MGX_NVLinkFabric_0/Switches/NVSwitch_0/Ports/NVLink_19 \
    -X PATCH -d '{"LinkState": "Disabled"}'

$ bin/ssh sched-worker1 'nvidia-smi topo -m | grep ^GPU2; nvidia-smi nvlink -s -i 2 | grep "Link 2:"'
GPU2	NV17	NV17	X	NV17	0-3	0		N/A
	 Link 2: <inactive>
```

A link that's down is down at both ends, so the tray BMC reports the GPU's port as `LinkDown` too. Its `LinkState` stays `Enabled`, because nothing was changed on the GPU side:

```console
$ bin/redfish sched-worker1 /redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_2/Ports/NVLink_2 | jq -c '{LinkState, LinkStatus}'
{"LinkState":"Enabled","LinkStatus":"LinkDown"}
```

On real hardware, a job using that link would now fail. The lab's emulated NCCL never fails, so jobs carry on; it's the one known difference from real hardware that `make test-bmc-conformance` reports. Set the port back to `"Enabled"` to restore the link.

### Power a tray off and on

```console
$ bin/redfish sched-worker2 /redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset \
    -X POST -d '{"ResetType": "ForceOff"}'

$ bin/redfish sched-worker2 /redfish/v1/Systems/System_0 | jq -c '{PowerState}'
{"PowerState":"Off"}
```

The tray is off, but its BMC still answers. That's the point of a BMC: it's the one way to reach a tray whose OS is gone. Prometheus notices the tray is gone within seconds: `up{job="gpu", instance="sched-worker2:9835"}` drops to `0`. `"ResetType": "On"` powers the tray back on.

### Using Redfish sessions

So far every request sent the password. Scripts that talk to a BMC for a while should log in once and use a session token instead.

Log in by posting the credentials to the session collection. The BMC answers with the token in the `X-Auth-Token` header, and the session's own address in `Location`:

```console
$ curl -sk -D - -o /dev/null -X POST https://10.107.111.32/redfish/v1/SessionService/Sessions \
    -H 'Content-Type: application/json' -d '{"UserName":"root","Password":"0penBmc"}' \
    | grep -iE '^(HTTP|x-auth-token|location)'
HTTP/2 201
location: /redfish/v1/SessionService/Sessions/93694e3cef
x-auth-token: 3c4c9f1d0a424028a33f658dff623032
```

Send the token with every request instead of the password:

```console
$ curl -sk -H "X-Auth-Token: 3c4c9f1d0a424028a33f658dff623032" \
    https://10.107.111.32/redfish/v1/Systems/System_0 | jq -c '{PowerState}'
{"PowerState":"On"}
```

Log out by deleting the session. After that, the token no longer works (`401`):

```console
$ curl -sk -o /dev/null -w '%{http_code}\n' -X DELETE -H "X-Auth-Token: 3c4c9f1d0a424028a33f658dff623032" \
    https://10.107.111.32/redfish/v1/SessionService/Sessions/93694e3cef
204

$ curl -sk -o /dev/null -w '%{http_code}\n' -H "X-Auth-Token: 3c4c9f1d0a424028a33f658dff623032" \
    https://10.107.111.32/redfish/v1/Systems/System_0
401
```

## What to build against it

- A **Redfish inventory collector** that joins BMC GPU UUIDs with `nvidia-smi` and the scheduler's node list.
- A **link-health check** that cross-checks the tray BMC, the switch BMC and NVML, so that a stale reading or a partial failure seen by only one source doesn't go unnoticed.
- A **maintenance runbook as code** (drain → power cycle → verify → resume) with the failure paths tested: what if the tray never comes back?
- Redfish client libraries: the lab's BMCs follow NVIDIA's GB200 layout, and `make test-bmc` in the repo runs the lab's own pytest suite against them.

## Next in the series

[Part 5: NVLink](@/ai-lab/05-nvlink/index.md) moves from single links to the whole fabric: NVLink partitions, the controller that manages them, fabric telemetry, and how a partition change rewrites the scheduler's topology.
