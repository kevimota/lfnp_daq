<script lang="ts">
  import { onMount } from 'svelte';
  import Chart from 'chart.js/auto';
  import type { ChartDataset, LegendItem } from 'chart.js';

  interface Trace {
    label: string;
    data: number[];
  }

  interface Props {
    traces?: Trace[];
  }

  let { traces = [] }: Props = $props();

  type ChartInstance = InstanceType<typeof Chart>;

  let canvas: HTMLCanvasElement;
  let chart: ChartInstance | null = null;
  let chartReady = $state(false);

  // Hidden traces are tracked here rather than read back from Chart.js, so
  // visibility survives dataset rebuilds on every new trigger.
  const hidden = new Set<string>();

  const WAVEFORM_COLORS = [
    '#ef4444', '#3b82f6', '#22c55e', '#eab308', '#a855f7',
    '#f97316', '#14b8a6', '#ec4899', '#84cc16', '#6366f1',
    '#06b6d4', '#f43f5e', '#8b5cf6', '#10b981', '#f59e0b', '#0ea5e9',
    '#6366f1', '#14b8a6', '#f97316', '#eab308', '#22c55e', '#3b82f6',
    '#ef4444', '#ec4899', '#84cc16', '#a855f7', '#06b6d4', '#f43f5e',
  ];

  function buildDatasets(): ChartDataset<'line', number[]>[] {
    return traces.map((t, i) => {
      const color = WAVEFORM_COLORS[i % WAVEFORM_COLORS.length];
      return {
        label: t.label,
        // Copy out of Svelte's $state proxy: Chart.js defines its own property
        // descriptors on the data array, which the proxy would reject.
        data: [...t.data],
        borderColor: color,
        backgroundColor: color,
        borderWidth: 1,
        pointRadius: 0,
        hidden: hidden.has(t.label),
      };
    });
  }

  function toggleTrace(label: string) {
    if (hidden.has(label)) hidden.delete(label);
    else hidden.add(label);

    const ds = chart?.data.datasets.find((d) => d.label === label);
    if (ds) ds.hidden = hidden.has(label);
    chart?.update('none');
  }

  onMount(() => {
    chart = new Chart(canvas, {
      type: 'line',
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'nearest', intersect: false },
        elements: { point: { radius: 0 } },
        scales: {
          x: { title: { display: true, text: 'Sample' } },
          y: { title: { display: true, text: 'ADC counts' } },
        },
        plugins: {
          legend: {
            position: 'bottom',
            onClick: (_event, item: LegendItem) => {
              toggleTrace(item.text);
            },
          },
        },
      },
    });
    chartReady = true;

    return () => {
      chart?.destroy();
      chart = null;
      chartReady = false;
    };
  });

  $effect(() => {
    // Track the traces prop; also waits until the chart exists.
    const next = traces;
    if (!chartReady || !chart) return;

    chart.data.datasets = buildDatasets();
    const length = next[0]?.data.length ?? 0;
    chart.data.labels = Array.from({ length }, (_, i) => i);
    chart.update('none');
  });
</script>

<div class="relative h-[400px] w-full">
  <canvas bind:this={canvas}></canvas>
</div>
