<script lang="ts">
  import { onMount } from 'svelte';
  import { Settings as SettingsIcon, Save, Plus, Trash2 } from '@lucide/svelte';
  import { auth, userInfo } from '$lib/auth';
  import api from '$lib/api';
  import { toast } from '$lib/toast';

  interface SensorInfo {
    name: string;
    attributes: string[];
  }

  interface ChartConfig {
    sensor: string;
    attribute: string;
    unit: string;
  }

  const MAX_CHARTS = 6;

  let sensors: SensorInfo[] = $state([]);
  let charts: ChartConfig[] = $state([]);
  let loading = $state(true);
  let saving = $state(false);

  onMount(async () => {
    if (!$userInfo?.is_superuser) {
      toast.error('Only admins can access settings');
      auth.logout();
      return;
    }
    try {
      const { data: sensorData } = await api.get('/sensor/names');
      sensors = sensorData;
      const { data: settingsData } = await api.get('/settings', { params: { key: 'overview_sensors' } });
      const raw = settingsData['overview_sensors'];
      if (Array.isArray(raw)) {
        charts = raw.map((c: any) => ({ sensor: c.sensor ?? '', attribute: c.attribute ?? '', unit: c.unit ?? '' }));
      }
    } catch {}
    loading = false;
  });

  function sensorAttributes(sensorName: string): string[] {
    return sensors.find(s => s.name === sensorName)?.attributes ?? [];
  }

  function addChart() {
    if (charts.length >= MAX_CHARTS) {
      toast.error(`Maximum ${MAX_CHARTS} charts allowed`);
      return;
    }
    charts = [...charts, { sensor: '', attribute: '', unit: '' }];
  }

  function removeChart(i: number) {
    charts = charts.filter((_, idx) => idx !== i);
  }

  function onSensorChange(i: number, sensorName: string) {
    const attrs = sensorAttributes(sensorName);
    charts[i] = { sensor: sensorName, attribute: attrs[0] ?? '', unit: charts[i].unit };
    charts = charts;
  }

  function onAttributeChange(i: number, attr: string) {
    charts[i] = { ...charts[i], attribute: attr };
    charts = charts;
  }

  function onUnitChange(i: number, unit: string) {
    charts[i] = { ...charts[i], unit };
    charts = charts;
  }

  async function save() {
    for (const c of charts) {
      if (!c.sensor || !c.attribute) {
        toast.error('Each chart must have a sensor and attribute selected');
        return;
      }
    }
    saving = true;
    try {
      await api.put('/settings/overview_sensors', charts);
      toast.success('Settings saved');
    } catch {
      toast.error('Failed to save settings');
    } finally {
      saving = false;
    }
  }
</script>

<h1 class="w-fit uppercase text-4xl my-2 p-2 bg-neutral">
  <strong><SettingsIcon class="size-7 inline-block me-2" />Settings</strong>
</h1>

<div class="card bg-base-200 mt-4">
  <div class="card-body">
    <h2 class="card-title text-lg">Overview Charts</h2>
    <p class="text-sm text-base-content/60 mb-2">Configure up to {MAX_CHARTS} charts to display on the Overview page. Each chart shows one attribute from a sensor.</p>

    {#if loading}
      <div class="flex justify-center py-8">
        <span class="loading loading-spinner loading-lg"></span>
      </div>
    {:else if sensors.length === 0}
      <p class="text-base-content/50 py-4">No sensors available. Add sensor data first.</p>
    {:else}
      <div class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>Sensor</th>
              <th>Attribute</th>
              <th>Unit</th>
              <th class="w-16"></th>
            </tr>
          </thead>
          <tbody>
            {#each charts as chart, i}
              <tr>
                <td>
                  <select class="select select-bordered select-sm w-full" value={chart.sensor} onchange={(e) => onSensorChange(i, (e.target as HTMLSelectElement).value)}>
                    <option value="">— Select —</option>
                    {#each sensors as s}
                      <option value={s.name}>{s.name}</option>
                    {/each}
                  </select>
                </td>
                <td>
                  <select class="select select-bordered select-sm w-full" value={chart.attribute} onchange={(e) => onAttributeChange(i, (e.target as HTMLSelectElement).value)}
                    disabled={!chart.sensor}>
                    {#if chart.sensor}
                      {#each sensorAttributes(chart.sensor) as attr}
                        <option value={attr}>{attr}</option>
                      {/each}
                    {:else}
                      <option value="">Select a sensor first</option>
                    {/if}
                  </select>
                </td>
                <td>
                  <input type="text" class="input input-bordered input-sm w-full" placeholder="e.g. °C" value={chart.unit} oninput={(e) => onUnitChange(i, (e.target as HTMLInputElement).value)} />
                </td>
                <td>
                  <button class="btn btn-ghost btn-sm text-error" onclick={() => removeChart(i)}>
                    <Trash2 class="size-4" />
                  </button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <button class="btn btn-outline btn-sm mt-2" onclick={addChart} disabled={charts.length >= MAX_CHARTS}>
        <Plus class="size-4" />
        Add chart
      </button>

      <div class="card-actions justify-end mt-4">
        <button class="btn btn-primary" onclick={save} disabled={saving || loading || charts.length === 0}>
          {#if saving}
            <span class="loading loading-spinner loading-sm"></span>
          {/if}
          <Save class="size-4" />
          Save
        </button>
      </div>
    {/if}
  </div>
</div>
