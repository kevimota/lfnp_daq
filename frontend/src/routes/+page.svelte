<script lang="ts">
  import { onMount, onDestroy, tick } from 'svelte';
  import { Chart, registerables } from 'chart.js';
  import { Cpu, HardDrive, Activity, Gauge, MonitorCheck } from '@lucide/svelte';
  import api from '$lib/api';

  Chart.register(...registerables);

  const COLORS = ['#EF4444', '#38BDF8', '#EAB308', '#22C55E', '#A855F7', '#F97316'];

  interface ScanRun {
    id: number; configuration_id: number; status: string; label: string | null;
    started_at: string | null; created_at: string;
  }
  interface PowerSupply {
    id: number; name: string;
  }
  interface SensorReading {
    name: string; data: Record<string, number>; timestamp: string;
  }
  interface ChartConfig {
    sensor: string; attribute: string; unit: string;
  }

  let onGoingScans = $state<any[]>([]);
  let powerSupplies: PowerSupply[] = $state([]);
  let channelsByPS = $state<Record<number, { slot: number; channel: number; voltage: number }[]>>({});
  let recentRuns: ScanRun[] = $state([]);
  let health = $state<{ backend_status: string; daq_status: string } | null>(null);
  let storage = $state<{ total_bytes: number; used_bytes: number; free_bytes: number; percent_used: number; data_size_bytes: number } | null>(null);

  let chartConfigs: ChartConfig[] = $state([]);
  let sensorReadings = $state<Record<string, SensorReading[]>>({});
  let sensorCharts: Chart[] = $state([]);

  let scanInterval: ReturnType<typeof setInterval>;
  let systemInterval: ReturnType<typeof setInterval>;

  function fsmBadge(state: string) {
    const map: Record<string, string> = {
      halted: 'badge-neutral', configuring: 'badge-info', waiting: 'badge-warning',
      recording: 'badge-success', paused: 'badge-warning', fisished: 'badge-success',
      failed: 'badge-error',
    };
    return map[state] || 'badge-ghost';
  }

  function statusBadge(status: string) {
    const map: Record<string, string> = {
      created: 'badge-ghost', running: 'badge-info', paused: 'badge-warning',
      finished: 'badge-success', stopped: 'badge-neutral', failed: 'badge-error',
    };
    return map[status] || 'badge-ghost';
  }

  function display_usage(bytes:number) {
    if (bytes / 1_024 < 1_024) {
      return (bytes / 1_024).toFixed(1) + ' kB';
    } 
    else if (bytes / 1_048_576 < 1_024) {
      return (bytes / 1_048_576).toFixed(1) + ' MB';
    } 
    else {
      return (bytes / 1_073_741_824).toFixed(1) + ' GB';
    }
  }

  function toGB(bytes: number): string {
    return (bytes / 1_073_741_824).toFixed(1) + ' GB';
  }

  async function fetchOngoingScans() {
    let scans: any[] = [];
    try {
      const { data } = await api.get('/daq/runs', { params: { per_page: 50 } });
      scans = data.items.filter((r: ScanRun) => r.status === 'running');
    } catch {}
    const chMap: Record<number, { slot: number; channel: number; voltage: number }[]> = {};
    for (const scan of scans) {
      try { const resp = await api.get(`/daq/runs/${scan.id}/status`); scan.state = resp.data.state; scan.hv_point = resp.data.hv_point; } catch {}
      try {
        const { data: config } = await api.get(`/daq/configs/${scan.configuration_id}`);
        if (config.power_supply && config.voltage_points) {
          const allChannels: { slot: number; channel: number; voltage: number }[] = [];
          for (const point of config.voltage_points) {
            for (const ch of point) { allChannels.push(ch); }
          }
          chMap[config.power_supply] = [...(chMap[config.power_supply] || []), ...allChannels];
        }
      } catch {}
    }
    channelsByPS = chMap;
    onGoingScans = scans;
  }

  async function fetchSystemData() {
    try { const { data: psData } = await api.get('/hardware/caen-ps'); powerSupplies = psData; } catch {}
    try {
      const { data: runsData } = await api.get('/daq/runs', { params: { per_page: 5 } });
      recentRuns = runsData.items.filter((r: ScanRun) => r.status !== 'running').slice(0, 5);
    } catch {}
    try { const { data: healthData } = await api.get('/system/health'); health = healthData; } catch {}
    try { const { data: storageData } = await api.get('/system/storage'); storage = storageData; } catch {}
  }

  async function fetchSensorSettings() {
    try {
      const { data } = await api.get('/settings', { params: { key: 'overview_sensors' } });
      const raw = data['overview_sensors'];
      chartConfigs = Array.isArray(raw) ? raw.map((c: any) => ({ sensor: c.sensor ?? '', attribute: c.attribute ?? '', unit: c.unit ?? '' })) : [];
    } catch {
      chartConfigs = [];
    }
  }

  async function fetchSensorData() {
    if (chartConfigs.length === 0) return;
    const uniqueSensors = [...new Set(chartConfigs.map(c => c.sensor))];
    const readings: Record<string, SensorReading[]> = {};
    for (const name of uniqueSensors) {
      try {
        const { data } = await api.get('/sensor', { params: { name, limit: 100 } });
        readings[name] = data.toReversed();
      } catch {
        readings[name] = [];
      }
    }
    sensorReadings = readings;
    await updateSensorCharts();
  }

  async function updateSensorCharts() {
    for (const c of sensorCharts) c.destroy();

    if (chartConfigs.length === 0 || Object.keys(sensorReadings).length === 0) {
      sensorCharts = [];
      return;
    }

    await tick();
    const newCharts: Chart[] = [];
    for (let i = 0; i < chartConfigs.length; i++) {
      const cfg = chartConfigs[i];
      const canvas = document.getElementById(`sensor-chart-${i}`) as HTMLCanvasElement | null;
      if (!canvas) continue;

      const readings = sensorReadings[cfg.sensor] ?? [];
      const labels = readings.map(r => new Date(r.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      const data = readings.map(r => r.data[cfg.attribute] ?? null);

      const ch = new Chart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: cfg.attribute,
            data,
            borderColor: COLORS[i % COLORS.length],
            backgroundColor: COLORS[i % COLORS.length] + '1A',
            fill: true,
            tension: 0.4,
            pointRadius: 3,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { maxTicksLimit: 8, color: '#94A3B8' }, grid: { color: 'rgba(148,163,184,0.1)' } },
            y: {
              ticks: { color: '#94A3B8' },
              grid: { color: 'rgba(148,163,184,0.1)' },
              ...(cfg.unit ? { title: { display: true, text: cfg.unit, color: '#94A3B8' } } : {}),
            },
          },
        },
      });
      newCharts.push(ch);
    }
    sensorCharts = newCharts;
  }

  onMount(async () => {
    await fetchSensorSettings();
    await Promise.all([fetchOngoingScans(), fetchSystemData(), fetchSensorData()]);
    scanInterval = setInterval(fetchOngoingScans, 5000);
    systemInterval = setInterval(async () => {
      await fetchSystemData();
      await fetchSensorSettings();
      await fetchSensorData();
    }, 30000);
  });

  onDestroy(() => {
    clearInterval(scanInterval);
    clearInterval(systemInterval);
    for (const c of sensorCharts) c.destroy();
  });
