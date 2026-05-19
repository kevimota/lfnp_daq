<script lang="ts">
  import { onMount } from 'svelte';
  import { Plus, Play, Square, RotateCcw, Code, List, Trash2, Search, ArrowLeft } from '@lucide/svelte';
  import api from '$lib/api';
  import { toast } from '$lib/toast';
  import { goto } from '$app/navigation';

  interface ScanRun {
    id: number;
    configuration_id: number;
    status: string;
    data_path: string | null;
    label: string | null;
    comments: string | null;
    started_at: string | null;
    stopped_at: string | null;
    created_at: string;
  }

  interface PowerSupply {
    id: number;
    name: string;
  }

  interface ChannelConfig {
    slot: number;
    channel: number;
    voltage: number;
  }

  let runs: ScanRun[] = $state([]);
  let supplies: PowerSupply[] = $state([]);
  let showNewModal = $state(false);
  let useJsonEditor = $state(false);

  let newScan = $state({
    label: '',
    comments: '',
    wait_time_seconds: 30,
    sample_interval_seconds: 1,
    number_of_samples: 60,
    end_voltage: 0,
    power_supply: 1,
    voltage_points: [[{ slot: 0, channel: 0, voltage: 100 }] as ChannelConfig[]],
  });

  let jsonConfig = $state('');

  let creating = $state(false);
  let currentPage = $state(1);
  let totalPages = $state(1);
  let searchQuery = $state('');
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  onMount(async () => {
    await Promise.all([fetchRuns(), fetchSupplies()]);
  });

  async function fetchRuns() {
    try {
      const params: Record<string, string | number> = { page: currentPage, per_page: 20 };
      if (searchQuery) params.search = searchQuery;
      const { data } = await api.get('/daq/runs', { params });
      runs = data.items;
      totalPages = data.pages;
    } catch {}
  }

  function onSearchInput(value: string) {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchQuery = value;
      currentPage = 1;
      fetchRuns();
    }, 300);
  }

  async function fetchSupplies() {
    try {
      const { data } = await api.get('/hardware/caen-ps');
      supplies = data;
    } catch {}
  }

  function addPoint() {
    const last = newScan.voltage_points[newScan.voltage_points.length - 1];
    const copy = last ? last.map(ch => ({ ...ch })) : [{ slot: 0, channel: 0, voltage: 100 }];
    newScan.voltage_points = [...newScan.voltage_points, copy];
  }

  function removePoint(index: number) {
    newScan.voltage_points = newScan.voltage_points.filter((_, i) => i !== index);
  }

  function addChannel(pointIndex: number) {
    const updated = [...newScan.voltage_points];
    updated[pointIndex] = [...updated[pointIndex], { slot: 0, channel: 0, voltage: 100 }];
    newScan.voltage_points = updated;
  }

  function removeChannel(pointIndex: number, chIndex: number) {
    const updated = [...newScan.voltage_points];
    updated[pointIndex] = updated[pointIndex].filter((_, i) => i !== chIndex);
    newScan.voltage_points = updated;
  }

  async function loadConfigFromRun(runId: number) {
    if (!runId) return;
    try {
      const { data: run } = await api.get(`/daq/runs/${runId}`);
      const { data: config } = await api.get(`/daq/configs/${run.configuration_id}`);
      newScan.wait_time_seconds = config.wait_time_seconds;
      newScan.sample_interval_seconds = config.sample_interval_seconds;
      newScan.number_of_samples = config.number_of_samples;
      newScan.end_voltage = config.end_voltage;
      newScan.power_supply = config.power_supply;
      newScan.voltage_points = config.voltage_points;
      syncJson();
    } catch {}
  }

  function syncJson() {
    jsonConfig = JSON.stringify(
      {
        voltage_points: newScan.voltage_points,
        wait_time_seconds: newScan.wait_time_seconds,
        sample_interval_seconds: newScan.sample_interval_seconds,
        number_of_samples: newScan.number_of_samples,
        end_voltage: newScan.end_voltage,
        power_supply: newScan.power_supply,
        label: newScan.label || undefined,
        comments: newScan.comments || undefined,
      },
      null,
      2,
    );
  }

  function applyJson() {
    try {
      const parsed = JSON.parse(jsonConfig);
      newScan.voltage_points = parsed.voltage_points ?? newScan.voltage_points;
      newScan.wait_time_seconds = parsed.wait_time_seconds ?? newScan.wait_time_seconds;
      newScan.sample_interval_seconds = parsed.sample_interval_seconds ?? newScan.sample_interval_seconds;
      newScan.number_of_samples = parsed.number_of_samples ?? newScan.number_of_samples;
      newScan.end_voltage = parsed.end_voltage ?? newScan.end_voltage;
      newScan.power_supply = parsed.power_supply ?? newScan.power_supply;
      newScan.label = parsed.label ?? newScan.label;
      newScan.comments = parsed.comments ?? newScan.comments;
    } catch {
      toast.error('Invalid JSON');
    }
  }

  async function createScan() {
    creating = true;
    try {
      await api.post('/daq/runs', newScan);
      showNewModal = false;
      await fetchRuns();
      resetForm();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to create scan');
    } finally {
      creating = false;
    }
  }

  function resetForm() {
    newScan = {
      label: '',
      comments: '',
      wait_time_seconds: 30,
      sample_interval_seconds: 1,
      number_of_samples: 60,
      end_voltage: 0,
      power_supply: supplies[0]?.id || 1,
      voltage_points: [[{ slot: 0, channel: 0, voltage: 100 }]],
    };
    jsonConfig = '';
    useJsonEditor = false;
  }

  function resetList() {
    searchQuery = '';
    currentPage = 1;
    fetchRuns();
  }

  function openModal() {
    showNewModal = true;
    if (supplies.length > 0) {
      newScan.power_supply = supplies[0].id;
    }
    syncJson();
  }

  async function startRun(run: ScanRun) {
    try {
      await api.post(`/daq/runs/${run.id}/start`);
      run.status = 'running';
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to start scan');
    }
  }

  async function stopRun(run: ScanRun) {
    try {
      await api.post(`/daq/runs/${run.id}/stop`);
      run.status = 'stopped';
    } catch {}
  }

  async function deleteRun(run: ScanRun) {
    if (!confirm(`Delete scan #${run.id}${run.label ? ` (${run.label})` : ''}? This cannot be undone.`)) return;
    try {
      await api.delete(`/daq/runs/${run.id}`);
      toast.success(`Scan #${run.id} deleted`);
      runs = runs.filter(r => r.id !== run.id);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to delete scan');
    }
  }

  function statusBadge(status: string) {
    const map: Record<string, string> = {
      created: 'badge-ghost',
      running: 'badge-info',
      paused: 'badge-warning',
      finished: 'badge-success',
      stopped: 'badge-neutral',
      failed: 'badge-error',
    };
    return map[status] || 'badge-ghost';
  }
