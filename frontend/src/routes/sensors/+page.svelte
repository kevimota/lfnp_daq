<script lang="ts">
  import { onMount, onDestroy, tick } from 'svelte';
  import { Chart, registerables } from 'chart.js';
  import { Activity, Gauge } from '@lucide/svelte';
  import api from '$lib/api';

  Chart.register(...registerables);

  interface SensorInfo {
    name: string;
    attributes: string[];
  }

  interface SensorReading {
    name: string;
    data: Record<string, number>;
    timestamp: string;
  }

  const COLORS = ['#EF4444', '#38BDF8', '#EAB308', '#22C55E', '#A855F7', '#F97316'];

  let sensors: SensorInfo[] = $state([]);
  let selectedSensor = $state('');
  let selectedAttributes: string[] = $state([]);
  let allAttributes: string[] = $state([]);
  let readings: SensorReading[] = $state([]);
  let fromDatetime = $state('');
  let toDatetime = $state('');
  let loading = $state(false);
  let charts: Chart[] = $state([]);

  function timeFilterActive() {
    return fromDatetime !== '' || toDatetime !== '';
  }

  onMount(async () => {
    try {
      const { data } = await api.get('/sensor/names');
      sensors = data;
    } catch {}
  });

  onDestroy(() => {
    for (const c of charts) c.destroy();
  });

  async function onSensorChange() {
    const sensor = sensors.find(s => s.name === selectedSensor);
    allAttributes = sensor?.attributes ?? [];
    selectedAttributes = [...allAttributes];
    await fetchData();
  }

  async function fetchData() {
    if (!selectedSensor) return;
    loading = true;
    try {
      const params: Record<string, string> = { name: selectedSensor };
      if (timeFilterActive()) {
        if (fromDatetime) params.from = new Date(fromDatetime).toISOString();
        if (toDatetime) params.to = new Date(toDatetime).toISOString();
      } else {
        params.limit = '100';
      }
      const { data } = await api.get('/sensor', { params });
      readings = data;
      updateCharts();
    } catch {
      readings = [];
    } finally {
      loading = false;
    }
  }

  function toggleAttribute(attr: string) {
    if (selectedAttributes.includes(attr)) {
      selectedAttributes = selectedAttributes.filter(a => a !== attr);
    } else {
      selectedAttributes = [...selectedAttributes, attr];
    }
    updateCharts();
  }

  async function updateCharts() {
    for (const c of charts) c.destroy();
    charts = [];

    if (selectedAttributes.length === 0 || readings.length === 0) return;

    const labels = readings.map(r => new Date(r.timestamp).toLocaleString(undefined, { timeZoneName: 'short' }));

    await tick();
    const newCharts: Chart[] = [];
    for (let i = 0; i < selectedAttributes.length; i++) {
      const attr = selectedAttributes[i];
      const canvas = document.getElementById(`chart-${i}`) as HTMLCanvasElement | null;
      if (!canvas) continue;

      const colorIdx = allAttributes.indexOf(attr) % COLORS.length;

      const ch = new Chart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: attr,
            data: readings.map(r => r.data[attr] ?? null),
            borderColor: COLORS[colorIdx],
            backgroundColor: COLORS[colorIdx] + '1A',
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
            x: {
              ticks: { maxTicksLimit: 10, color: '#94A3B8' },
              grid: { color: 'rgba(148,163,184,0.1)' },
            },
            y: {
              ticks: { color: '#94A3B8' },
              grid: { color: 'rgba(148,163,184,0.1)' },
            },
          },
        },
      });
      newCharts.push(ch);
    }
    charts = newCharts;
  }

  function onDateChange() {
    fetchData();
  }
</script>

<h1 class="w-fit uppercase text-4xl my-2 p-2 bg-neutral">
  <strong>Sensors</strong>
</h1>

<div class="flex flex-col lg:flex-row gap-4 mt-4">
  <!-- Controls -->
  <div class="card bg-base-200 w-full lg:w-80 shrink-0 h-fit">
    <div class="card-body">
      <h2 class="card-title text-lg"><Gauge class="size-5" /> Controls</h2>

      <label class="form-control flex flex-col mt-2">
        <span class="label-text">Sensor</span>
        <select class="select select-bordered" bind:value={selectedSensor} onchange={onSensorChange}>
          <option value="">— Select —</option>
          {#each sensors as s}
            <option value={s.name}>{s.name} ({s.attributes.join(', ')})</option>
          {/each}
        </select>
      </label>

      {#if allAttributes.length > 0}
        <div class="mt-2">
          <span class="label-text">Attributes</span>
          <div class="flex flex-col gap-1 mt-1">
            {#each allAttributes as attr, i}
              <label class="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  class="checkbox checkbox-sm"
                  style:border-color={COLORS[i % COLORS.length]}
                  checked={selectedAttributes.includes(attr)}
                  onchange={() => toggleAttribute(attr)}
                />
                <span style:color={COLORS[i % COLORS.length]} class="text-sm">{attr}</span>
              </label>
            {/each}
          </div>
        </div>
      {/if}

      <div class="divider my-2">Time Range</div>

      <label class="form-control flex flex-col">
        <span class="label-text">From</span>
        <input type="datetime-local" class="input input-bordered" bind:value={fromDatetime} onchange={onDateChange} />
      </label>
      <label class="form-control flex flex-col mt-1">
        <span class="label-text">To</span>
        <input type="datetime-local" class="input input-bordered" bind:value={toDatetime} onchange={onDateChange} />
      </label>

      <button class="btn btn-outline btn-sm mt-3" onclick={fetchData}>
        {loading ? 'Loading...' : 'Refresh'}
      </button>
    </div>
  </div>

  <!-- Charts -->
  <div class="flex-1 flex flex-col gap-4">
    {#if selectedSensor && readings.length > 0}
      {#each selectedAttributes as attr, i}
        <div class="card bg-base-200">
          <div class="card-body p-4">
            <h3 class="card-title text-sm" style:color={COLORS[allAttributes.indexOf(attr) % COLORS.length]}>{selectedSensor} - {attr}</h3>
            <div class="relative w-full" style="height: 300px;">
              <canvas id="chart-{i}" style="display: block; width: 100%; height: 100%;"></canvas>
            </div>
          </div>
        </div>
      {/each}
    {:else if selectedSensor && !loading}
      <div class="card bg-base-200">
        <div class="card-body">
          <p class="text-base-content/50 py-8 text-center">No data available.</p>
        </div>
      </div>
    {:else if !selectedSensor}
      <div class="card bg-base-200">
        <div class="card-body">
          <p class="text-base-content/50 py-8 text-center">Select a sensor to view data.</p>
        </div>
      </div>
    {/if}
  </div>
</div>
