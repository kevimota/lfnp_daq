<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/state';
  import { Play, Square, Pause, PlayIcon, ArrowLeft, Trash2, Code } from '@lucide/svelte';
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
    type: string;
    voltage_points: { slot: number; channel: number; voltage: number }[][];
    wait_time_seconds: number;
    sample_interval_seconds: number;
    number_of_samples: number;
    end_voltage: number;
    power_supply: number | null;
    digitizer_id: number | null;
    trigger_mode: 'random' | 'external' | null;
    trigger_frequency_hz: number | null;
    sampling_frequency_hz: number | null;
    number_of_triggers: number | null;
    record_length: number | null;
    post_trigger_size: number | null;
    input_range_vpp: number | null;
    channels: DigitizerChannel[] | null;
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

  interface CaenDigitizer {
    id: number;
    name: string;
    board_model: number;
    connection_type: number;
    arg: string;
    conet_node: number;
    vme_base_address: number;
    comment: string;
  }

  interface DigitizerChannel {
    channel: number;
    enabled: boolean;
  }

  let run: ScanRun | null = $state(null);
  let config: DAQConfig | null = $state(null);
  let daqStatus: DAQStatus | null = $state(null);
  let logContent = $state('');
  let supplies: PowerSupply[] = $state([]);
  let digitizers: CaenDigitizer[] = $state([]);
  let activeTab = $state('monitor');
  let configJson = $state('');
  let useJsonEditor = $state(false);
  let editJson = $state('');
  let editScanType = $state<'hv_scan' | 'digitizer_scan'>('hv_scan');
  let editDigitizerId = $state<number | null>(null);
  let editTriggerMode = $state<'random' | 'external'>('random');
  let editTriggerFrequencyHz = $state<number>(1);
  let editSamplingFrequencyHz = $state<number | null>(null);
  let editNumberOfTriggers = $state<number>(100);
  let editRecordLength = $state<number>(1024);
  let editPostTriggerSize = $state<number>(50);
  let editInputRangeVpp = $state<number | null>(null);
  let editChannels = $state<DigitizerChannel[]>([]);
  let editVoltagePoints = $state<{ slot: number; channel: number; voltage: number }[][]>([]);
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let ws: WebSocket | null = $state(null);
  let wsConnected = $state(false);
  let liveData: any[] = $state([]);
  let updating = $state(false);
  let runFiles: { name: string; size: number }[] = $state([]);
  let editLabel = $state('');
  let editComments = $state('');
  let updatingRun = $state(false);

  const runId = $derived(Number(page.params.id));

  onMount(async () => {
    await Promise.all([fetchRun(), fetchSupplies(), fetchDigitizers()]);
    await fetchLog();
  });

  onDestroy(() => {
    stopPolling();
    disconnectWs();
  });

  async function fetchRun() {
    try {
      const { data } = await api.get(`/daq/runs/${runId}`);
      run = data;
      editLabel = data.label ?? '';
      editComments = data.comments ?? '';
      await Promise.all([fetchConfig(data.configuration_id), fetchFiles()]);
      if (data.status === 'running') {
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
      editScanType = data.type || 'hv_scan';
      editVoltagePoints = JSON.parse(JSON.stringify(data.voltage_points));
      editDigitizerId = data.digitizer_id ?? null;
      editTriggerMode = data.trigger_mode ?? 'random';
      editTriggerFrequencyHz = data.trigger_frequency_hz ?? 1;
      editSamplingFrequencyHz = data.sampling_frequency_hz ?? null;
      editNumberOfTriggers = data.number_of_triggers ?? 100;
      editRecordLength = data.record_length ?? 1024;
      editPostTriggerSize = data.post_trigger_size ?? 50;
      editInputRangeVpp = data.input_range_vpp ?? null;
      editChannels = data.channels?.length
        ? JSON.parse(JSON.stringify(data.channels))
        : (data.type === 'digitizer_scan' ? defaultChannels() : []);
      configJson = JSON.stringify(data.voltage_points, null, 2);
    } catch {}
  }

  async function fetchSupplies() {
    try {
      const { data } = await api.get('/hardware/caen-ps');
      supplies = data;
    } catch {}
  }

  async function fetchDigitizers() {
    try {
      const { data } = await api.get('/hardware/caen-digitizers');
      digitizers = data;
    } catch {}
  }

  function channelCountForBoard(board_model: number): number {
    if (board_model === 18) return 16; // DT5742: 2 groups x 8 channels
    if (board_model === 27) return 8; // DT5743: 4 groups x 2 channels
    return 8;
  }

  // Valid sampling frequencies per board model (DT5742 DRS4: 5/2.5/1/0.75 GS/s,
  // DT5743 SAMLONG: 3.2/1.6/0.8/0.4 GS/s).
  const SAMPLING_FREQUENCIES: Record<number, number[]> = {
    18: [5_000_000_000, 2_500_000_000, 1_000_000_000, 750_000_000],
    27: [3_200_000_000, 1_600_000_000, 800_000_000, 400_000_000],
  };
  const DEFAULT_SAMPLING: Record<number, number> = { 18: 5_000_000_000, 27: 3_200_000_000 };

  function digitizerBoardModel(id: number | null): number {
    return digitizers.find(d => d.id === id)?.board_model ?? 0;
  }

  function samplingOptions(boardModel: number): number[] {
    return SAMPLING_FREQUENCIES[boardModel] ?? [];
  }

  function formatSamplingFrequency(hz: number): string {
    return `${hz / 1e9} GS/s`;
  }

  function defaultSamplingLabel(boardModel: number): string {
    const hz = DEFAULT_SAMPLING[boardModel];
    return hz != null ? `Hardware default (${formatSamplingFrequency(hz)})` : 'Hardware default';
  }

  function defaultChannels(): DigitizerChannel[] {
    const dm = digitizers.find(d => d.id === editDigitizerId)?.board_model ?? 0;
    const n = channelCountForBoard(dm);
    return Array.from({ length: n }, (_, i) => ({ channel: i, enabled: true }));
  }

  function setEditScanType(t: 'hv_scan' | 'digitizer_scan') {
    editScanType = t;
    if (t === 'digitizer_scan') {
      if (editChannels.length === 0) editChannels = defaultChannels();
      if (editDigitizerId == null && digitizers.length > 0) editDigitizerId = digitizers[0].id;
    }
  }

  function selectEditDigitizer(id: number) {
    editDigitizerId = id;
    editSamplingFrequencyHz = null;
    editChannels = defaultChannels();
  }

  function toggleEditChannel(ch: DigitizerChannel) {
    if (ch.enabled) {
      const enabled = editChannels.filter(c => c.enabled);
      if (enabled.length <= 1) return;
      ch.enabled = false;
    } else {
      ch.enabled = true;
    }
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
    const daqUrl = `${env.PUBLIC_DAQ_URL || 'http://localhost'}:${env.PUBLIC_DAQ_PORT || '8001'}`;
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
      if (action === 'stop') { await fetchLog(); stopPolling(); disconnectWs(); daqStatus = null; liveData = []; }
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
      recording: 'badge-success', paused: 'badge-warning', finished: 'badge-success',
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

  async function deleteRun() {
    if (!confirm(`Delete scan #${runId}${run?.label ? ` (${run.label})` : ''}? This cannot be undone.`)) return;
    try {
      await api.delete(`/daq/runs/${runId}`);
      toast.success(`Scan #${runId} deleted`);
      goto('/scans');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to delete scan');
    }
  }

  function syncEditJson() {
    editJson = JSON.stringify({
      type: editScanType,
      voltage_points: editVoltagePoints,
      wait_time_seconds: config?.wait_time_seconds ?? 30,
      sample_interval_seconds: config?.sample_interval_seconds ?? 1,
      number_of_samples: config?.number_of_samples ?? 60,
      end_voltage: config?.end_voltage ?? 0,
      power_supply: config?.power_supply,
      ...(editScanType === 'digitizer_scan'
        ? {
            digitizer_id: editDigitizerId,
            trigger_mode: editTriggerMode,
            trigger_frequency_hz: editTriggerFrequencyHz,
            sampling_frequency_hz: editSamplingFrequencyHz,
            number_of_triggers: editNumberOfTriggers,
            record_length: editRecordLength,
            post_trigger_size: editPostTriggerSize,
            input_range_vpp: editInputRangeVpp,
            channels: editChannels,
          }
        : {}),
    }, null, 2);
  }

  function applyEditJson() {
    try {
      const parsed = JSON.parse(editJson);
      if (config) {
        if (parsed.type) editScanType = parsed.type;
        if (parsed.voltage_points) editVoltagePoints = parsed.voltage_points;
        if (parsed.wait_time_seconds != null) config.wait_time_seconds = parsed.wait_time_seconds;
        if (parsed.sample_interval_seconds != null) config.sample_interval_seconds = parsed.sample_interval_seconds;
        if (parsed.number_of_samples != null) config.number_of_samples = parsed.number_of_samples;
        if (parsed.end_voltage != null) config.end_voltage = parsed.end_voltage;
        config.power_supply = parsed.power_supply ?? config.power_supply;
        editDigitizerId = parsed.digitizer_id ?? editDigitizerId;
        editTriggerMode = parsed.trigger_mode ?? editTriggerMode;
        editTriggerFrequencyHz = parsed.trigger_frequency_hz ?? editTriggerFrequencyHz;
        if (parsed.sampling_frequency_hz != null) editSamplingFrequencyHz = parsed.sampling_frequency_hz;
        if (editScanType === 'digitizer_scan' && editSamplingFrequencyHz != null) {
          const dm = digitizerBoardModel(editDigitizerId);
          if (!samplingOptions(dm).includes(Number(editSamplingFrequencyHz))) {
            editSamplingFrequencyHz = null;
          }
        }
        editNumberOfTriggers = parsed.number_of_triggers ?? editNumberOfTriggers;
        editRecordLength = parsed.record_length ?? editRecordLength;
        editPostTriggerSize = parsed.post_trigger_size ?? editPostTriggerSize;
        editInputRangeVpp = parsed.input_range_vpp ?? editInputRangeVpp;
        if (parsed.channels) editChannels = parsed.channels;
        if (editScanType === 'digitizer_scan' && editChannels.length === 0) {
          editChannels = defaultChannels();
        }
      }
    } catch {
      toast.error('Invalid JSON');
    }
  }

  function addEditPoint() {
    const last = editVoltagePoints[editVoltagePoints.length - 1];
    const copy = last ? last.map(ch => ({ ...ch })) : [{ slot: 0, channel: 0, voltage: 100 }];
    editVoltagePoints = [...editVoltagePoints, copy];
  }

  function removeEditPoint(index: number) {
    editVoltagePoints = editVoltagePoints.filter((_, i) => i !== index);
  }

  function addEditChannel(pointIndex: number) {
    const updated = [...editVoltagePoints];
    updated[pointIndex] = [...updated[pointIndex], { slot: 0, channel: 0, voltage: 100 }];
    editVoltagePoints = updated;
  }

  function removeEditChannel(pointIndex: number, chIndex: number) {
    const updated = [...editVoltagePoints];
    updated[pointIndex] = updated[pointIndex].filter((_, i) => i !== chIndex);
    editVoltagePoints = updated;
  }

  async function updateConfig() {
    if (!config) return;
    updating = true;
    try {
      const body = {
        type: editScanType,
        voltage_points: editVoltagePoints,
        wait_time_seconds: config.wait_time_seconds,
        sample_interval_seconds: config.sample_interval_seconds,
        number_of_samples: config.number_of_samples,
        end_voltage: config.end_voltage,
        power_supply: config.power_supply,
        digitizer_id: editScanType === 'digitizer_scan' ? editDigitizerId : null,
        trigger_mode: editScanType === 'digitizer_scan' ? editTriggerMode : null,
        trigger_frequency_hz: editScanType === 'digitizer_scan' ? editTriggerFrequencyHz : null,
        sampling_frequency_hz: editScanType === 'digitizer_scan' ? editSamplingFrequencyHz : null,
        number_of_triggers: editScanType === 'digitizer_scan' ? editNumberOfTriggers : null,
        record_length: editScanType === 'digitizer_scan' ? editRecordLength : null,
        post_trigger_size: editScanType === 'digitizer_scan' ? editPostTriggerSize : null,
        input_range_vpp: editScanType === 'digitizer_scan' ? editInputRangeVpp : null,
        channels: editScanType === 'digitizer_scan' ? editChannels : null,
      };
      const { data } = await api.put(`/daq/configs/${config.id}`, body);
      toast.success('Configuration saved');
      config = data;
      editScanType = data.type || 'hv_scan';
      editVoltagePoints = JSON.parse(JSON.stringify(data.voltage_points));
      editDigitizerId = data.digitizer_id ?? null;
      editTriggerMode = data.trigger_mode ?? 'random';
      editTriggerFrequencyHz = data.trigger_frequency_hz ?? 1;
      editSamplingFrequencyHz = data.sampling_frequency_hz ?? null;
      editNumberOfTriggers = data.number_of_triggers ?? 100;
      editRecordLength = data.record_length ?? 1024;
      editPostTriggerSize = data.post_trigger_size ?? 50;
      editInputRangeVpp = data.input_range_vpp ?? null;
      editChannels = data.channels?.length
        ? JSON.parse(JSON.stringify(data.channels))
        : (data.type === 'digitizer_scan' ? defaultChannels() : []);
      editJson = '';
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to update configuration');
    } finally {
      updating = false;
    }
  }

  async function updateRunDetails() {
    updatingRun = true;
    try {
      const body: Record<string, string> = {};
      if (editLabel !== (run?.label ?? '')) body.label = editLabel;
      if (editComments !== (run?.comments ?? '')) body.comments = editComments;
      if (Object.keys(body).length === 0) { updatingRun = false; return; }
      const { data } = await api.patch(`/daq/runs/${runId}`, body);
      run = data;
      editLabel = data.label ?? '';
      editComments = data.comments ?? '';
      toast.success('Run details saved');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to update run details');
    } finally {
      updatingRun = false;
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
            <button class="btn btn-outline btn-error ml-auto" onclick={() => deleteRun()}>
              <Trash2 class="size-5" /> Delete
            </button>
          </div>
        </div>
      </div>

      {#if run?.comments}
        <div class="card bg-base-200">
          <div class="card-body">
            <h3 class="card-title">Comments</h3>
            <p class="text-base whitespace-pre-wrap">{run.comments}</p>
          </div>
        </div>
      {/if}

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
    <div class="flex flex-col gap-4">
      <div class="card bg-base-200">
        <div class="card-body">
          <h3 class="card-title">Run Details</h3>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <label class="form-control flex flex-col self-center">
              <span class="label-text">Label</span>
              <input type="text" class="input input-bordered w-full" placeholder="e.g. Test run 2026-06-01" bind:value={editLabel} />
            </label>
            <label class="form-control flex flex-col sm:col-span-2">
              <span class="label-text">Comments</span>
              <textarea class="textarea textarea-bordered w-full" placeholder="Any notes about this scan..." bind:value={editComments}></textarea>
            </label>
            <button class="btn btn-primary sm:col-span-3 justify-self-end" onclick={updateRunDetails} disabled={updatingRun}>
              {updatingRun ? 'Saving...' : 'Save Details'}
            </button>
          </div>
        </div>
      </div>

      <div class="card bg-base-200">
        <div class="card-body">
          <div class="flex items-center justify-between">
            <h3 class="card-title">Configuration</h3>
            <button class="btn btn-ghost btn-sm" onclick={() => { useJsonEditor = !useJsonEditor; if (useJsonEditor) syncEditJson(); }}>
              <Code class="size-4" /> {useJsonEditor ? 'Form' : 'JSON'}
            </button>
          </div>
          {#if config}
            <div class="flex flex-col gap-4">
              {#if useJsonEditor}
                <label class="form-control">
                  <span class="label-text">Raw Configuration (JSON)</span>
                  <textarea class="textarea textarea-bordered font-mono text-sm min-h-[400px] w-full" bind:value={editJson}></textarea>
                </label>
                <button class="btn btn-sm btn-outline self-end" onclick={applyEditJson}>Apply JSON to Form</button>
              {:else}
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

                <div class="form-control">
                  <span class="label-text">Scan Type</span>
                  <div class="join">
                    <button
                      class="join-item btn btn-sm {editScanType === 'hv_scan' ? 'btn-primary' : ''}"
                      onclick={() => setEditScanType('hv_scan')}>HV Scan</button>
                    <button
                      class="join-item btn btn-sm {editScanType === 'digitizer_scan' ? 'btn-primary' : ''}"
                      onclick={() => setEditScanType('digitizer_scan')}>Digitizer Scan</button>
                  </div>
                </div>

                {#if editScanType === 'digitizer_scan'}
                  <div class="divider">Digitizer</div>

                  <div class="grid grid-cols-2 gap-4">
                    <label class="form-control flex flex-col">
                      <span class="label-text">Digitizer</span>
                      <select
                        class="select select-bordered"
                        value={editDigitizerId}
                        onchange={(e) => selectEditDigitizer(Number((e.currentTarget as HTMLSelectElement).value))}>
                        <option value="">— Select —</option>
                        {#each digitizers as d}
                          <option value={d.id}>{d.name} (id {d.id})</option>
                        {/each}
                      </select>
                    </label>
                    <label class="form-control flex flex-col">
                      <span class="label-text">Trigger Mode</span>
                      <select class="select select-bordered" bind:value={editTriggerMode}>
                        <option value="random">Random (internal)</option>
                        <option value="external">External</option>
                      </select>
                    </label>
                    {#if editTriggerMode === 'random'}
                      <label class="form-control flex flex-col">
                        <span class="label-text">Trigger Frequency (Hz)</span>
                        <input type="number" step="0.1" class="input input-bordered"
                          bind:value={editTriggerFrequencyHz} />
                      </label>
                    {/if}
                    <label class="form-control flex flex-col">
                      <span class="label-text">Number of Triggers</span>
                      <input type="number" class="input input-bordered"
                        bind:value={editNumberOfTriggers} />
                    </label>
                    <label class="form-control flex flex-col">
                      <span class="label-text">Record Length (DRS4 fixed at 1024)</span>
                      <input type="number" min="1" max="1024" class="input input-bordered"
                        bind:value={editRecordLength} />
                    </label>
                    <label class="form-control flex flex-col">
                      <span class="label-text">Sampling Frequency ({#if samplingOptions(digitizerBoardModel(editDigitizerId)).length}
                        only {samplingOptions(digitizerBoardModel(editDigitizerId)).map(formatSamplingFrequency).join(' / ')}{/if})</span>
                      <select class="select select-bordered" bind:value={editSamplingFrequencyHz}>
                        <option value={null}>{defaultSamplingLabel(digitizerBoardModel(editDigitizerId))}</option>
                        {#each samplingOptions(digitizerBoardModel(editDigitizerId)) as hz}
                          <option value={hz}>{formatSamplingFrequency(hz)} ({hz} Hz)</option>
                        {/each}
                      </select>
                    </label>
                    <label class="form-control flex flex-col">
                      <span class="label-text">Post Trigger Size (%, 0-100)</span>
                      <input type="number" min="0" max="100" step="1" class="input input-bordered"
                        bind:value={editPostTriggerSize} />
                    </label>
                    <label class="form-control flex flex-col">
                      <span class="label-text">Input Range (Vpp, optional)</span>
                      <input type="number" step="0.5" class="input input-bordered"
                        bind:value={editInputRangeVpp} />
                    </label>
                  </div>

                  <div class="form-control">
                    <span class="label-text mb-1">Channels</span>
                    <div class="flex flex-wrap gap-2">
                      {#each editChannels as ch}
                        <button
                          type="button"
                          class="btn btn-sm {ch.enabled ? 'btn-primary' : 'btn-ghost'}"
                          aria-pressed={ch.enabled}
                          onclick={() => toggleEditChannel(ch)}>
                          CH {ch.channel}
                        </button>
                      {/each}
                    </div>
                  </div>
                {/if}

                <div class="divider">Voltage Points</div>

                {#each editVoltagePoints as point, pi}
                  <div class="card bg-base-300">
                    <div class="card-body p-4">
                      <div class="flex items-center justify-between">
                        <span class="font-bold">Point {pi + 1}</span>
                        <button class="btn btn-ghost btn-xs text-error" onclick={() => removeEditPoint(pi)}>Remove</button>
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
                            <button class="btn btn-ghost btn-xs text-error" onclick={() => removeEditChannel(pi, ci)}>×</button>
                          </div>
                        </div>
                      {/each}
                      <button class="btn btn-ghost btn-xs" onclick={() => addEditChannel(pi)}>+ Add Channel</button>
                    </div>
                  </div>
                {/each}

                <button class="btn btn-outline btn-sm" onclick={addEditPoint}>+ Add Point</button>
              {/if}

              <button class="btn btn-primary self-end" onclick={updateConfig} disabled={updating}>
                {updating ? 'Saving...' : 'Save Configuration'}
              </button>
            </div>
          {:else}
            <p class="text-base-content/50">No configuration data available.</p>
          {/if}
        </div>
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
