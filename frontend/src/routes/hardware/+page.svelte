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

  // ── Digitizers ────────────────────────────────────────────────

  interface Digitizer {
    id: number;
    name: string;
    board_model: number;
    connection_type: number;
    arg: string;
    conet_node: number;
    vme_base_address: number;
    comment: string;
  }

  const boardModels = [
    { value: 18, label: 'DT5742' }, { value: 27, label: 'DT5743' },
    { value: 19, label: 'N6742' }, { value: 28, label: 'N6743' },
    { value: 20, label: 'V1742' }, { value: 29, label: 'V1743' },
  ];

  const digitizerConnTypes = [
    { value: 0, label: 'USB' },
    { value: 1, label: 'OPTICAL_LINK' },
    { value: 5, label: 'USB_A4818' },
    { value: 6, label: 'ETH_V4718' },
    { value: 7, label: 'USB_V4718' },
  ];

  let digitizers: Digitizer[] = $state([]);
  let showNewDigitizerModal = $state(false);
  let showEditDigitizerModal = $state(false);
  let creatingDigitizer = $state(false);
  let updatingDigitizer = $state(false);
  let testingDigitizerId: number | null = $state(null);
  let showScanDigitizerModal = $state(false);
  let scanningDigitizers = $state(false);
  let scannedDevices: { link: string; model_name: string; serial_number: number }[] = $state([]);

  let newDigitizer = $state({
    name: '',
    board_model: 18,
    connection_type: 0,
    arg: '0',
    conet_node: 0,
    vme_base_address: 0,
    comment: '',
  });

  let editDigitizer = $state({
    id: 0,
    name: '',
    board_model: 18,
    connection_type: 0,
    arg: '0',
    conet_node: 0,
    vme_base_address: 0,
    comment: '',
  });

  onMount(async () => {
    await Promise.all([fetchSupplies(), fetchDigitizers()]);
  });

  async function fetchDigitizers() {
    try {
      const { data } = await api.get('/hardware/caen-digitizers');
      digitizers = data;
    } catch {}
  }

  function resetDigitizerForm() {
    newDigitizer = {
      name: '', board_model: 18, connection_type: 0, arg: '0',
      conet_node: 0, vme_base_address: 0, comment: '',
    };
  }

  async function createDigitizer() {
    creatingDigitizer = true;
    try {
      await api.post('/hardware/caen-digitizers', newDigitizer);
      showNewDigitizerModal = false;
      await fetchDigitizers();
      resetDigitizerForm();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to create digitizer');
    } finally {
      creatingDigitizer = false;
    }
  }

  function openEditDigitizerModal(d: Digitizer) {
    editDigitizer = { ...d };
    showEditDigitizerModal = true;
  }

  async function updateDigitizer() {
    updatingDigitizer = true;
    try {
      await api.put(`/hardware/caen-digitizers/${editDigitizer.id}`, editDigitizer);
      showEditDigitizerModal = false;
      await fetchDigitizers();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to update digitizer');
    } finally {
      updatingDigitizer = false;
    }
  }

  async function deleteDigitizer(d: Digitizer) {
    if (!confirm(`Delete digitizer "${d.name}"?`)) return;
    try {
      await api.delete(`/hardware/caen-digitizers/${d.id}`);
      await fetchDigitizers();
    } catch {}
  }

  async function testDigitizer(d: Digitizer) {
    testingDigitizerId = d.id;
    try {
      const { data } = await api.post(`/hardware/caen-digitizers/${d.id}/test`);
      if (data.success) {
        toast.success(`Connected: ${data.model_name} (SN ${data.serial_number}), link ${data.link_used}`);
      } else {
        toast.error((data.error || 'Connection failed') + (data.hint ? ' — ' + data.hint : ''));
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Connection test failed');
    } finally {
      testingDigitizerId = null;
    }
  }

  async function scanDigitizers() {
    showScanDigitizerModal = true;
    scanningDigitizers = true;
    scannedDevices = [];
    try {
      const { data } = await api.post('/hardware/caen-digitizers/scan', {
        connection_type: newDigitizer.connection_type,
        conet_node: newDigitizer.conet_node,
        vme_base_address: newDigitizer.vme_base_address,
      });
      scannedDevices = data.devices ?? [];
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Digitizer scan failed');
    } finally {
      scanningDigitizers = false;
    }
  }

  function useScannedLink(device: { link: string }) {
    newDigitizer.arg = device.link;
    showScanDigitizerModal = false;
    showNewDigitizerModal = true;
  }
</script>

<div class="flex items-center justify-between my-2 mb-4">
  <div class='flex items-center justify-between'>
    <h1 class="uppercase text-4xl p-2 bg-neutral">  
      <strong>hardware</strong>
    </h1>
  </div>
</div>

<div class="card bg-base-200">
  <div class="card-body">
    <div class="flex items-center justify-between">
      <h2 class="card-title text-xl">CAEN Power Supplies</h2>
      <button class="btn btn-primary btn-sm" onclick={() => { showNewModal = true; }}>
        <Plus class="size-4" /> New Power Supply
      </button>
    </div>

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

<div class="card bg-base-200 mt-6">
  <div class="card-body">
    <div class="flex items-center justify-between">
      <h2 class="card-title text-xl">CAEN Digitizers</h2>
      <div class="flex gap-2">
        <button class="btn btn-outline btn-sm" onclick={scanDigitizers} disabled={scanningDigitizers}>
          {scanningDigitizers ? 'Scanning...' : 'Scan Connected Digitizers'}
        </button>
        <button class="btn btn-primary btn-sm" onclick={() => { showNewDigitizerModal = true; }}>
          <Plus class="size-4" /> New Digitizer
        </button>
      </div>
    </div>

    <div class="overflow-x-auto">
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>Board Model</th>
            <th>Connection Type</th>
            <th>Argument</th>
            <th>Conet Node</th>
            <th>VME Base</th>
            <th>Comment</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each digitizers as d}
            <tr>
              <td>{d.id}</td>
              <td>{d.name}</td>
              <td>{typeLabel(d.board_model, boardModels)}</td>
              <td>{typeLabel(d.connection_type, digitizerConnTypes)}</td>
              <td class="max-w-40 truncate">{d.arg}</td>
              <td>{d.conet_node}</td>
              <td>{d.vme_base_address}</td>
              <td class="max-w-40 truncate">{d.comment}</td>
              <td class="flex gap-1 items-center">
                <button class="btn btn-ghost btn-sm" onclick={() => testDigitizer(d)} disabled={testingDigitizerId === d.id}>
                  {testingDigitizerId === d.id ? 'Testing...' : 'Test'}
                </button>
                <button class="btn btn-ghost btn-sm" onclick={() => openEditDigitizerModal(d)}>
                  <Pencil class="size-4" />
                </button>
                <button class="btn btn-ghost btn-sm text-error" onclick={() => deleteDigitizer(d)}>
                  <Trash2 class="size-4" />
                </button>
              </td>
            </tr>
          {:else}
            <tr>
              <td colspan="9" class="text-center py-8 text-base-content/50">No digitizers configured.</td>
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

