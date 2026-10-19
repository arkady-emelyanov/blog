+++
title = "AI lab, part 9: Inference servers"
date = 2026-10-12
draft = true
description = "Running SGLang and vLLM on the emulated GB200s: one works unmodified, the other reads its bookkeeping off a GPU that computes nothing."

[extra]
# Series navigation and table of contents are placed in the body (below).
toc = false
social_media_card = "card.png"
# Thumbnail in the post list.
local_image = "ai-lab/09-inference/card.png"
+++

<!-- series_intro -->

<h3>Table of contents</h3>

<!-- toc -->

## Overview

The first eight parts built the cluster and ran training-shaped work on it: `srun`, PyTorch DDP, NCCL across trays. This part runs the other half of a GPU cluster's workload, LLM inference, using the official images of two servers: SGLang and vLLM. Both run under Kubernetes, on one GPU, with tensor parallelism inside a tray, and with tensor parallelism across both trays.

The emulated GPUs compute nothing ([Part 1](@/ai-lab/01-intro/index.md)), so the answers are meaningless. What the lab tests is everything around the arithmetic: the deployment, Kueue queueing, startup, the OpenAI-compatible API, batching and caching, how the servers set up communication, how they behave under concurrent load, and which fabric their tensor-parallel traffic crosses.

The short version is that SGLang runs unmodified and vLLM does not. Working out why took me into vLLM's model runner, and the reason turns out to say something about the design rather than about the emulation.

## Two servers, one of them unmodified

Both images are the published ones, with nothing patched:

- SGLang, `lmsysorg/sglang:v0.5.21-cu130-runtime`. Unmodified, it completes 832 of 832 requests at up to 64 concurrent, on one GPU, with TP=4 inside a tray, and with TP=8 across both trays.
- vLLM, `vllm/vllm-openai:v0.31.0-x86_64`. Unmodified, short single requests work. Under load a request stalls at 0 tokens per second, or the engine dies on an assertion and the pod restarts. Tensor parallelism starts and hits the same limits.

So SGLang is the one to reach for on the lab, and `vllm-serve.yaml` is there for trying the deployment, the API and the queueing rather than for load tests. The load generator behind those numbers sends requests of random length in batches of 64, 256 and 512 at 16, 32 and 64 concurrent, asks every seventh for prompt logprobs, and counts a request as complete only when it returns exactly its `max_tokens`.

## Why vLLM stalls or crashes

After each step vLLM's model runner tells the scheduler how many tokens each request produced: one, or zero while the request is still in the middle of its prompt. Model Runner V2, the default since v0.29, computes that count **on the GPU** and copies it back to the host.

No GPU kernel runs in the lab, so the count is whatever the memory held. A diagnostic probe in a live TP=4 pod caught it:

```text
PROBE pid=57 reqs=14 num_sampled=[1065353216, 0, 0, 0, 0, 0, 0, 0] ids=[[1065353216], [0], [0], [0]]
```

`1065353216` is `0x3F800000`, the bit pattern of float32 `1.0`, left behind in a recycled allocation. Thirteen of the fourteen requests were told they had produced zero tokens.

From there the two failures follow. A request told "zero tokens" makes no progress, which is the stall. And whether a request's prompt finished this step is decided **separately, on the host**, for prompt logprobs. When the host said "the prompt finished, here are its logprobs" and the device bytes said "no token", vLLM's scheduler hit the invariant that protects against exactly that combination:

```text
File ".../vllm/v1/core/sched/scheduler.py", line 2231, in update_from_output
    assert not prompt_logprobs_tensors
AssertionError
```

Tensor parallelism is not the cause of any of this. The same assertion appears on a single GPU, with prompt logprobs on a repeated prompt; adding ranks only made it easy to hit, because the first concurrent batch of 64 requests was enough to kill the engine.

