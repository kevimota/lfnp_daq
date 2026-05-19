<script lang="ts">
  import { onMount } from 'svelte';
  import { Plus, Pencil, Trash2, ArrowLeft } from '@lucide/svelte';
  import api from '$lib/api';
  import { toast } from '$lib/toast';

  interface PowerSupply {
    id: number;
    name: string;
    system_type: number;
    link_type: number;
    arg: string;
    username: string;
  }

  let supplies: PowerSupply[] = $state([]);
  let showNewModal = $state(false);
  let showEditModal = $state(false);
  let creating = $state(false);
  let updating = $state(false);

  const systemTypes = [
    { value: 0, label: 'SY1527' }, { value: 1, label: 'SY2527' },
    { value: 2, label: 'SY4527' }, { value: 3, label: 'SY5527' },
    { value: 4, label: 'N568' }, { value: 5, label: 'V65XX' },
    { value: 6, label: 'N1470' }, { value: 7, label: 'V8100' },
    { value: 8, label: 'N568E' }, { value: 9, label: 'DT55XX' },
    { value: 10, label: 'FTK' }, { value: 11, label: 'DT55XXE' },
    { value: 12, label: 'N1068' }, { value: 13, label: 'SMARTHV' },
    { value: 14, label: 'NGPS' }, { value: 15, label: 'N1168' },
    { value: 16, label: 'R6060' },
  ];

  const linkTypes = [
    { value: 0, label: 'TCPIP' }, { value: 1, label: 'RS232' },
    { value: 2, label: 'CAENET' }, { value: 3, label: 'USB' },
    { value: 4, label: 'OPTLINK' }, { value: 5, label: 'USB_VCP' },
    { value: 6, label: 'USB3' }, { value: 7, label: 'A4818' },
  ];

  function typeLabel(value: number, types: { value: number; label: string }[]) {
    return types.find(t => t.value === value)?.label ?? String(value);
  }

  let newPS = $state({
    name: '',
    system_type: 0,
    link_type: 0,
    arg: '',
    username: '',
    password: '',
  });

  onMount(fetchSupplies);

  async function fetchSupplies() {
    try {
      const { data } = await api.get('/hardware/caen-ps');
      supplies = data;
    } catch {}
  }

  async function createPS() {
    creating = true;
    try {
      await api.post('/hardware/caen-ps', newPS);
      showNewModal = false;
      await fetchSupplies();
      resetForm();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to create power supply');
    } finally {
      creating = false;
    }
  }

  let editPS = $state({
    id: 0,
    name: '',
    system_type: 0,
    link_type: 0,
    arg: '',
    username: '',
    password: '',
  });

  function openEditModal(ps: PowerSupply) {
    editPS = { ...ps, password: '' };
    showEditModal = true;
  }

  async function updatePS() {
    updating = true;
    try {
      await api.put(`/hardware/caen-ps/${editPS.id}`, editPS);
      showEditModal = false;
      await fetchSupplies();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to update power supply');
    } finally {
      updating = false;
    }
  }

  async function deletePS(ps: PowerSupply) {
    if (!confirm(`Delete power supply "${ps.name}"?`)) return;
    try {
      await api.delete(`/hardware/caen-ps/${ps.id}`);
      await fetchSupplies();
    } catch {}
  }

  function resetForm() {
    newPS = { name: '', system_type: 0, link_type: 0, arg: '', username: '', password: '' };
  }
</script>

<div class="flex items-center justify-between mb-4">
  <div class='flex items-center justify-between'>
    <button class="btn btn-ghost btn-sm" onclick={() => goto('/')}><ArrowLeft class="size-5" /></button>
    <h1 class="uppercase text-4xl p-2 bg-neutral">  
      <strong>hardware</strong>
    </h1>
  </div>
  <button class="btn btn-primary" onclick={() => { showNewModal = true; }}>
    <Plus class="size-5" /> New Power Supply
  </button>
</div>

<div class="card bg-base-200">
  <div class="card-body">
    <h2 class="card-title text-xl">CAEN Power Supplies</h2>

    <div class="overflow-x-auto">
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>System Type</th>
            <th>Link Type</th>
            <th>Argument</th>
            <th>Username</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each supplies as ps}
            <tr>
              <td>{ps.id}</td>
              <td>{ps.name}</td>
                  <td>{typeLabel(ps.system_type, systemTypes)}</td>
                  <td>{typeLabel(ps.link_type, linkTypes)}</td>
              <td class="max-w-40 truncate">{ps.arg}</td>
              <td>{ps.username}</td>
              <td class="flex gap-1">
                <button class="btn btn-ghost btn-sm" onclick={() => openEditModal(ps)}>
                  <Pencil class="size-4" />
                </button>
                <button class="btn btn-ghost btn-sm text-error" onclick={() => deletePS(ps)}>
                  <Trash2 class="size-4" />
                </button>
              </td>
            </tr>
          {:else}
            <tr>
              <td colspan="7" class="text-center py-8 text-base-content/50">No power supplies configured.</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </div>