</script>

<div class="flex items-center justify-between mb-4">
  <div class='flex items-center justify-between'>
    <button class="btn btn-ghost btn-sm" onclick={() => goto('/')}><ArrowLeft class="size-5" /></button>
    <h1 class="uppercase text-4xl p-2 bg-neutral">  
      <strong>Scans</strong>
    </h1>
  </div>
  
  <button class="btn btn-primary" onclick={openModal}>
    <Plus class="size-5" /> New Scan
  </button>
</div>

<label class="input input-bordered flex items-center gap-2 mb-4">
  <Search class="size-4" />
  <input type="text" class="grow" placeholder="Search by label…"
    oninput={(e) => onSearchInput(e.target.value)} />
  {#if searchQuery}
    <button class="btn btn-ghost btn-xs" onclick={resetList}>✕</button>
  {/if}
</label>

<div class="overflow-x-auto">
  <table class="table">
    <thead>
      <tr>
        <th>ID</th>
        <th>Label</th>
        <th>Status</th>
        <th>Created</th>
        <th>Started</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {#each runs as run}
        <tr class="cursor-pointer hover:bg-base-300" onclick={() => goto('/scans/' + run.id)}>
          <td>{run.id}</td>
          <td>{run.label || '-'}</td>
          <td><span class="badge {statusBadge(run.status)}">{run.status}</span></td>
          <td>{run.created_at ? new Date(run.created_at).toLocaleString() : '-'}</td>
          <td>{run.started_at ? new Date(run.started_at).toLocaleString() : '-'}</td>
          <td class="flex gap-1" onclick={(e) => e.stopPropagation()}>
            {#if run.status === 'created'}
              <button class="btn btn-soft btn-info btn-sm" onclick={() => startRun(run)}>
                <Play class="size-4" />
              </button>
            {:else if run.status === 'running'}
              <button class="btn btn-soft btn-error btn-sm" onclick={() => stopRun(run)}>
                <Square class="size-4" />
              </button>
            {:else if run.status === 'finished' || run.status === 'failed' || run.status === 'stopped'}
              <button class="btn btn-soft btn-neutral btn-sm" onclick={() => startRun(run)}>
                <RotateCcw class="size-4" />
              </button>
            {/if}
            <button class="btn btn-ghost btn-sm text-error" onclick={() => deleteRun(run)}>
              <Trash2 class="size-4" />
            </button>
          </td>
        </tr>
      {:else}
        <tr>
          <td colspan="6" class="text-center py-8 text-base-content/50">No scans yet.</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<div class="flex justify-center mt-4">
  <div class="join">
    <button class="join-item btn btn-sm" disabled={currentPage <= 1}
      onclick={() => { currentPage--; fetchRuns(); }}>«</button>
    {#each Array(totalPages) as _, i}
      <button class="join-item btn btn-sm" class:btn-active={currentPage === i + 1}
        onclick={() => { currentPage = i + 1; fetchRuns(); }}>{i + 1}</button>
    {/each}
    <button class="join-item btn btn-sm" disabled={currentPage >= totalPages}
      onclick={() => { currentPage++; fetchRuns(); }}>»</button>
  </div>
</div>

{#if showNewModal}
  <div class="modal modal-open">
    <div class="modal-box max-w-3xl max-h-[85vh] overflow-y-auto">
      <h3 class="text-2xl font-bold mb-4">New Scan</h3>

      <div class="flex flex-col gap-4">
        <!-- Load config from existing run -->
        <div class="flex items-end gap-2">
          <label class="form-control flex-1">
            <span class="label-text">Load config from existing run</span>
            <select class="select select-bordered" onchange={(e) => loadConfigFromRun(Number(e.target.value))}>
              <option value="">— Select a run —</option>
              {#each runs as run}
                <option value={run.id}>Run {run.id}{run.label ? ` — ${run.label}` : ''}</option>
              {/each}
            </select>
          </label>
          <button class="btn btn-ghost btn-sm" onclick={() => { useJsonEditor = !useJsonEditor; if (useJsonEditor) syncJson(); }}>
            <Code class="size-4" /> {useJsonEditor ? 'Form' : 'JSON'}
          </button>
        </div>

        <div class="divider my-1"></div>

        {#if useJsonEditor}
          <label class="form-control">
            <span class="label-text">Raw Configuration (JSON)</span>
            <textarea class="textarea textarea-bordered font-mono text-sm min-h-[400px] w-full" bind:value={jsonConfig}></textarea>
          </label>
          <button class="btn btn-sm btn-outline self-end" onclick={applyJson}>Apply JSON to Form</button>
        {:else}
          <div class="grid grid-cols-2 gap-4">
            <label class="form-control">
              <span class="label-text">Label</span>
              <input type="text" class="input input-bordered" bind:value={newScan.label} />
            </label>
            <label class="form-control">
              <span class="label-text">Comments</span>
              <textarea class="textarea textarea-bordered" bind:value={newScan.comments}></textarea>
            </label>
          </div>

          <div class="grid grid-cols-2 gap-4">
            <label class="form-control">
              <span class="label-text">Wait Time (s)</span>
              <input type="number" class="input input-bordered" bind:value={newScan.wait_time_seconds} />
            </label>
            <label class="form-control">
              <span class="label-text">Sample Interval (s)</span>
              <input type="number" step="0.1" class="input input-bordered" bind:value={newScan.sample_interval_seconds} />
            </label>
            <label class="form-control">
              <span class="label-text">Samples per Point</span>
              <input type="number" class="input input-bordered" bind:value={newScan.number_of_samples} />
            </label>
            <label class="form-control">
              <span class="label-text">End Voltage (V)</span>
              <input type="number" class="input input-bordered" bind:value={newScan.end_voltage} />
            </label>
            <label class="form-control">
              <span class="label-text">Power Supply</span>
              <select class="select select-bordered" bind:value={newScan.power_supply}>
                {#each supplies as ps}
                  <option value={ps.id}>{ps.name}</option>
                {/each}
              </select>
            </label>
          </div>

          <div class="divider">Voltage Points</div>

          {#each newScan.voltage_points as point, pi}
            <div class="card bg-base-300">
              <div class="card-body p-4">
                <div class="flex items-center justify-between">
                  <span class="font-bold">Point {pi + 1}</span>
                  <button class="btn btn-ghost btn-xs text-error" onclick={() => removePoint(pi)}>Remove</button>
                </div>

                <div class="grid grid-cols-3 gap-2 font-bold text-sm">
                  <span>Slot</span>
                  <span>Channel</span>
                  <span>Voltage (V)</span>
                </div>

                {#each point as ch, ci}
                  <div class="grid grid-cols-3 gap-2 items-center">
                    <input type="number" class="input input-bordered input-sm" bind:value={ch.slot} />
                    <input type="number" class="input input-bordered input-sm" bind:value={ch.channel} />
                    <div class="flex gap-1 items-center">
                      <input type="number" step="1" class="input input-bordered input-sm flex-1" bind:value={ch.voltage} />
                      <button class="btn btn-ghost btn-xs text-error" onclick={() => removeChannel(pi, ci)}>×</button>
                    </div>
                  </div>
                {/each}

                <button class="btn btn-ghost btn-xs" onclick={() => addChannel(pi)}>+ Add Channel</button>
              </div>
            </div>
          {/each}

          <button class="btn btn-outline btn-sm" onclick={addPoint}>+ Add Point</button>
        {/if}
      </div>

      <div class="modal-action">
        <button class="btn" onclick={() => { showNewModal = false; resetForm(); }}>Cancel</button>
        <button class="btn btn-primary" onclick={createScan} disabled={creating}>
          {creating ? 'Creating...' : 'Create Scan'}
        </button>
      </div>
    </div>
    <div class="modal-backdrop" onclick={() => { showNewModal = false; resetForm(); }}></div>
  </div>
{/if}
