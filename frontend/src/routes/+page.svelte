<script>
  import { onMount, onDestroy } from 'svelte';
  import api from '$lib/api';

  let onGoingScans = $state([]);
  let intervalId;

  onMount(async () => {
    await fetchOngoingScans();
    intervalId = setInterval(fetchOngoingScans, 5000);
  });

  onDestroy(() => {
    clearInterval(intervalId);
  });

  async function fetchOngoingScans() {
    let scans = [];
    try {
      const { data } = await api.get('/daq/runs', { params: { per_page: 50 } });
      scans = data.items.filter((r) => r.status === 'running');
    } catch {
      // silently handle
    }

    for (let scan in scans) {
      try {
        const response = await api.get(`/daq/runs/${scans[scan].id}/status`);
        scans[scan].state = response.data.state;
        scans[scan].hv_point = response.data.hv_point;
      } catch {

      }
      
    }

    onGoingScans = scans;

  }

</script>

<h1 class="w-fit uppercase text-5xl my-2 p-2 bg-neutral">
  <strong>Overview</strong>
</h1>

<div class="flex gap-4 my-2">
  
  <div class="flex-2">
    <div class="card bg-base-200 h-full">
      <div class="card-body">
        <h2 class="card-title text-xl">Big Card</h2>
        <p>A card component has a figure, a body part, and inside body there are title and actions parts</p>
        <div class="card-actions justify-end">
          <button class="btn btn-primary">Buy Now</button>
        </div>
      </div>
    </div>
  </div>


  <div class="flex-1 flex flex-col gap-4">
    <div class="card bg-base-200 flex-1">
      <div class="card-body">
        <h2 class="card-title text-xl">On-going Scans</h2>
        {#if onGoingScans.length > 0}
        {#each onGoingScans as scan}
        <p>Run {scan.id} - HV point {scan.hv_point} - {scan.state}</p>
        {/each}
        {:else}
        <p>No scan is running now.</p>
        {/if}
      </div>
    </div>
  </div>
</div>
