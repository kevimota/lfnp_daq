<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/state';
  import { Play, Square, Pause, PlayIcon, ArrowLeft } from '@lucide/svelte';
  import api from '$lib/api';
  import { toast } from '$lib/toast';
  import { goto } from '$app/navigation';
  import { env } from '$env/dynamic/public';

  interface ScanRun {
    id: number;
    configuration_id: number;
    status: string;
    label: string | null;
    comments: string | null;
    started_at: string | null;
    data_path: string | null;
    created_at: string;
  }

  interface DAQConfig {
    id: number;
    voltage_points: { slot: number; channel: number; voltage: number }[][];
    wait_time_seconds: number;
    sample_interval_seconds: number;
    number_of_samples: number;
    end_voltage: number;
    power_supply: number | null;
    created_at: string;
  }

  interface DAQStatus {
    state: string;
    hv_point: string;
    run_id: string;
  }

  interface PowerSupply {
    id: number;
    name: string;
  }

  let run: ScanRun | null = $state(null);
  let config: DAQConfig | null = $state(null);
  let daqStatus: DAQStatus | null = $state(null);
  let logContent = $state('');
  let supplies: PowerSupply[] = $state([]);
  let activeTab = $state('monitor');
  let configJson = $state('');
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let ws: WebSocket | null = $state(null);
  let wsConnected = $state(false);
  let liveData: any[] = $state([]);
  let updating = $state(false);
  let runFiles: { name: string; size: number }[] = $state([]);

  const runId = $derived(Number(page.params.id));

  onMount(async () => {
    await Promise.all([fetchRun(), fetchSupplies()]);
  });

  onDestroy(() => {
    stopPolling();
    disconnectWs();
  });

  async function fetchRun() {
    try {
      const { data } = await api.get(`/daq/runs/${runId}`);
      run = data;
      await Promise.all([fetchConfig(run.configuration_id), fetchFiles()]);
      if (run.status === 'running') {
        startPolling();
        connectWs();
      }
    } catch {
      run = null;
    }
  }

  async function fetchConfig(configId: number) {
    try {
      const { data } = await api.get(`/daq/configs/${configId}`);
      config = data;
      configJson = JSON.stringify(data.voltage_points, null, 2);
    } catch {}
  }

  async function fetchSupplies() {
    try {
      const { data } = await api.get('/hardware/caen-ps');
      supplies = data;
    } catch {}
  }

  async function fetchStatus() {
    try {
      const { data } = await api.get(`/daq/runs/${runId}/status`);
      daqStatus = data;
    } catch {
      daqStatus = null;
    }
  }

  async function fetchFiles() {
    try {
      const { data } = await api.get(`/daq/runs/${runId}/files`);
      runFiles = data.files || [];
    } catch {
      runFiles = [];
    }
  }

  async function fetchLog() {
    try {
      const { data } = await api.get(`/daq/runs/${runId}/log`, { responseType: 'text' });
      logContent = data || '';
    } catch {
      logContent = '';
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(async () => {
      await Promise.all([fetchStatus(), fetchLog(), fetchFiles()]);
      if (run) {
        const { data } = await api.get(`/daq/runs/${runId}`);
        run = { ...run, status: data.status };
      }
    }, 2000);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function connectWs() {
    disconnectWs();
    const daqUrl = env.PUBLIC_DAQ_URL || 'http://localhost:8001';
    const wsUrl = daqUrl.replace(/^http/, 'ws') + `/ws/${runId}`;
    ws = new WebSocket(wsUrl);
    ws.onopen = () => { wsConnected = true; };
    ws.onclose = () => { wsConnected = false; };
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'current_scan') {
          liveData = [...liveData.slice(-100), msg];
        }
      } catch {}
    };
    ws.onerror = () => { wsConnected = false; };
  }

  function disconnectWs() {
    if (ws) {
      ws.close();
      ws = null;
    }
    wsConnected = false;
  }

  async function controlRun(action: 'start' | 'stop' | 'pause' | 'resume') {
    try {
      await api.post(`/daq/runs/${runId}/${action}`);
      toast.success(`${action.charAt(0).toUpperCase() + action.slice(1)} successful`);
      await fetchRun();
      if (action === 'start') { startPolling(); connectWs(); }
      if (action === 'stop') { stopPolling(); disconnectWs(); daqStatus = null; }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Failed to ${action}`);
    }
  }

  function statusBadge(status: string) {
    const map: Record<string, string> = {
      created: 'badge-ghost', running: 'badge-info', paused: 'badge-warning',
      finished: 'badge-success', stopped: 'badge-neutral', failed: 'badge-error',
    };
    return map[status] || 'badge-ghost';
  }

  function fsmBadge(state: string) {
    const map: Record<string, string> = {
      halted: 'badge-neutral', configuring: 'badge-info', waiting: 'badge-warning',
      recording: 'badge-success', paused: 'badge-warning', fisished: 'badge-success',
      failed: 'badge-error',
    };
    return map[state] || 'badge-ghost';
  }

  function canControl(state: string | undefined, action: string) {
    if (!state) return action === 'start';
    switch (action) {
      case 'start': return state === 'halted';
      case 'stop': return ['configuring', 'waiting', 'recording', 'paused'].includes(state);
      case 'pause': return ['configuring', 'waiting', 'recording'].includes(state);
      case 'resume': return state === 'paused';
      default: return false;
    }
  }

  async function downloadAllFiles() {
    try {
      const resp = await api.get(`/daq/runs/${runId}/download`, { responseType: 'blob' });
      const url = URL.createObjectURL(resp.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = `run_${runId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error('Failed to download archive');
    }
  }

  async function downloadFile(file: { name: string }) {
    try {
      const resp = await api.get(`/daq/runs/${runId}/files/${file.name}`, { responseType: 'blob' });
      const url = URL.createObjectURL(resp.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.name;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error('Failed to download file');
    }
  }

  async function updateConfig() {
    if (!config) return;
    updating = true;
    try {
      let voltage_points;
      try { voltage_points = JSON.parse(configJson); } catch { voltage_points = config.voltage_points; }
      const body = {
        voltage_points,
        wait_time_seconds: config.wait_time_seconds,
        sample_interval_seconds: config.sample_interval_seconds,
        number_of_samples: config.number_of_samples,
        end_voltage: config.end_voltage,
        power_supply: config.power_supply,
      };
      const { data } = await api.put(`/daq/configs/${config.id}`, body);
      toast.success('Configuration saved');
      config = data;
      configJson = JSON.stringify(data.voltage_points, null, 2);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to update configuration');
    } finally {
      updating = false;
    }
  }
</script>

<div class="flex items-center gap-4 mb-4">
  <button class="btn btn-ghost btn-sm" onclick={() => goto('/scans')}><ArrowLeft class="size-5" /></button>
  <h1 class="uppercase text-4xl p-2 bg-neutral">
    <strong>Run {runId}</strong>
  </h1>
  {#if run?.label}
    <span class="text-xl text-base-content/70">— {run.label}</span>
  {/if}
</div>

{#if !run}
  <div class="text-center py-12 text-base-content/50">Run not found.</div>
{:else}
  <div role="tablist" class="tabs tabs-bordered mb-4">
    <button role="tab" class="tab text-lg font-semibold" class:tab-active={activeTab === 'monitor'} onclick={() => activeTab = 'monitor'}>Monitor</button>
    <button role="tab" class="tab text-lg font-semibold" class:tab-active={activeTab === 'edit'} onclick={() => activeTab = 'edit'}>Edit</button>
    <button role="tab" class="tab text-lg font-semibold" class:tab-active={activeTab === 'download'} onclick={() => activeTab = 'download'}>Download</button>
  </div>

  {#if activeTab === 'monitor'}
    <div class="flex flex-col gap-4">
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div class="stat bg-base-200 rounded-box p-4">
          <div class="stat-title">Database Status</div>
          <div class="stat-value"><span class="badge {statusBadge(run.status)} text-lg py-3 px-4">{run.status}</span></div>
        </div>
        <div class="stat bg-base-200 rounded-box p-4">
          <div class="stat-title">FSM State</div>
          <div class="stat-value">
            {#if daqStatus}
              <span class="badge {fsmBadge(daqStatus.state)} text-lg py-3 px-4">{daqStatus.state}</span>
            {:else}
              <span class="text-base-content/50">—</span>
            {/if}
          </div>
        </div>
        <div class="stat bg-base-200 rounded-box p-4">
          <div class="stat-title">HV Point</div>
          <div class="stat-value text-2xl">{daqStatus?.hv_point || '—'}</div>
        </div>
        <div class="stat bg-base-200 rounded-box p-4">
          <div class="stat-title">WebSocket</div>
          <div class="stat-value text-lg">
            {#if wsConnected}
              <span class="badge badge-success text-lg py-3 px-4">Connected</span>
            {:else}
              <span class="badge badge-ghost text-lg py-3 px-4">Disconnected</span>
            {/if}
          </div>
        </div>
      </div>

      <!-- FSM Controls -->
      <div class="card bg-base-200">
        <div class="card-body">
          <h3 class="card-title">FSM Controls</h3>
          <div class="flex gap-3 flex-wrap">
            <button class="btn btn-info" disabled={!canControl(daqStatus?.state, 'start')} onclick={() => controlRun('start')}>
              <Play class="size-5" /> Start
            </button>
            <button class="btn btn-error" disabled={!canControl(daqStatus?.state, 'stop')} onclick={() => controlRun('stop')}>
              <Square class="size-5" /> Stop
            </button>
            <button class="btn btn-warning" disabled={!canControl(daqStatus?.state, 'pause')} onclick={() => controlRun('pause')}>
              <Pause class="size-5" /> Pause
            </button>
            <button class="btn btn-success" disabled={!canControl(daqStatus?.state, 'resume')} onclick={() => controlRun('resume')}>
              <PlayIcon class="size-5" /> Resume
            </button>
          </div>
        </div>
      </div>

      <div class="card bg-base-200">
        <div class="card-body">
          <h3 class="card-title">Log</h3>
          <pre class="text-xs bg-base-300 p-3 rounded-box overflow-y-auto max-h-96 font-mono whitespace-pre-wrap">{logContent || '(no log entries yet)'}</pre>
        </div>
      </div>

      {#if liveData.length > 0}
        <div class="card bg-base-200">
          <div class="card-body">
            <h3 class="card-title">Live Data (last {liveData.length} samples)</h3>
            <pre class="text-xs bg-base-300 p-3 rounded-box overflow-y-auto max-h-48 font-mono">{JSON.stringify(liveData.slice(-10), null, 2)}</pre>
          </div>
        </div>
      {/if}
    </div>

  {:else if activeTab === 'edit'}
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title">Configuration</h3>
        {#if config}
          <div class="flex flex-col gap-4">
            <div class="grid grid-cols-2 gap-4">
              <label class="form-control flex flex-col">
                <span class="label-text">Wait Time (s)</span>
                <input type="number" class="input input-bordered" bind:value={config.wait_time_seconds} />
              </label>
              <label class="form-control flex flex-col">
                <span class="label-text">Sample Interval (s)</span>
                <input type="number" step="0.1" class="input input-bordered" bind:value={config.sample_interval_seconds} />
              </label>
              <label class="form-control flex flex-col">
                <span class="label-text">Samples per Point</span>
                <input type="number" class="input input-bordered" bind:value={config.number_of_samples} />
              </label>
              <label class="form-control flex flex-col">
                <span class="label-text">End Voltage (V)</span>
                <input type="number" class="input input-bordered" bind:value={config.end_voltage} />
              </label>
              <label class="form-control flex flex-col">
                <span class="label-text">Power Supply</span>
                <select class="select select-bordered" bind:value={config.power_supply}>
                  {#each supplies as ps}
                    <option value={ps.id}>{ps.name}</option>
                  {/each}
                </select>
              </label>
            </div>

            <div>
              <span class="font-bold text-sm">Voltage Points (JSON)</span>
              <textarea class="textarea textarea-bordered font-mono text-sm w-full min-h-[200px] mt-1" bind:value={configJson}></textarea>
            </div>

            <button class="btn btn-primary self-end" onclick={updateConfig} disabled={updating}>
              {updating ? 'Saving...' : 'Save Configuration'}
            </button>
          </div>
        {:else}
          <p class="text-base-content/50">No configuration data available.</p>
        {/if}
      </div>
    </div>

  {:else if activeTab === 'download'}
    <div class="flex flex-col gap-4">
      <div class="card bg-base-200">
        <div class="card-body">
          <div class="flex items-center justify-between">
            <h3 class="card-title">Run Files</h3>
            <button class="btn btn-primary" onclick={downloadAllFiles}>
              Download All (.zip)
            </button>
          </div>
          {#if runFiles.length > 0}
            <div class="overflow-x-auto">
              <table class="table">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Size</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {#each runFiles as file}
                    <tr>
                      <td class="font-mono text-sm">{file.name}</td>
                      <td>{file.size > 1024 ? (file.size / 1024).toFixed(1) + ' KB' : file.size + ' B'}</td>
                      <td>
                        <button class="btn btn-ghost btn-sm" onclick={() => downloadFile(file)}>Download</button>
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {:else}
            <p class="text-base-content/50 py-4">No files available yet.</p>
          {/if}
        </div>
      </div>
    </div>
  {/if}
{/if}