</script>

<h1 class="w-fit uppercase text-4xl my-2 p-2 bg-neutral">
  <strong>Overview</strong>
</h1>

<div class="flex flex-col lg:flex-row gap-4 my-2">
  <!-- Left Column -->
  <div class="flex-2">
    <div class="card bg-base-200">
      <div class="card-body">
        <h2 class="card-title text-xl"><MonitorCheck class="size-5" /> Sensor Data</h2>
        {#if chartConfigs.length > 0}
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-2">
            {#each chartConfigs as cfg, i}
              <div>
                <span class="text-sm font-semibold" style:color={COLORS[i % COLORS.length]}>{cfg.sensor}</span>
                {#if cfg.attribute || cfg.unit}
                  <span class="text-xs text-base-content/50 ml-1">{cfg.attribute}{cfg.unit ? ` (${cfg.unit})` : ''}</span>
                {/if}
                <div class="h-44"><canvas id="sensor-chart-{i}"></canvas></div>
              </div>
            {/each}
          </div>
        {:else}
          <p class="text-base-content/50 py-8 text-center">No sensor data configured for the overview.</p>
        {/if}
      </div>
    </div>

    <div class="card bg-base-200 my-4">
      <div class="card-body">
        <h2 class="card-title text-xl"><Activity class="size-5" /> Last Scan overview</h2>
        Add info on last scan
      </div>
    </div>
  </div>

  <!-- Right Column -->
  <div class="flex-1 flex flex-col gap-4 min-w-0">
    <!-- On-going Scans -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h2 class="card-title text-lg"><Activity class="size-4" /> On-going Scans</h2>
        {#if onGoingScans.length > 0}
          {#each onGoingScans as scan}
            <div class="flex items-center justify-between py-1 border-b border-base-300 last:border-0">
              <span>
                <a href="/scans/{scan.id}" class="link link-hover font-medium">Run {scan.id}</a>
                {#if scan.hv_point}— HV point {scan.hv_point}{/if}
              </span>
              <span class="badge {fsmBadge(scan.state)}">{scan.state}</span>
            </div>
          {/each}
        {:else}
          <p class="text-base-content/50 text-sm">No scan is running now.</p>
        {/if}
      </div>
    </div>

    <!-- Power Supplies -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h2 class="card-title text-lg"><Cpu class="size-4" /> Power Supplies</h2>
        {#if powerSupplies.length > 0}
          <div class="flex flex-col gap-2">
            {#each powerSupplies as ps}
              <div class="py-1">
                <div class="flex items-center gap-2">
                  <Gauge class="size-4 text-base-content/60" />
                  <span class="text-sm font-medium">{ps.name}</span>
                  <span class="badge badge-ghost badge-xs ml-auto">ID {ps.id}</span>
                </div>
                {#if channelsByPS[ps.id]?.length}
                  <div class="flex flex-wrap gap-1 mt-1 ml-6">
                    {#each channelsByPS[ps.id] as ch}
                      <span class="badge badge-outline badge-xs text-base-content/70">{ch.slot}:{ch.channel} @ {ch.voltage}V</span>
                    {/each}
                  </div>
                {:else}
                  <p class="text-xs text-base-content/40 ml-6 mt-0.5">No active channels</p>
                {/if}
              </div>
            {/each}
          </div>
        {:else}
          <p class="text-base-content/50 text-sm">No power supplies configured.</p>
        {/if}
      </div>
    </div>

    <!-- Recent Runs -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h2 class="card-title text-lg"><Activity class="size-4" /> Recent Runs</h2>
        {#if recentRuns.length > 0}
          <div class="flex flex-col gap-1">
            {#each recentRuns as run}
              <div class="flex items-center justify-between py-1 border-b border-base-300 last:border-0">
                <a href="/scans/{run.id}" class="link link-hover text-sm">{run.label || `Run ${run.id}`}</a>
                <span class="badge {statusBadge(run.status)} badge-sm">{run.status}</span>
              </div>
            {/each}
          </div>
        {:else}
          <p class="text-base-content/50 text-sm">No recent runs.</p>
        {/if}
      </div>
    </div>

    <!-- System Health & Storage -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h2 class="card-title text-lg"><HardDrive class="size-4" /> System</h2>
        <div class="flex flex-col gap-3">
          {#if health}
            <div class="flex items-center gap-2 text-sm">
              <span class="size-2.5 rounded-full" class:bg-success={health.daq_status === 'ok'} class:bg-error={health.daq_status !== 'ok'}></span>
              <span class="text-base-content/70">DAQ Service:</span>
              <span class="font-medium">{health.daq_status === 'ok' ? 'Online' : 'Unreachable'}</span>
            </div>
          {/if}
          {#if storage}
            <div>
              <div class="flex justify-between text-sm mb-1">
                <span class="text-base-content/70">Disk Usage</span>
                <span>{toGB(storage.used_bytes)} / {toGB(storage.total_bytes)} ({storage.percent_used}%)</span>
              </div>
              <progress class="progress w-full" class:progress-success={storage.percent_used < 70}
                class:progress-warning={storage.percent_used >= 70 && storage.percent_used < 90}
                class:progress-error={storage.percent_used >= 90}
                value={storage.percent_used} max="100"></progress>
            </div>
            {#if storage.data_size_bytes > 0}
              <div class="flex items-center gap-2 text-sm">
                <span class="text-base-content/70">Size of the data folder:</span>
                <span class="font-medium">{display_usage(storage.data_size_bytes)}</span>
              </div>
            {/if}
          {/if}
        </div>
      </div>
    </div>
  </div>
</div>