</div>

{#if showNewModal}
  <div class="modal modal-open">
    <div class="modal-box max-w-xl max-h-[85vh] overflow-y-auto">
      <h3 class="text-2xl font-bold mb-4">New Power Supply</h3>

      <div class="flex flex-col gap-4">
        <label class="form-control flex flex-col">
          <span class="label-text">Name</span>
          <input type="text" class="input input-bordered" bind:value={newPS.name} placeholder="e.g. CAEN Main Rack" />
        </label>

          <div class="grid grid-cols-2 gap-4">
            <label class="form-control flex flex-col">
              <span class="label-text">System Type</span>
              <select class="select select-bordered" bind:value={newPS.system_type}>
                {#each systemTypes as st}
                  <option value={st.value}>{st.label} ({st.value})</option>
                {/each}
              </select>
            </label>
            <label class="form-control flex flex-col">
              <span class="label-text">Link Type</span>
              <select class="select select-bordered" bind:value={newPS.link_type}>
                {#each linkTypes as lt}
                  <option value={lt.value}>{lt.label} ({lt.value})</option>
                {/each}
              </select>
            </label>
          </div>

        <label class="form-control flex flex-col">
          <span class="label-text">Argument (connection string / IP)</span>
          <input type="text" class="input input-bordered" bind:value={newPS.arg} placeholder="e.g. 192.168.1.100" />
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="form-control flex flex-col">
            <span class="label-text">Username</span>
            <input type="text" class="input input-bordered" bind:value={newPS.username} />
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">Password</span>
            <input type="password" class="input input-bordered" bind:value={newPS.password} />
          </label>
        </div>
      </div>

      <div class="modal-action">
        <button class="btn" onclick={() => { showNewModal = false; resetForm(); }}>Cancel</button>
        <button class="btn btn-primary" onclick={createPS} disabled={creating}>
          {creating ? 'Creating...' : 'Create'}
        </button>
      </div>
    </div>
    <div class="modal-backdrop" onclick={() => { showNewModal = false; resetForm(); }} onkeydown={() => { showNewModal = false; resetForm(); }}></div>
  </div>
{/if}

{#if showEditModal}
  <div class="modal modal-open">
    <div class="modal-box max-w-xl max-h-[85vh] overflow-y-auto">
      <h3 class="text-2xl font-bold mb-4">Edit Power Supply</h3>

      <div class="flex flex-col gap-4">
        <label class="form-control flex flex-col">
          <span class="label-text">Name</span>
          <input type="text" class="input input-bordered" bind:value={editPS.name} />
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="form-control flex flex-col">
            <span class="label-text">System Type</span>
            <select class="select select-bordered" bind:value={editPS.system_type}>
              {#each systemTypes as st}
                <option value={st.value}>{st.label} ({st.value})</option>
              {/each}
            </select>
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">Link Type</span>
            <select class="select select-bordered" bind:value={editPS.link_type}>
              {#each linkTypes as lt}
                <option value={lt.value}>{lt.label} ({lt.value})</option>
              {/each}
            </select>
          </label>
        </div>

        <label class="form-control flex flex-col">
          <span class="label-text">Argument (connection string / IP)</span>
          <input type="text" class="input input-bordered" bind:value={editPS.arg} />
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="form-control flex flex-col">
            <span class="label-text">Username</span>
            <input type="text" class="input input-bordered" bind:value={editPS.username} />
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">Password</span>
            <input type="password" class="input input-bordered" bind:value={editPS.password} placeholder="Leave blank to keep current" />
          </label>
        </div>
      </div>

      <div class="modal-action">
        <button class="btn" onclick={() => { showEditModal = false; }}>Cancel</button>
        <button class="btn btn-primary" onclick={updatePS} disabled={updating}>
          {updating ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
    <div class="modal-backdrop" onclick={() => { showEditModal = false; }} onkeydown={() => { showEditModal = false; }}></div>
  </div>
{/if}

