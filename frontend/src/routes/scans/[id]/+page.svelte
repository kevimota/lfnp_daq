<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/state';
  import {
    Play,
    Square,
    Pause,
    PlayIcon,
    ArrowLeft,
    Trash2,
    Code,
    ChevronDown,
    ChevronUp,
    ListChecks,
    X,
  } from '@lucide/svelte';
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
    dc_offset: number | null;
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

  interface ChannelStatus {
    is_on: boolean;
    ramping_up: boolean;
    ramping_down: boolean;
    overcurrent: boolean;
    overvoltage: boolean;
    undervoltage: boolean;
    external_trip: boolean;
    HVMax: boolean;
  }

  interface ChannelReading {
    slot: number;
    channel: number;
    voltage: number;
    current: number;
    status: ChannelStatus;
    timestamp: string;
  }

  interface LiveScanMessage {
    type: 'current_scan';
    point: number;
    data: ChannelReading[];
    triggers?: number;
    target?: number;
    timestamp: string;
  }

  interface PointVoltage {
    slot: number;
    channel: number;
    voltage: number;
  }

  interface PointProgress {
    index: number;
    number: number;
    status: 'completed' | 'running' | 'failed' | 'pending';
    started_at: string | null;
    completed_at: string | null;
    samples: number | null;
    triggers: number | null;
    target: number | null;
    voltages: PointVoltage[];
    data_files: string[];
    file_exists: boolean;
  }

  interface ScanProgress {
    run_id: number;
    status: string;
    scan_type: string;
    total_points: number;
    completed_points: number[];
    failed_point: number | null;
    resume_point: number | null;
    active_point: number | null;
    fallback_used: boolean;
    last_run: Record<string, unknown> | null;
    points: PointProgress[];
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
  let editDcOffsetMv = $state(0);
  let editChannels = $state<DigitizerChannel[]>([]);
  let editVoltagePoints = $state<{ slot: number; channel: number; voltage: number }[][]>([]);
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let ws: WebSocket | null = $state(null);
  let wsConnected = $state(false);
  let liveData: LiveScanMessage[] = $state([]);
  let updating = $state(false);
  let runFiles: { name: string; size: number }[] = $state([]);
  let editLabel = $state('');
  let editComments = $state('');
  let updatingRun = $state(false);
  let scanProgress: ScanProgress | null = $state(null);
  let pointsModalOpen = $state(false);
  let selectedPoints: number[] = $state([]);
  let liveOpen = $state(true);

  const runId = $derived(Number(page.params.id));
  const latestLive = $derived(liveData.at(-1) ?? null);
  const completedCount = $derived.by(() =>
    scanProgress ? scanProgress.completed_points.length : 0,
  );
  const totalPointCount = $derived.by(() =>
    scanProgress
      ? scanProgress.total_points
      : config
        ? config.voltage_points.length
        : 0,
  );
  const resumePoint = $derived.by(() =>
    scanProgress ? scanProgress.resume_point : null,
  );
  const failedPoint = $derived.by(() =>
    scanProgress ? scanProgress.failed_point : null,
  );
  const scanActive = $derived.by(() => run?.status === 'running');
  const canResume = $derived.by(
    () =>
      resumePoint !== null &&
      (run?.status === 'failed' || run?.status === 'stopped'),
  );

  onMount(async () => {
    await Promise.all([fetchRun(), fetchSupplies(), fetchDigitizers()]);
    await Promise.all([fetchLog(), fetchProgress()]);
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
        liveOpen = true;
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
      editSamplingFrequencyHz = data.sampling_frequency_hz
        ?? (data.type === 'digitizer_scan' ? defaultSamplingHz(digitizerBoardModel(editDigitizerId)) : null);
      editNumberOfTriggers = data.number_of_triggers ?? 100;
      editRecordLength = data.record_length ?? 1024;
      editPostTriggerSize = data.post_trigger_size ?? 0;
      editDcOffsetMv = data.dc_offset != null
        ? dcOffsetCodeToMv(data.dc_offset, digitizerBoardModel(editDigitizerId))
        : 0;
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

  // DC-offset half-scale in mV per board: DT5742 (18) -> +/-0.5 V, DT5743 (27) -> +/-1.25 V.
  // DAC code 0x8000 == 0 V (default center); code = 0x8000 + mv/half_scale * 0x8000.
  const DC_OFFSET_HALF_SCALE_MV: Record<number, number> = { 18: 500, 27: 1250 };

  function dcOffsetHalfScaleMv(boardModel: number): number {
    return DC_OFFSET_HALF_SCALE_MV[boardModel] ?? 625;
  }

  function dcOffsetMvToCode(mv: number, boardModel: number): number {
    const half = dcOffsetHalfScaleMv(boardModel);
    return Math.max(0, Math.min(0xffff, Math.round(0x8000 + (mv / half) * 0x8000)));
  }

  function dcOffsetCodeToMv(code: number, boardModel: number): number {
    const half = dcOffsetHalfScaleMv(boardModel);
    return Math.round(((code - 0x8000) * half) / 0x8000);
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

  function defaultSamplingHz(boardModel: number): number | null {
    return DEFAULT_SAMPLING[boardModel] ?? samplingOptions(boardModel)[0] ?? null;
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
      if (editSamplingFrequencyHz == null) {
        editSamplingFrequencyHz = defaultSamplingHz(digitizerBoardModel(editDigitizerId));
      }
    }
  }

  function selectEditDigitizer(id: number) {
    editDigitizerId = id;
    editSamplingFrequencyHz = defaultSamplingHz(digitizerBoardModel(id));
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

  async function fetchProgress() {
    try {
      const { data } = await api.get(`/daq/runs/${runId}/progress`);
      scanProgress = data;
    } catch {
      scanProgress = null;
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(async () => {
      await Promise.all([fetchStatus(), fetchLog(), fetchFiles(), fetchProgress()]);
      if (run) {
        const { data } = await api.get(`/daq/runs/${runId}`);
        run = { ...run, status: data.status };
        if (data.status !== 'running') {
          stopPolling();
          disconnectWs();
          daqStatus = null;
        }
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
        if (msg.type === 'current_scan' && Array.isArray(msg.data)) {
          liveData = [...liveData.slice(-100), msg as LiveScanMessage];
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
      await fetchProgress();
      if (action === 'start') {
        liveData = [];
        liveOpen = true;
        startPolling();
        connectWs();
      }
      if (action === 'stop') {
        await fetchLog();
        stopPolling();
        disconnectWs();
        daqStatus = null;
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Failed to ${action}`);
    }
  }

  function togglePoint(index: number) {
    if (selectedPoints.includes(index)) {
      selectedPoints = selectedPoints.filter((i) => i !== index);
    } else {
      selectedPoints = [...selectedPoints, index].sort((a, b) => a - b);
    }
  }

  function pointBadge(status: PointProgress['status']): string {
    const map: Record<string, string> = {
      completed: 'badge-success',
      running: 'badge-info',
      failed: 'badge-error',
      pending: 'badge-ghost',
    };
    return map[status] || 'badge-ghost';
  }

  function formatVoltages(voltages: PointVoltage[], max = 4): string {
    if (!voltages?.length) return '—';
    const parts = voltages
      .slice(0, max)
      .map((v) => `${v.slot}:${v.channel}=${v.voltage} V`);
    if (voltages.length > max) parts.push(`+${voltages.length - max} more`);
    return parts.join(', ');
  }

  function formatPointCount(point: PointProgress): string {
    if (point.triggers != null || point.target != null) {
      return `${point.triggers ?? '—'} / ${point.target ?? '—'}`;
    }
    if (point.samples != null) return `${point.samples} samples`;
    return '—';
  }

  async function startScanSelection(body: { start_point?: number; points?: number[] }) {
    try {
      await api.post(`/daq/runs/${runId}/start`, body);
      toast.success('Scan started');
      pointsModalOpen = false;
      selectedPoints = [];
      liveData = [];
      liveOpen = true;
      await fetchRun();
      await fetchProgress();
      startPolling();
      connectWs();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to start scan');
    }
  }

  async function resumeScan() {
    if (resumePoint === null) return;
    const pointNumber = resumePoint + 1;
    if (!confirm(
      `Redo point ${pointNumber} and continue to the end of the scan? ` +
      'Existing data for this and subsequent points will be overwritten.',
    )) return;
    await startScanSelection({ start_point: resumePoint });
  }

  async function redoSelected() {
    if (selectedPoints.length === 0) return;
    const points = [...selectedPoints].sort((a, b) => a - b);
    if (!confirm(
      `Redo ${points.length} selected point(s)? ` +
      'Their recorded data data will be overwritten.',
    )) return;
    await startScanSelection({ points });
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

  function channelState(status: ChannelStatus): { label: string; badgeClass: string } {
    if (status.overcurrent) return { label: 'Overcurrent', badgeClass: 'badge-error' };
    if (status.overvoltage) return { label: 'Overvoltage', badgeClass: 'badge-error' };
    if (status.undervoltage) return { label: 'Undervoltage', badgeClass: 'badge-error' };
    if (status.external_trip) return { label: 'External trip', badgeClass: 'badge-error' };
    if (status.HVMax) return { label: 'HV max', badgeClass: 'badge-error' };
    if (status.ramping_up) return { label: 'Ramping up', badgeClass: 'badge-warning' };
    if (status.ramping_down) return { label: 'Ramping down', badgeClass: 'badge-warning' };
    if (status.is_on) return { label: 'On', badgeClass: 'badge-success' };
    return { label: 'Off', badgeClass: 'badge-ghost' };
  }

  function formatVoltage(value: number): string {
    return Number.isFinite(value) ? `${value.toFixed(0)} V` : '—';
  }

  function formatCurrent(value: number): string {
    return Number.isFinite(value) ? `${value.toFixed(2)} µA` : '—';
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
            dc_offset_mv: editDcOffsetMv,
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
            editSamplingFrequencyHz = defaultSamplingHz(dm);
          }
        }
        editNumberOfTriggers = parsed.number_of_triggers ?? editNumberOfTriggers;
        editRecordLength = parsed.record_length ?? editRecordLength;
        editPostTriggerSize = parsed.post_trigger_size ?? editPostTriggerSize;
        editDcOffsetMv = parsed.dc_offset_mv ?? editDcOffsetMv;
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
        dc_offset: editScanType === 'digitizer_scan'
          ? dcOffsetMvToCode(editDcOffsetMv, digitizerBoardModel(editDigitizerId))
          : null,
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
      editSamplingFrequencyHz = data.sampling_frequency_hz
        ?? (data.type === 'digitizer_scan' ? defaultSamplingHz(digitizerBoardModel(editDigitizerId)) : null);
      editNumberOfTriggers = data.number_of_triggers ?? 100;
      editRecordLength = data.record_length ?? 1024;
      editPostTriggerSize = data.post_trigger_size ?? 0;
      editDcOffsetMv = data.dc_offset != null
        ? dcOffsetCodeToMv(data.dc_offset, digitizerBoardModel(editDigitizerId))
        : 0;
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
    <button role="tab" class="tab text-lg font-semibold" class:tab-active={activeTab === 'log'} onclick={() => activeTab = 'log'}>Log</button>
    <button role="tab" class="tab text-lg font-semibold" class:tab-active={activeTab === 'edit'} onclick={() => activeTab = 'edit'}>Edit</button>
    <button role="tab" class="tab text-lg font-semibold" class:tab-active={activeTab === 'download'} onclick={() => activeTab = 'download'}>Download</button>
  </div>

  {#if pointsModalOpen}
    <div class="modal modal-open" role="dialog" aria-modal="true" aria-label="Scan points">
      <div class="modal-box max-w-5xl">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h3 class="text-lg font-bold">Scan points — Run {runId}</h3>
            <p class="text-sm text-base-content/70">
              {completedCount}/{totalPointCount} completed
              {#if failedPoint !== null}· failed at point {failedPoint + 1}{/if}
            </p>
          </div>
          <button class="btn btn-sm btn-circle btn-ghost" aria-label="Close" onclick={() => pointsModalOpen = false}>
            <X class="size-5" />
          </button>
        </div>

        {#if scanProgress}
          <div class="mt-4 max-h-[60vh] overflow-auto rounded-box border border-base-300">
            <table class="table table-sm">
              <thead class="sticky top-0 bg-base-100">
                <tr>
                  <th class="w-10"></th>
                  <th>Point</th>
                  <th>Status</th>
                  <th>Voltages</th>
                  <th>Samples / Triggers</th>
                  <th>Completed</th>
                </tr>
              </thead>
              <tbody>
                {#each scanProgress.points as point}
                  <tr class:bg-base-200={point.index === scanProgress.active_point}>
                    <td>
                      <input
                        type="checkbox"
                        class="checkbox checkbox-sm"
                        checked={selectedPoints.includes(point.index)}
                        disabled={scanActive}
                        onchange={() => togglePoint(point.index)}
                        aria-label={`Select point ${point.number}`}
                      />
                    </td>
                    <td class="font-mono">{point.number}</td>
                    <td><span class="badge badge-sm {pointBadge(point.status)}">{point.status}</span></td>
                    <td class="max-w-xs whitespace-normal font-mono text-xs">{formatVoltages(point.voltages)}</td>
                    <td class="font-mono text-xs">{formatPointCount(point)}</td>
                    <td class="text-xs">{point.completed_at ? new Date(point.completed_at).toLocaleString() : '—'}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>

          <div class="modal-action flex-wrap">
            {#if canResume}
              <button class="btn btn-success" onclick={resumeScan} disabled={scanActive}>
                Resume from point {(resumePoint ?? 0) + 1}
              </button>
            {/if}
            <button class="btn btn-primary" onclick={redoSelected} disabled={scanActive || selectedPoints.length === 0}>
              Redo selected ({selectedPoints.length})
            </button>
            <button class="btn" onclick={() => pointsModalOpen = false}>Close</button>
          </div>
        {:else}
          <p class="py-6 text-base-content/50">Progress is not available yet.</p>
        {/if}
      </div>
      <button class="modal-backdrop" aria-label="Close" onclick={() => pointsModalOpen = false}></button>
    </div>
  {/if}

  {#if activeTab === 'monitor'}
    <div class="flex flex-col gap-4">
      <div class="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-box bg-base-200 px-4 py-3 text-sm">
        <div class="flex items-center gap-2">
          <span class="text-base-content/60">DB</span>
          <span class="badge {statusBadge(run.status)}">{run.status}</span>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-base-content/60">FSM</span>
          {#if daqStatus}
            <span class="badge {fsmBadge(daqStatus.state)}">{daqStatus.state}</span>
          {:else}
            <span class="text-base-content/50">—</span>
          {/if}
        </div>
        <div class="flex items-center gap-2">
          <span class="text-base-content/60">Point</span>
          <span class="font-mono">{daqStatus?.hv_point || '—'}</span>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-base-content/60">WebSocket</span>
          {#if wsConnected}
            <span class="badge badge-success badge-sm">Connected</span>
          {:else}
            <span class="badge badge-ghost badge-sm">Disconnected</span>
          {/if}
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

      <!-- Scan Progress -->
      <div class="card bg-base-200">
        <div class="card-body gap-2 p-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex flex-wrap items-center gap-3">
              <h3 class="card-title text-base">Scan Progress</h3>
              <span class="text-sm text-base-content/70">{completedCount}/{totalPointCount} completed</span>
              {#if failedPoint !== null}
                <span class="badge badge-error badge-sm">failed at {failedPoint + 1}</span>
              {/if}
              {#if scanProgress?.active_point != null}
                <span class="badge badge-info badge-sm">point {scanProgress.active_point + 1}</span>
              {/if}
              {#if scanProgress?.fallback_used}
                <span class="badge badge-ghost badge-sm">from log</span>
              {/if}
            </div>
            <div class="flex flex-wrap gap-2">
              {#if canResume}
                <button class="btn btn-sm btn-success" onclick={resumeScan} disabled={scanActive}>
                  <PlayIcon class="size-4" /> Resume
                </button>
              {/if}
              <button class="btn btn-sm btn-outline" onclick={() => pointsModalOpen = true}>
                <ListChecks class="size-4" /> Manage points
              </button>
            </div>
          </div>
          <button
            type="button"
            class="block w-full cursor-pointer"
            aria-label="Manage scan points"
            onclick={() => pointsModalOpen = true}
          >
            <progress class="progress progress-primary w-full" value={completedCount} max={Math.max(totalPointCount, 1)}></progress>
          </button>
        </div>
      </div>

      <!-- Live Data -->
      <div class="card bg-base-200">
        <div class="card-body p-4">
          <button
            type="button"
            class="flex w-full items-center justify-between gap-3 text-left"
            onclick={() => liveOpen = !liveOpen}
          >
            <div class="flex items-center gap-2">
              <h3 class="card-title text-base">Live Data</h3>
              {#if latestLive && run?.status === 'running'}
                <span class="badge badge-success badge-sm">Live</span>
              {/if}
            </div>
            <div class="flex flex-wrap items-center justify-end gap-x-4 gap-y-1 text-sm text-base-content/70">
              {#if latestLive}
                <span>Point {latestLive.point}</span>
                {#if latestLive.triggers !== undefined && latestLive.target !== undefined}
                  <span>Triggers {latestLive.triggers}/{latestLive.target}</span>
                {/if}
                <span>Updated {new Date(latestLive.timestamp).toLocaleTimeString()}</span>
              {:else}
                <span>Waiting for live data…</span>
              {/if}
              {#if liveOpen}
                <ChevronUp class="size-5" />
              {:else}
                <ChevronDown class="size-5" />
              {/if}
            </div>
          </button>

          {#if liveOpen}
            <div class="divider my-2"></div>
            {#if latestLive && latestLive.data.length > 0}
              <div class="max-h-96 overflow-x-auto overflow-y-auto rounded-box">
                <table class="table table-sm">
                  <thead class="sticky top-0 bg-base-200">
                    <tr>
                      <th>Slot</th>
                      <th>Channel</th>
                      <th>Voltage</th>
                      <th>Current</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each latestLive.data as reading}
                      {@const state = channelState(reading.status)}
                      <tr>
                        <td>{reading.slot}</td>
                        <td>{reading.channel}</td>
                        <td class="font-mono">{formatVoltage(reading.voltage)}</td>
                        <td class="font-mono">{formatCurrent(reading.current)}</td>
                        <td><span class="badge {state.badgeClass}">{state.label}</span></td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {:else}
              <div class="text-base-content/50 text-sm py-2">Waiting for live data…</div>
            {/if}
          {/if}
        </div>
      </div>
    </div>

  {:else if activeTab === 'log'}
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title">Log</h3>
        <pre class="text-xs bg-base-300 p-3 rounded-box overflow-y-auto max-h-[70vh] font-mono whitespace-pre-wrap">{logContent || '(no log entries yet)'}</pre>
      </div>
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
                      <span class="label-text">Record Length (DT5742 fixed at 1024)</span>
                      <input type="number" min="1" max="1024" class="input input-bordered"
                        bind:value={editRecordLength} />
                    </label>
                    <label class="form-control flex flex-col">
                      <span class="label-text">Sampling Frequency ({#if samplingOptions(digitizerBoardModel(editDigitizerId)).length}
                        only {samplingOptions(digitizerBoardModel(editDigitizerId)).map(formatSamplingFrequency).join(' / ')}{/if})</span>
                      <select class="select select-bordered" bind:value={editSamplingFrequencyHz}>
                        {#each samplingOptions(digitizerBoardModel(editDigitizerId)) as hz}
                          <option value={hz}>{formatSamplingFrequency(hz)}</option>
                        {/each}
                      </select>
                    </label>
                    <label class="form-control flex flex-col">
                      <span class="label-text">Post Trigger Size (%, 0-100)</span>
                      <input type="number" min="0" max="100" step="1" class="input input-bordered"
                        bind:value={editPostTriggerSize} />
                    </label>
{#if editScanType === 'digitizer_scan'}
                      <label class="form-control flex flex-col">
                        <span class="label-text">DC Offset ({editDcOffsetMv} mV)</span>
                        <input type="range" class="range range-primary range-sm"
                          min={-dcOffsetHalfScaleMv(digitizerBoardModel(editDigitizerId))}
                          max={dcOffsetHalfScaleMv(digitizerBoardModel(editDigitizerId))}
                          step="1" bind:value={editDcOffsetMv} />
                        <input type="number" step="1" class="input input-sm input-bordered mt-1"
                          bind:value={editDcOffsetMv} />
                      </label>
                    {/if}
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