{% <admonition type="note" title="This is not a bug in vLLM"> %}
On real hardware the kernel is correct and the count it returns is right. What the lab exposes is a design fragility: one fact, "did this request's prompt finish this step?", is derived twice, on two different devices, and an assertion couples them. It only bites when the device value is wrong. That does happen on real hardware too, though rarely: vLLM issue [#57562](https://github.com/vllm-project/vllm/issues/57562) reports the same class of failure on Confidential Computing machines, where a stale read through UVA makes the device value disagree with the host.
{% </admonition> %}

The history is worth a paragraph, because the round trip was not always there. Model Runner V2 landed in November 2025 ([#25266](https://github.com/vllm-project/vllm/pull/25266)) with the count computed on the host, in numpy, with no copy at all. Two days later [#29194](https://github.com/vllm-project/vllm/pull/29194) moved it onto the GPU, to lay groundwork for speculative decoding, where the accepted count genuinely is only known on the device. The same change put the prompt-logprobs decision on the host in its current form, and from that point the two decisions have lived on different devices. The older V1 runner never made the round trip for non-speculative requests.

## Why SGLang does not care

SGLang runs on the same emulated GPUs untouched, and reading its v0.5.21 source shows why. In normal decode its scheduler never asks the GPU how many tokens a request produced. It assumes one, reads only the token ids, and wraps each as a single token (`_normalize_decode_outputs` in `python/sglang/srt/managers/scheduler_components/batch_result_processor.py`). When a step ends inside a prompt, that is tracked on the CPU: the scheduler counts the partial chunks it has scheduled, and the result loop drops the token while that count is above zero. Only speculative decoding takes an accepted count from the device, as it has to.

So on the normal path SGLang reads nothing off the GPU but token ids, and those are valid, if meaningless, once memory reads as zeros. That is the whole difference. I should be clear that this comes from reading SGLang's source and matching it against the behaviour I saw, not from instrumenting SGLang itself.

## The one change vLLM needs

For a step with no draft tokens, the host already knows the count. The change is to use it: report the host value to the scheduler instead of copying the device tensor, and leave the device count driving the GPU-side bookkeeping and all of speculative decoding. It comes to about thirty lines across three files, with three times that again in tests.

On a real GPU it changes nothing, which I checked rather than assumed. Greedy decoding over 14 requests on an RTX 3060, with prompts up to 2005 tokens and `max_num_batched_tokens` set low enough to force chunked prefill, with both `logprobs` and `prompt_logprobs` requested, gives identical token ids, sample logprobs and prompt logprobs patched and unpatched: 672 generated tokens and 8110 prompt positions, the same on both.

The change is not upstream, so until it is, vLLM on the lab needs an image built with it. vLLM's own `docker/Dockerfile` with `VLLM_USE_PRECOMPILED=1` builds one from a patched checkout in under an hour, taking the compiled kernels from the published wheel of the same commit, and the result is imported into each tray's k3s with `k3s ctr -n k8s.io images import` and used with `imagePullPolicy: Never`.

With that image, vLLM matches SGLang: 832 of 832 requests at TP=4 in a tray, and 832 of 832 at TP=8 across both trays.

## What the GPUs return

Fixing the count is not enough on its own. The next load test failed in the API server instead, with an `OverflowError` in the tokenizer while detokenizing logprob token ids, which are also uncomputed GPU output. No change to vLLM can make those meaningful, because the numbers were never computed. That one belongs to the lab.

Fresh allocations from the emulated driver are already zeroed. Stale bytes come from PyTorch's caching allocator reusing blocks, and PyTorch has a switch for that: `PYTORCH_NO_CUDA_MEMORY_CACHING=1` makes every tensor a fresh driver allocation. With it, output that no kernel wrote reads as zeros, so token ids are 0 and logprobs are 0.0, which are valid and repeatable. Every answer becomes `"!!!..."`, token 0 repeated, with exactly `max_tokens` tokens and `finish_reason: length`.

Both pieces are needed, and I have the control experiment to prove it: the unpatched vLLM image with `PYTORCH_NO_CUDA_MEMORY_CACHING=1` hangs, because the count now reads a clean zero every step instead of stale garbage. The lab setting fixes the data and the vLLM change fixes the bookkeeping.

{% <admonition type="note" title="One side effect"> %}
PyTorch's CUDA tensor sharing between processes goes through the caching allocator's blocks, so `torch.multiprocessing` stops working with this setting. That is also true on a real GPU, and neither inference server needs it.
{% </admonition> %}

## What the emulated driver had to grow

Getting this far needed three additions to the fake CUDA driver, all of which are about communication setup rather than arithmetic.

The first is a hidden export table. FlashInfer's MNNVL setup failed with `cudaErrorInvalidValue`, and with debug logging on, the driver reported two unknown "dark API" tables requested through `cuGetExportTable`. One of them, `21318c60971432488ca641ff7324c8f2`, is requested only by the CUDA 13.4 runtime, and without it every runtime call failed, down to `cudaGetDeviceCount`. Asking the host's real driver for the table returned a header of `0x2f8` bytes, that is 95 slots. Serving 94 logging stubs showed the runtime never calls any of them: it only checks the table exists. The emulated driver now serves it with no-op slots.

The second is shared GPU memory. `cuMemCreate` now makes an allocation backed by a memfd, or by a file on the lab's `/shared` for fabric handles, so it is reachable from either tray. Exported POSIX file descriptors are real descriptors, which is what FlashInfer passes over a Unix socket. Fabric memory and multicast objects follow NVLink partitions the way IMEX does on GB200: an import into a GPU in a different partition is refused with `CUDA_ERROR_NOT_PERMITTED`, and a GPU in no partition has none.

The third is legacy CUDA IPC, which vLLM's custom all-reduce self-tests before enabling itself. Device allocations are now page-aligned, and on the first `cuIpcGetMemHandle` the allocation moves in place onto a file under `/var/lib/fakegpu/ipc` on that tray, so any process on the tray can open the handle from any container, while the other tray correctly gets `CUDA_ERROR_INVALID_HANDLE`. I checked the behaviour I was emulating against a real driver first, with two Podman containers on an RTX 3060: CUDA IPC works between completely separate containers, with private IPC and PID namespaces, so the driver only cares that both are on the same node with access to the same GPU.

With all three, vLLM stops falling back. It reports `Initialized FlashInfer Allreduce norm fusion workspace with backend=mnnvl` and `Using ['FLASHINFER', 'CUSTOM', 'PYNCCL'] all-reduce backends`, without `--disable-custom-all-reduce`. SGLang, inside a tray, runs its all-reduce over symmetric memory shared between the GPUs, logging `All Reduce config: symmetric_memory = 40.01 MB`.

## Tensor parallelism across the trays

TP=8 spans both trays as two pods of four GPUs, a JobSet with rank 0 serving the API. Which fabric the all-reduces cross depends on the NVLink partitioning from [Part 5](@/ai-lab/05-nvlink/index.md), and the traffic counters show it. With 256 requests per run:

- Both trays in one NVLink partition, which is the default. SGLang moves 320 GB over NVLink and nothing over InfiniBand; vLLM moves 407 GB over NVLink and nothing over InfiniBand.
- Trays in separate partitions. SGLang moves 262 GB over NVLink and 23 GB over InfiniBand per tray; vLLM moves 279 GB over NVLink, and 26 GB and 22 GB over InfiniBand on the two trays.

That is the behaviour to expect on a real GB200, where NCCL crosses NVLink inside a domain and the scale-out fabric between domains. Kueue enforces it from the other side: with the normal `podset-required-topology` annotation and the trays split, the job never starts, and Kueue says so plainly.

```text
couldn't assign flavors to pod set node: topology "nvl" allows to fit only 1 out of 2 pod(s)
```

Switching to `podset-preferred-topology` is what lets the job span partitions and produce the second row above.

{% <admonition type="note" title="Not all of it shows in the metrics"> %}
All-reduces that run as a server's own kernels, which means SGLang's symmetric-memory all-reduce and vLLM's custom and FlashInfer paths, do not go through the emulated NCCL, so their NVLink traffic is not counted. NCCL traffic is. The numbers above are comparable with each other, not a complete accounting of every byte the servers moved.
{% </admonition> %}

## What you need to run it

- **Bigger trays.** The default k3s trays of 4 CPU and 10 GiB fit a one-GPU server. Tensor parallelism runs a server process per GPU plus the API server, and the TP=4 and TP=8 examples ask for 4 CPU and 14 GiB per pod. Set `workers: { cpu: 8, memory: 24GiB }` under `instance_limits_k3s` in `local.yml`, then `make down && make up`, which keeps the volumes.
- **A model whose heads divide.** The tensor-parallel size has to divide the model's attention head count. Qwen2.5-0.5B has 14, so TP 1, 2 and 7; Qwen2.5-1.5B has 12, so TP 1, 2 and 4; Qwen3-0.6B has 16, so TP 1, 2, 4 and 8.
- **Patience on the first start.** The images are about 14 GB for SGLang and 9 GB for vLLM per tray, and the model downloads to `~/.cache/huggingface` on `/shared`.
- **`--enforce-eager` for vLLM.** There is nothing to gain from CUDA graphs or `torch.compile` when no kernels run.

## Where the answers stop being meaningful

This is the part to keep in mind before trusting anything above. The lab validates control flow, scheduling, communication setup and topology behaviour. It says nothing about numerical results, and several features cannot work at all:

- Answer quality, accuracy and numerical behaviour. Every token is id 0.
- Speculative decoding, which is untested and not expected to work: how many draft tokens get accepted depends on model outputs that are never computed.
- Structured output with grammars or JSON schemas, untested: a grammar may reject token 0 and end the request with an error.
- Mixture-of-experts models with expert parallelism, untested: the routing decisions come from the GPU.

## What to build on this

- **Run your own serving stack against it.** If your deployment, autoscaler or gateway talks to an OpenAI-compatible endpoint, the lab gives you eight GPUs' worth of topology to test it against without a rack.
- **Test the placement, not the tokens.** The interesting failures here were about where pods land and which fabric they talk over. Change the partitioning under a running TP=8 job and watch what Kueue and the counters do.
- **Check your own code for facts derived twice.** The vLLM finding generalises: anywhere a value is computed on the device and the same value is inferred on the host, an emulator, a simulator or an unusual memory architecture will find the seam.

## Next in the series

[Part 10: NVIDIA Run:ai](@/ai-lab/10-runai/index.md) runs NVIDIA's newly open-sourced orchestration platform on the lab, including its driver-enforced GPU fractions, which meant finding out how little of an NVIDIA GPU stack it actually checks for.