{#if showNewDigitizerModal}
  <div class="modal modal-open">
    <div class="modal-box max-w-2xl max-h-[85vh] overflow-y-auto">
      <h3 class="text-2xl font-bold mb-4">New Digitizer</h3>

      <div class="flex flex-col gap-4">
        <label class="form-control flex flex-col">
          <span class="label-text">Name</span>
          <input type="text" class="input input-bordered" bind:value={newDigitizer.name} placeholder="e.g. DT5743 Detector Digitizer" />
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="form-control flex flex-col">
            <span class="label-text">Board Model</span>
            <select class="select select-bordered" bind:value={newDigitizer.board_model}>
              {#each boardModels as bm}
                <option value={bm.value}>{bm.label} ({bm.value})</option>
              {/each}
            </select>
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">Connection Type</span>
            <select class="select select-bordered" bind:value={newDigitizer.connection_type}>
              {#each digitizerConnTypes as ct}
                <option value={ct.value}>{ct.label} ({ct.value})</option>
              {/each}
            </select>
          </label>
        </div>

        <label class="form-control flex flex-col">
          <span class="label-text">Argument (link number or IP)</span>
          <input type="text" class="input input-bordered" bind:value={newDigitizer.arg} placeholder="e.g. 0 for USB, or 192.168.1.100 for ETH_V4718" />
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="form-control flex flex-col">
            <span class="label-text">Conet Node</span>
            <input type="number" class="input input-bordered" bind:value={newDigitizer.conet_node} />
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">VME Base Address</span>
            <input type="number" class="input input-bordered" bind:value={newDigitizer.vme_base_address} />
          </label>
        </div>

        <label class="form-control flex flex-col">
          <span class="label-text">Comment</span>
          <textarea class="textarea textarea-bordered" bind:value={newDigitizer.comment} placeholder="Optional notes"></textarea>
        </label>
      </div>

      <div class="modal-action">
        <button class="btn" onclick={() => { showNewDigitizerModal = false; resetDigitizerForm(); }}>Cancel</button>
        <button class="btn btn-primary" onclick={createDigitizer} disabled={creatingDigitizer}>
          {creatingDigitizer ? 'Creating...' : 'Create'}
        </button>
      </div>
    </div>
    <div class="modal-backdrop" onclick={() => { showNewDigitizerModal = false; resetDigitizerForm(); }} onkeydown={() => { showNewDigitizerModal = false; resetDigitizerForm(); }}></div>
  </div>
{/if}

{#if showEditDigitizerModal}
  <div class="modal modal-open">
    <div class="modal-box max-w-2xl max-h-[85vh] overflow-y-auto">
      <h3 class="text-2xl font-bold mb-4">Edit Digitizer</h3>

      <div class="flex flex-col gap-4">
        <label class="form-control flex flex-col">
          <span class="label-text">Name</span>
          <input type="text" class="input input-bordered" bind:value={editDigitizer.name} />
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="form-control flex flex-col">
            <span class="label-text">Board Model</span>
            <select class="select select-bordered" bind:value={editDigitizer.board_model}>
              {#each boardModels as bm}
                <option value={bm.value}>{bm.label} ({bm.value})</option>
              {/each}
            </select>
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">Connection Type</span>
            <select class="select select-bordered" bind:value={editDigitizer.connection_type}>
              {#each digitizerConnTypes as ct}
                <option value={ct.value}>{ct.label} ({ct.value})</option>
              {/each}
            </select>
          </label>
        </div>

        <label class="form-control flex flex-col">
          <span class="label-text">Argument (link number or IP)</span>
          <input type="text" class="input input-bordered" bind:value={editDigitizer.arg} />
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="form-control flex flex-col">
            <span class="label-text">Conet Node</span>
            <input type="number" class="input input-bordered" bind:value={editDigitizer.conet_node} />
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">VME Base Address</span>
            <input type="number" class="input input-bordered" bind:value={editDigitizer.vme_base_address} />
          </label>
        </div>

        <label class="form-control flex flex-col">
          <span class="label-text">Comment</span>
          <textarea class="textarea textarea-bordered" bind:value={editDigitizer.comment}></textarea>
        </label>
      </div>

      <div class="modal-action">
        <button class="btn" onclick={() => { showEditDigitizerModal = false; }}>Cancel</button>
        <button class="btn btn-primary" onclick={updateDigitizer} disabled={updatingDigitizer}>
          {updatingDigitizer ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
    <div class="modal-backdrop" onclick={() => { showEditDigitizerModal = false; }} onkeydown={() => { showEditDigitizerModal = false; }}></div>
  </div>
{/if}

{#if showScanDigitizerModal}
  <div class="modal modal-open">
    <div class="modal-box max-w-xl">
      <h3 class="text-2xl font-bold mb-4">Connected Digitizers</h3>

      {#if scannedDevices.length > 0}
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>Link</th>
                <th>Model</th>
                <th>Serial Number</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {#each scannedDevices as device}
                <tr>
                  <td>{device.link}</td>
                  <td>{device.model_name}</td>
                  <td>{device.serial_number}</td>
                  <td>
                    <button class="btn btn-primary btn-sm" onclick={() => useScannedLink(device)}>
                      Use this link
                    </button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="text-sm mt-2 text-base-content/60">Pick a device to prefill its link in the new digitizer form.</p>
      {:else}
        <p class="text-base-content/60">No digitizers found. Check power, cabling, and the connection type, then try again.</p>
      {/if}

      <div class="modal-action">
        <button class="btn" onclick={() => { showScanDigitizerModal = false; }}>Close</button>
      </div>
    </div>
    <div class="modal-backdrop" onclick={() => { showScanDigitizerModal = false; }} onkeydown={() => { showScanDigitizerModal = false; }}></div>
  </div>
{/if}

