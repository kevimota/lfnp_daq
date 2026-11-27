<script lang="ts">
  import { onMount } from 'svelte';
  import { Plus, Play, Square, RotateCcw, Code, List, Trash2, Search, ArrowLeft } from '@lucide/svelte';
  import api from '$lib/api';
  import { toast } from '$lib/toast';
  import { formatApiError } from '$lib/apiError';
  import { goto } from '$app/navigation';
  import {
    SAMPLING_FREQUENCIES,
    DEFAULT_SAMPLING,
    channelCountForBoard,
    dcOffsetHalfScaleMv,
    dcOffsetMvToCode,
    dcOffsetCodeToMv,
    tr0ThresholdMvToCode,
    tr0ThresholdCodeToMv,
    tr0DcOffsetMvToCode,
    tr0DcOffsetCodeToMv,
    formatDacCode,
    TR0_PRESETS,
    type Tr0Preset,
    samplingOptions,
    formatSamplingFrequency,
    defaultSamplingHz,
  } from '$lib/digitizer';

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

  interface ChannelConfig {
    slot: number;
    channel: number;
    voltage: number;
  }

  interface DigitizerChannel {
    channel: number;
    enabled: boolean;
  }

  let runs: ScanRun[] = $state([]);
  let supplies: PowerSupply[] = $state([]);
  let digitizers: CaenDigitizer[] = $state([]);
  let showNewModal = $state(false);
  let useJsonEditor = $state(false);

  let newScan = $state({
    type: 'hv_scan',
    label: '',
    comments: '',
    wait_time_seconds: 30,
    sample_interval_seconds: 1,
    number_of_samples: 60,
    end_voltage: 0,
    power_supply: 1,
    voltage_points: [[{ slot: 0, channel: 0, voltage: 100 }] as ChannelConfig[]],
    digitizer_id: null as number | null,
    trigger_mode: 'random' as string,
    trigger_frequency_hz: 1,
    sampling_frequency_hz: null as number | null,
    number_of_triggers: 100,
    record_length: 1024,
    post_trigger_size: 0,
    dc_offset_mv: 0,
    channels: [] as DigitizerChannel[],
    fast_trigger_digitizing: true,  // Default to ON when fast trigger is active
    fast_trigger_threshold: 0x6666,
    fast_trigger_threshold_mv: 0,
    fast_trigger_dc_offset: 0x8000,
    fast_trigger_dc_offset_mv: 0,
  });

  let jsonConfig = $state('');

  let creating = $state(false);
  let currentPage = $state(1);
  let totalPages = $state(1);
  let searchQuery = $state('');
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  onMount(async () => {
    await Promise.all([fetchRuns(), fetchSupplies(), fetchDigitizers()]);
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

  async function fetchDigitizers() {
    try {
      const { data } = await api.get('/hardware/caen-digitizers');
      digitizers = data;
    } catch {}
  }

  let tr0PresetOpen = $state(false);

  function applyTr0Preset(preset: Tr0Preset) {
    newScan.fast_trigger_threshold = preset.thresholdCode;
    newScan.fast_trigger_threshold_mv = preset.thresholdMv;
    newScan.fast_trigger_dc_offset = preset.offsetCode;
    newScan.fast_trigger_dc_offset_mv = preset.offsetMv;
    tr0PresetOpen = false;
  }

  function onTr0ThresholdMvInput(raw: string) {
    const mv = Number(raw);
    if (!Number.isFinite(mv)) return;
    newScan.fast_trigger_threshold_mv = mv;
    newScan.fast_trigger_threshold = tr0ThresholdMvToCode(mv);
  }

  function onTr0DcOffsetMvInput(raw: string) {
    const mv = Number(raw);
    if (!Number.isFinite(mv)) return;
    newScan.fast_trigger_dc_offset_mv = mv;
    newScan.fast_trigger_dc_offset = tr0DcOffsetMvToCode(mv);
  }

  function digitizerBoardModel(id: number | null): number {
    return digitizers.find(d => d.id === id)?.board_model ?? 0;
  }

  function defaultChannels(): DigitizerChannel[] {
    const dm = digitizers.find(d => d.id === newScan.digitizer_id)?.board_model ?? 0;
    const n = channelCountForBoard(dm);
    return Array.from({ length: n }, (_, i) => ({ channel: i, enabled: true }));
  }

  function setScanType(t: 'hv_scan' | 'digitizer_scan') {
    newScan.type = t;
    if (t === 'digitizer_scan') {
      if (newScan.digitizer_id == null && digitizers.length > 0) {
        newScan.digitizer_id = digitizers[0].id;
      }
      if (newScan.sampling_frequency_hz == null) {
        newScan.sampling_frequency_hz = defaultSamplingHz(digitizerBoardModel(newScan.digitizer_id));
      }
      if (newScan.channels.length === 0) newScan.channels = defaultChannels();
    } else {
      newScan.digitizer_id = null;
      newScan.channels = [];
    }
  }

  function selectDigitizer(id: number) {
    newScan.digitizer_id = id;
    newScan.sampling_frequency_hz = defaultSamplingHz(digitizerBoardModel(id));
    newScan.channels = defaultChannels();
  }

  function toggleDigitizerChannel(ch: DigitizerChannel) {
    if (ch.enabled) {
      const enabled = newScan.channels.filter(c => c.enabled);
      if (enabled.length <= 1) return;
      ch.enabled = false;
    } else {
      ch.enabled = true;
    }
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

  function isValidHvPoints(points: { slot: number; channel: number; voltage: number }[][]): boolean {
    return points.length > 0 && points.every((point) => point.length > 0);
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
      newScan.type = config.type || 'hv_scan';
      newScan.digitizer_id = config.digitizer_id ?? (config.type === 'digitizer_scan' && digitizers.length > 0 ? digitizers[0].id : null);
      newScan.trigger_mode = config.trigger_mode ?? 'random';
      newScan.trigger_frequency_hz = config.trigger_frequency_hz ?? 1;
      newScan.sampling_frequency_hz = config.sampling_frequency_hz
        ?? (config.type === 'digitizer_scan' ? defaultSamplingHz(digitizerBoardModel(newScan.digitizer_id)) : null);
      newScan.number_of_triggers = config.number_of_triggers ?? 100;
      newScan.record_length = config.record_length ?? 1024;
      newScan.post_trigger_size = config.post_trigger_size ?? 0;
      newScan.dc_offset_mv = config.dc_offset != null
        ? dcOffsetCodeToMv(config.dc_offset, digitizerBoardModel(newScan.digitizer_id))
        : 0;
      newScan.channels = config.channels?.length ? config.channels : defaultChannels();
      newScan.fast_trigger_digitizing = config.fast_trigger_digitizing ?? false;
      newScan.fast_trigger_threshold = config.fast_trigger_threshold ?? 0x6666;
      newScan.fast_trigger_threshold_mv = tr0ThresholdCodeToMv(newScan.fast_trigger_threshold);
      newScan.fast_trigger_dc_offset = config.fast_trigger_dc_offset ?? 0x8000;
      newScan.fast_trigger_dc_offset_mv = tr0DcOffsetCodeToMv(newScan.fast_trigger_dc_offset);
      syncJson();
    } catch {}
  }

  function syncJson() {
    jsonConfig = JSON.stringify(
      {
        type: newScan.type,
        voltage_points: newScan.voltage_points,
        wait_time_seconds: newScan.wait_time_seconds,
        sample_interval_seconds: newScan.sample_interval_seconds,
        number_of_samples: newScan.number_of_samples,
        end_voltage: newScan.end_voltage,
        power_supply: newScan.power_supply,
        ...(newScan.type === 'digitizer_scan'
          ? {
              digitizer_id: newScan.digitizer_id,
              trigger_mode: newScan.trigger_mode,
              trigger_frequency_hz: newScan.trigger_frequency_hz,
              sampling_frequency_hz: newScan.sampling_frequency_hz,
              number_of_triggers: newScan.number_of_triggers,
              record_length: newScan.record_length,
              post_trigger_size: newScan.post_trigger_size,
              dc_offset_mv: newScan.dc_offset_mv,
              fast_trigger_digitizing: newScan.fast_trigger_digitizing,
              fast_trigger_threshold: newScan.fast_trigger_threshold,
              fast_trigger_threshold_mv: newScan.fast_trigger_threshold_mv,
              fast_trigger_dc_offset: newScan.fast_trigger_dc_offset,
              fast_trigger_dc_offset_mv: newScan.fast_trigger_dc_offset_mv,
              channels: newScan.channels,
            }
          : {}),
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
      if (parsed.type) newScan.type = parsed.type;
      newScan.digitizer_id = parsed.digitizer_id ?? newScan.digitizer_id;
      newScan.trigger_mode = parsed.trigger_mode ?? newScan.trigger_mode;
      newScan.trigger_frequency_hz = parsed.trigger_frequency_hz ?? newScan.trigger_frequency_hz;
      if (parsed.sampling_frequency_hz != null) newScan.sampling_frequency_hz = parsed.sampling_frequency_hz;
      newScan.number_of_triggers = parsed.number_of_triggers ?? newScan.number_of_triggers;
      newScan.record_length = parsed.record_length ?? newScan.record_length;
      newScan.post_trigger_size = parsed.post_trigger_size ?? newScan.post_trigger_size;
      if (newScan.type === 'digitizer_scan' && newScan.sampling_frequency_hz != null) {
        const dm = digitizerBoardModel(newScan.digitizer_id);
        if (!samplingOptions(dm).includes(Number(newScan.sampling_frequency_hz))) {
          newScan.sampling_frequency_hz = defaultSamplingHz(dm);
        }
      }
      newScan.dc_offset_mv = parsed.dc_offset_mv ?? newScan.dc_offset_mv;
      if (parsed.fast_trigger_digitizing != null) newScan.fast_trigger_digitizing = parsed.fast_trigger_digitizing;
      if (parsed.fast_trigger_threshold_mv != null) {
        newScan.fast_trigger_threshold_mv = parsed.fast_trigger_threshold_mv;
        newScan.fast_trigger_threshold = tr0ThresholdMvToCode(newScan.fast_trigger_threshold_mv);
      } else if (parsed.fast_trigger_threshold != null) {
        newScan.fast_trigger_threshold = parsed.fast_trigger_threshold;
        newScan.fast_trigger_threshold_mv = tr0ThresholdCodeToMv(newScan.fast_trigger_threshold);
      }
      if (parsed.fast_trigger_dc_offset_mv != null) {
        newScan.fast_trigger_dc_offset_mv = parsed.fast_trigger_dc_offset_mv;
        newScan.fast_trigger_dc_offset = tr0DcOffsetMvToCode(newScan.fast_trigger_dc_offset_mv);
      } else if (parsed.fast_trigger_dc_offset != null) {
        newScan.fast_trigger_dc_offset = parsed.fast_trigger_dc_offset;
        newScan.fast_trigger_dc_offset_mv = tr0DcOffsetCodeToMv(newScan.fast_trigger_dc_offset);
      }
      if (parsed.channels) newScan.channels = parsed.channels;
      if (newScan.type === 'digitizer_scan' && newScan.channels.length === 0) {
        newScan.channels = defaultChannels();
      }
    } catch {
      toast.error('Invalid JSON');
    }
  }

  async function createScan() {
    if (newScan.type === 'hv_scan' && !isValidHvPoints(newScan.voltage_points)) {
      toast.error('HV scan requires at least one HV channel in every point');
      return;
    }
    creating = true;
    try {
      const payload = {
        ...newScan,
        dc_offset: newScan.type === 'digitizer_scan'
          ? dcOffsetMvToCode(newScan.dc_offset_mv, digitizerBoardModel(newScan.digitizer_id))
          : null,
        fast_trigger_digitizing: newScan.fast_trigger_digitizing,
        fast_trigger_threshold: newScan.fast_trigger_threshold,
        fast_trigger_dc_offset: newScan.fast_trigger_dc_offset,
        label: newScan.label || undefined,
        comments: newScan.comments || undefined,
      };
      delete (payload as any).dc_offset_mv;
      delete (payload as any).fast_trigger_threshold_mv;
      delete (payload as any).fast_trigger_dc_offset_mv;
      await api.post('/daq/runs', payload);
      showNewModal = false;
      await fetchRuns();
      resetForm();
    } catch (err: any) {
      toast.error(formatApiError(err, 'Failed to create scan'));
    } finally {
      creating = false;
    }
  }

  function resetForm() {
    newScan = {
      type: 'hv_scan',
      label: '',
      comments: '',
      wait_time_seconds: 30,
      sample_interval_seconds: 1,
      number_of_samples: 60,
      end_voltage: 0,
      power_supply: supplies[0]?.id || 1,
      voltage_points: [[{ slot: 0, channel: 0, voltage: 100 }]],
      digitizer_id: null as number | null,
      trigger_mode: 'random' as 'random' | 'external',
      trigger_frequency_hz: 1,
      sampling_frequency_hz: null as number | null,
      number_of_triggers: 100,
      record_length: 1024,
      post_trigger_size: 0,
      dc_offset_mv: 0,
      channels: [] as DigitizerChannel[],
      fast_trigger_digitizing: true,
      fast_trigger_threshold: 0x6666,
      fast_trigger_threshold_mv: 0,
      fast_trigger_dc_offset: 0x8000,
      fast_trigger_dc_offset_mv: 0,
    };
    jsonConfig = '';
    useJsonEditor = false;
    tr0PresetOpen = false;
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
      toast.error(formatApiError(err, 'Failed to start scan'));
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
      toast.error(formatApiError(err, 'Failed to delete scan'));
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

<div class="flex items-center justify-between my-2 mb-4">
  <div class='flex items-center justify-between'>
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
    oninput={(e) => onSearchInput((e.currentTarget as HTMLInputElement).value)} />
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
            <select class="select select-bordered" onchange={(e) => loadConfigFromRun(Number((e.currentTarget as HTMLSelectElement).value))}>
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

          <div class="form-control">
            <span class="label-text">Scan Type</span>
            <div class="join">
              <button
                class="join-item btn btn-sm {newScan.type === 'hv_scan' ? 'btn-primary' : ''}"
                onclick={() => setScanType('hv_scan')}>HV Scan</button>
              <button
                class="join-item btn btn-sm {newScan.type === 'digitizer_scan' ? 'btn-primary' : ''}"
                onclick={() => setScanType('digitizer_scan')}>Digitizer Scan</button>
            </div>
          </div>

          {#if newScan.type === 'digitizer_scan'}
            <div class="divider">Digitizer</div>

            <div class="grid grid-cols-2 gap-4">
              <label class="form-control">
                <span class="label-text">Digitizer</span>
                <select
                  class="select select-bordered"
                  value={newScan.digitizer_id}
                  onchange={(e) => selectDigitizer(Number((e.currentTarget as HTMLSelectElement).value))}>
                  <option value="">— Select —</option>
                  {#each digitizers as d}
                    <option value={d.id}>{d.name} (id {d.id})</option>
                  {/each}
                </select>
              </label>
              <label class="form-control">
                <span class="label-text">Trigger Mode</span>
                <select class="select select-bordered" bind:value={newScan.trigger_mode}>
                  <option value="random">Random (internal)</option>
                  <option value="external">External</option>
                  {#if digitizerBoardModel(newScan.digitizer_id) === 18}
                    <option value="fast">Fast (TR0)</option>
                    <option value="random-external">Random + External</option>
                    <option value="random-fast">Random + Fast</option>
                  {/if}
                  {#if digitizerBoardModel(newScan.digitizer_id) === 27}
                    <option value="random-external">Random + External</option>
                  {/if}
                </select>
              </label>

              <label class="form-control">
                <span class="label-text">Number of Triggers</span>
                <input type="number" class="input input-bordered"
                  bind:value={newScan.number_of_triggers} />
              </label>
              <label class="form-control">
                <span class="label-text">Record Length (DT5742 fixed at 1024)</span>
                <input type="number" min="1" max="1024" class="input input-bordered"
                  bind:value={newScan.record_length} />
              </label>
              <label class="form-control">
                <span class="label-text">Sampling Frequency </span>
                <select class="select select-bordered" bind:value={newScan.sampling_frequency_hz}>
                  {#each samplingOptions(digitizerBoardModel(newScan.digitizer_id)) as hz}
                    <option value={hz}>{formatSamplingFrequency(hz)}</option>
                  {/each}
                </select>
              </label>
              <label class="form-control">
                <span class="label-text">Post Trigger Size (%, 0-100)</span>
                <input type="number" min="0" max="100" step="1" class="input input-bordered"
                  bind:value={newScan.post_trigger_size} />
              </label>
              {#if newScan.type === 'digitizer_scan'}
                <label class="form-control">
                  <span class="label-text">DC Offset ({newScan.dc_offset_mv} mV)</span>
                  <input type="range" class="range range-primary range-sm"
                    min={-dcOffsetHalfScaleMv(digitizerBoardModel(newScan.digitizer_id))}
                    max={dcOffsetHalfScaleMv(digitizerBoardModel(newScan.digitizer_id))}
                    step="1" bind:value={newScan.dc_offset_mv} />
                  <input type="number" step="1" class="input input-sm input-bordered mt-1"
                    bind:value={newScan.dc_offset_mv} />
                </label>
              {/if}
            </div>

            <div class="form-control">
              <span class="label-text mb-1">Channels</span>
              <div class="flex flex-wrap gap-2">
                {#each newScan.channels as ch}
                  <button
                    type="button"
                    class="btn btn-sm {ch.enabled ? 'btn-primary' : 'btn-ghost'}"
                    aria-pressed={ch.enabled}
                    onclick={() => toggleDigitizerChannel(ch)}>
                    CH {ch.channel}
                  </button>
                {/each}
              </div>
            </div>

            {#if newScan.trigger_mode === 'random' || newScan.trigger_mode === 'random-external' || newScan.trigger_mode === 'random-fast'}
              <div class="col-span-2">
                <div class="divider">Random Trigger</div>
                <div class="grid grid-cols-2 gap-4">
                  <label class="form-control">
                    <span class="label-text">Trigger Frequency (Hz)</span>
                    <input type="number" step="0.1" class="input input-bordered"
                      bind:value={newScan.trigger_frequency_hz}
                      max={newScan.trigger_mode === 'random' ? 1000 : 10}
                    />
                  </label>
                </div>
              </div>
            {/if}

            {#if newScan.trigger_mode === 'fast' || newScan.trigger_mode === 'random-fast'}
              <div class="col-span-2">
                <div class="divider">Fast Trigger (TR0) Configuration</div>
                <div class="grid grid-cols-2 gap-4">
                  <label class="form-control w-full">
                    <span class="label-text">Digitize TR0</span>
                    <select class="select select-bordered" bind:value={newScan.fast_trigger_digitizing}>
                      <option value={false}>No</option>
                      <option value={true}>Yes</option>
                    </select>
                  </label>
                  <div class="form-control w-full">
                    <span class="label-text">TR0 presets (DT5742 manual Table 9.1)</span>
                    <details class="dropdown" bind:open={tr0PresetOpen}>
                      <summary class="btn btn-sm btn-outline">Apply preset</summary>
                      <ul class="dropdown-content menu bg-base-100 rounded-box z-10 w-80 p-2 shadow">
                        {#each TR0_PRESETS as preset}
                          <li>
                            <button type="button" onclick={() => applyTr0Preset(preset)}>
                              <span class="flex flex-col items-start">
                                <span>{preset.label}</span>
                                <span class="text-xs opacity-60">
                                  offset {preset.offsetMv} mV · threshold {preset.thresholdMv} mV
                                </span>
                              </span>
                            </button>
                          </li>
                        {/each}
                      </ul>
                    </details>
                  </div>
                  <label class="form-control w-full">
                    <span class="label-text">TR0 Threshold (mV, approx.)</span>
                    <input type="number" min="-2000" max="3000" step="1" class="input input-bordered"
                      value={newScan.fast_trigger_threshold_mv}
                      oninput={(e) => onTr0ThresholdMvInput(e.currentTarget.value)} />
                    <span class="label-text-alt">= {formatDacCode(newScan.fast_trigger_threshold)}</span>
                  </label>
                  <label class="form-control w-full">
                    <span class="label-text">TR0 DC Offset (mV)</span>
                    <input type="number" min="-2000" max="2000" step="1" class="input input-bordered"
                      value={newScan.fast_trigger_dc_offset_mv}
                      oninput={(e) => onTr0DcOffsetMvInput(e.currentTarget.value)} />
                    <span class="label-text-alt">= {formatDacCode(newScan.fast_trigger_dc_offset)}</span>
                  </label>
                </div>
                <p class="text-xs opacity-60 mt-2">
                  Mezzanine PCB rev ≥ 1: TR0 input ±2000 mV, threshold 0x6666 = 0 mV.
                  For signals above 2 Vpp, CAEN recommends an external attenuator.
                </p>
              </div>
            {/if}
          {/if}

          <div class="divider">Power Supply Configuration</div>

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

          {#if newScan.type === 'hv_scan' && !isValidHvPoints(newScan.voltage_points)}
            <div class="alert alert-warning text-sm">
              HV scans require at least one HV channel in every point.
            </div>
          {/if}

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

                {#if newScan.type === 'hv_scan' && point.length === 0}
                  <div class="text-error text-xs">This point has no HV channel.</div>
                {/if}

                <button class="btn btn-ghost btn-xs" onclick={() => addChannel(pi)}>+ Add Channel</button>
              </div>
            </div>
          {/each}

          <button class="btn btn-outline btn-sm" onclick={addPoint}>+ Add Point</button>
        {/if}
      </div>

      <div class="modal-action">
        <button class="btn" onclick={() => { showNewModal = false; resetForm(); }}>Cancel</button>
        <button class="btn btn-primary" onclick={createScan} disabled={creating || (newScan.type === 'hv_scan' && !isValidHvPoints(newScan.voltage_points))}>
          {creating ? 'Creating...' : 'Create Scan'}
        </button>
      </div>
    </div>
    <div class="modal-backdrop" onclick={() => { showNewModal = false; resetForm(); }}></div>
  </div>
{/if}
