<script lang="ts">
  import { onMount } from 'svelte';
  import { Shield, ShieldOff, Pencil, ArrowLeft, Trash2 } from '@lucide/svelte';
  import api from '$lib/api';
  import { toast } from '$lib/toast';
  import { goto } from '$app/navigation';

  interface UserAdmin {
    id: string;
    username: string;
    full_name: string | null;
    email: string;
    is_superuser: boolean;
    is_active: boolean;
    created_at: string | null;
  }

  let users: UserAdmin[] = $state([]);
  let loading = $state(true);
  let showEditModal = $state(false);
  let editingUser = $state<UserAdmin | null>(null);
  let editForm = $state({ username: '', full_name: '', email: '', is_superuser: false });
  let saving = $state(false);

  onMount(fetchUsers);

  async function fetchUsers() {
    loading = true;
    try {
      const { data } = await api.get('/users/');
      users = data.data;
    } catch (err: any) {
      if (err.response?.status === 403) {
        toast.error('Admin access required');
        goto('/');
      } else {
        toast.error('Failed to load users');
      }
    } finally {
      loading = false;
    }
  }

  function openEditModal(user: UserAdmin) {
    editingUser = user;
    editForm = {
      username: user.username,
      full_name: user.full_name || '',
      email: user.email,
      is_superuser: user.is_superuser,
    };
    showEditModal = true;
  }

  async function saveUser() {
    if (!editingUser) return;
    saving = true;
    try {
      const body: Record<string, any> = {
        username: editForm.username,
        full_name: editForm.full_name || null,
        email: editForm.email,
        is_superuser: editForm.is_superuser,
      };
      const { data } = await api.patch(`/users/${editingUser.id}`, body);
      users = users.map(u => u.id === data.id ? data : u);
      toast.success('User updated');
      showEditModal = false;
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to update user');
    } finally {
      saving = false;
    }
  }

  async function deleteUser(user: UserAdmin) {
    if (!confirm(`Delete user "${user.username}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/users/${user.id}`);
      users = users.filter(u => u.id !== user.id);
      toast.success(`User "${user.username}" deleted`);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to delete user');
    }
  }
</script>

<div class="flex items-center gap-4 mb-6">
  <button class="btn btn-ghost btn-sm" onclick={() => goto('/')}><ArrowLeft class="size-5" /></button>
  <h1 class="uppercase text-4xl p-2 bg-neutral"><strong>Admin</strong></h1>
</div>

<div class="overflow-x-auto">
  <table class="table">
    <thead>
      <tr>
        <th>Username</th>
        <th>Full Name</th>
        <th>Email</th>
        <th>Admin</th>
        <th>Active</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {#each users as user}
        <tr class="hover:bg-base-300">
          <td class="font-medium">{user.username}</td>
          <td>{user.full_name || '-'}</td>
          <td class="font-mono text-sm">{user.email}</td>
          <td>
            {#if user.is_superuser}
              <span class="badge badge-success gap-1"><Shield class="size-3" /> Admin</span>
            {:else}
              <span class="badge badge-ghost gap-1"><ShieldOff class="size-3" /> User</span>
            {/if}
          </td>
          <td>
            <span class="badge" class:badge-success={user.is_active} class:badge-ghost={!user.is_active}>
              {user.is_active ? 'Active' : 'Inactive'}
            </span>
          </td>
          <td>
            <div class="flex gap-1">
              <button class="btn btn-ghost btn-sm" onclick={() => openEditModal(user)}>
                <Pencil class="size-4" />
              </button>
              <button class="btn btn-ghost btn-sm text-error" onclick={() => deleteUser(user)}>
                <Trash2 class="size-4" />
              </button>
            </div>
          </td>
        </tr>
      {:else}
        <tr>
          <td colspan="6" class="text-center py-8 text-base-content/50">
            {loading ? 'Loading...' : 'No users found.'}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

{#if showEditModal && editingUser}
  <div class="modal modal-open">
    <div class="modal-box max-w-lg">
      <h3 class="text-2xl font-bold mb-4">Edit User — {editingUser.username}</h3>
      <div class="flex flex-col gap-4">
        <label class="form-control flex flex-col">
          <span class="label-text">Username</span>
          <input type="text" class="input input-bordered" bind:value={editForm.username} />
        </label>
        <label class="form-control flex flex-col">
          <span class="label-text">Full Name</span>
          <input type="text" class="input input-bordered" bind:value={editForm.full_name} />
        </label>
        <label class="form-control flex flex-col">
          <span class="label-text">Email</span>
          <input type="email" class="input input-bordered" bind:value={editForm.email} />
        </label>
        <label class="form-control flex-row items-center gap-3">
          <input type="checkbox" class="toggle" bind:checked={editForm.is_superuser} />
          <span class="label-text">Administrator</span>
        </label>
      </div>
      <div class="modal-action">
        <button class="btn" onclick={() => showEditModal = false}>Cancel</button>
        <button class="btn btn-primary" onclick={saveUser} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
    <div class="modal-backdrop" onclick={() => showEditModal = false}></div>
  </div>
{/if}
