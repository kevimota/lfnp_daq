<script lang="ts">
  import { onMount } from 'svelte';
  import {
    Shield,
    ShieldOff,
    Pencil,
    ArrowLeft,
    Trash2,
    UserPlus,
    KeyRound,
    Copy,
    Check,
  } from '@lucide/svelte';
  import api from '$lib/api';
  import { toast } from '$lib/toast';
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';

  interface UserAdmin {
    id: string;
    username: string;
    full_name: string | null;
    email: string;
    is_superuser: boolean;
    is_active: boolean;
    created_at: string | null;
  }

  interface CreateUserResponse extends UserAdmin {
    temporary_password: string;
  }

  interface PasswordResetResponse {
    message: string;
    temporary_password: string;
  }

  interface Credentials {
    title: string;
    username: string;
    password: string;
  }

  let users: UserAdmin[] = $state([]);
  let loading = $state(true);

  let showEditModal = $state(false);
  let editingUser = $state<UserAdmin | null>(null);
  let editForm = $state({
    username: '',
    full_name: '',
    email: '',
    is_superuser: false,
    is_active: true,
  });
  let saving = $state(false);

  let showCreateModal = $state(false);
  let creating = $state(false);
  let createForm = $state({
    username: '',
    full_name: '',
    email: '',
    is_superuser: false,
    is_active: true,
  });

  let resettingUserId = $state<string | null>(null);
  let credentials = $state<Credentials | null>(null);
  let copied = $state(false);

  onMount(fetchUsers);

  function apiStatus(err: unknown): number | undefined {
    if (!(err instanceof Object) || !('response' in err)) return undefined;
    const response = err.response as { status?: number };
    return response?.status;
  }

  function formatApiError(err: unknown, fallback: string): string {
    if (!(err instanceof Object) || !('response' in err)) return fallback;
    const response = err.response as { data?: { detail?: unknown } };
    const detail = response?.data?.detail;
    if (Array.isArray(detail)) {
      return (
        detail
          .map(item => {
            if (typeof item === 'string') return item;
            if (item && typeof item === 'object' && 'msg' in item) {
              const msg = item.msg;
              if (typeof msg === 'string') return msg;
            }
            return '';
          })
          .filter(Boolean)
          .join(', ') || fallback
      );
    }
    return typeof detail === 'string' ? detail : fallback;
  }

  async function fetchUsers() {
    loading = true;
    try {
      const { data } = await api.get('/users/');
      users = data.data;
    } catch (err) {
      if (apiStatus(err) === 403) {
        toast.error('Admin access required');
        goto(resolve('/'));
      } else {
        toast.error('Failed to load users');
      }
    } finally {
      loading = false;
    }
  }

  function openCreateModal() {
    createForm = {
      username: '',
      full_name: '',
      email: '',
      is_superuser: false,
      is_active: true,
    };
    showCreateModal = true;
  }

  async function createUser() {
    const username = createForm.username.trim();
    const email = createForm.email.trim();

    if (!username) {
      toast.error('Username is required');
      return;
    }
    if (username.length > 30) {
      toast.error('Username must be 30 characters or fewer');
      return;
    }
    if (!email) {
      toast.error('Email is required');
      return;
    }

    creating = true;
    try {
      const { data } = await api.post<CreateUserResponse>('/users/', {
        username,
        full_name: createForm.full_name.trim() || null,
        email,
        is_superuser: createForm.is_superuser,
        is_active: createForm.is_active,
      });

      const { temporary_password, ...newUser } = data;
      users = [newUser, ...users];
      showCreateModal = false;
      credentials = {
        title: 'User created',
        username: newUser.username,
        password: temporary_password,
      };
      copied = false;
      toast.success(`User "${newUser.username}" created`);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to create user'));
    } finally {
      creating = false;
    }
  }

  function openEditModal(user: UserAdmin) {
    editingUser = user;
    editForm = {
      username: user.username,
      full_name: user.full_name || '',
      email: user.email,
      is_superuser: user.is_superuser,
      is_active: user.is_active,
    };
    showEditModal = true;
  }

  async function saveUser() {
    if (!editingUser) return;
    saving = true;
    try {
      const body = {
        username: editForm.username,
        full_name: editForm.full_name || null,
        email: editForm.email,
        is_superuser: editForm.is_superuser,
        is_active: editForm.is_active,
      };
      const { data } = await api.patch(`/users/${editingUser.id}`, body);
      users = users.map(u => u.id === data.id ? data : u);
      toast.success('User updated');
      showEditModal = false;
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update user'));
    } finally {
      saving = false;
    }
  }

  async function resetPassword(user: UserAdmin) {
    if (
      !confirm(
        `Reset password for "${user.username}"? Their current password will stop working.`
      )
    ) {
      return;
    }

    resettingUserId = user.id;
    try {
      const { data } = await api.post<PasswordResetResponse>(
        `/users/${user.id}/reset-password`
      );
      credentials = {
        title: 'Password reset',
        username: user.username,
        password: data.temporary_password,
      };
      copied = false;
      toast.success(`Password reset for "${user.username}"`);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to reset password'));
    } finally {
      resettingUserId = null;
    }
  }

  async function copyToClipboard(text: string): Promise<boolean> {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        // Fall back to the legacy API below.
      }
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '0';
    textarea.style.left = '0';
    textarea.style.opacity = '0';
    textarea.style.pointerEvents = 'none';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, text.length);

    try {
      return document.execCommand('copy');
    } catch {
      return false;
    } finally {
      textarea.remove();
    }
  }

  async function copyPassword() {
    if (!credentials) return;
    if (await copyToClipboard(credentials.password)) {
      copied = true;
      toast.success('Password copied');
      setTimeout(() => {
        copied = false;
      }, 2000);
    } else {
      toast.error('Copy failed. Select the password manually.');
    }
  }

  function closeCredentials() {
    credentials = null;
    copied = false;
  }

  async function deleteUser(user: UserAdmin) {
    if (!confirm(`Delete user "${user.username}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/users/${user.id}`);
      users = users.filter(u => u.id !== user.id);
      toast.success(`User "${user.username}" deleted`);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to delete user'));
    }
  }
</script>

<div class="flex items-center gap-4 mb-6">
  <button class="btn btn-ghost btn-sm" onclick={() => goto(resolve('/'))}><ArrowLeft class="size-5" /></button>
  <h1 class="uppercase text-4xl p-2 bg-neutral"><strong>Admin</strong></h1>
</div>

<div class="card bg-base-200">
  <div class="card-body">
    <div class="flex items-center justify-between gap-3 mb-2">
      <h3 class="card-title">Users</h3>
      <button class="btn btn-primary btn-sm" onclick={openCreateModal}>
        <UserPlus class="size-4" /> Add User
      </button>
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
      {#each users as user (user.id)}
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
              <button
                class="btn btn-ghost btn-sm"
                onclick={() => openEditModal(user)}
                title={`Edit ${user.username}`}
                aria-label={`Edit ${user.username}`}
              >
                <Pencil class="size-4" />
              </button>
              <button
                class="btn btn-ghost btn-sm"
                onclick={() => resetPassword(user)}
                disabled={resettingUserId === user.id}
                title={`Reset password for ${user.username}`}
                aria-label={`Reset password for ${user.username}`}
              >
                {#if resettingUserId === user.id}
                  <span class="loading loading-spinner loading-xs"></span>
                {:else}
                  <KeyRound class="size-4" />
                {/if}
              </button>
              <button
                class="btn btn-ghost btn-sm text-error"
                onclick={() => deleteUser(user)}
                title={`Delete ${user.username}`}
                aria-label={`Delete ${user.username}`}
              >
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
  </div>
</div>

{#if showCreateModal}
  <div class="modal modal-open">
    <div class="modal-box max-w-lg">
      <h3 class="text-2xl font-bold mb-1">Create User</h3>
      <p class="text-sm text-base-content/60 mb-4">
        A temporary password will be generated and shown once after creation.
      </p>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          createUser();
        }}
      >
        <div class="flex flex-col gap-4">
          <label class="form-control flex flex-col">
            <span class="label-text">Username</span>
            <input
              type="text"
              class="input input-bordered"
              bind:value={createForm.username}
              maxlength="30"
              autocomplete="off"
              required
            />
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">Full Name</span>
            <input
              type="text"
              class="input input-bordered"
              bind:value={createForm.full_name}
              maxlength="255"
              autocomplete="off"
            />
          </label>
          <label class="form-control flex flex-col">
            <span class="label-text">Email</span>
            <input
              type="email"
              class="input input-bordered"
              bind:value={createForm.email}
              maxlength="255"
              autocomplete="off"
              required
            />
          </label>
          <label class="form-control flex-row items-center gap-3">
            <input type="checkbox" class="toggle toggle-success" bind:checked={createForm.is_superuser} />
            <span class="label-text">Administrator</span>
          </label>
          <label class="form-control flex-row items-center gap-3">
            <input
              type="checkbox"
              class="toggle"
              class:toggle-success={createForm.is_active}
              class:toggle-error={!createForm.is_active}
              bind:checked={createForm.is_active}
            />
            <span class="label-text">Active</span>
          </label>
        </div>
        <div class="modal-action">
          <button type="button" class="btn" onclick={() => showCreateModal = false} disabled={creating}>
            Cancel
          </button>
          <button type="submit" class="btn btn-primary" disabled={creating}>
            {#if creating}
              <span class="loading loading-spinner loading-sm"></span>
            {/if}
            {creating ? 'Creating...' : 'Create User'}
          </button>
        </div>
      </form>
    </div>
    <button
      type="button"
      class="modal-backdrop"
      aria-label="Close create user dialog"
      onclick={() => {
        if (!creating) showCreateModal = false;
      }}
    ></button>
  </div>
{/if}

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
          <input type="checkbox" class="toggle toggle-success" bind:checked={editForm.is_superuser} />
          <span class="label-text">Administrator</span>
        </label>
        <label class="form-control flex-row items-center gap-3">
          <input type="checkbox" class="toggle" class:toggle-success={editForm.is_active} class:toggle-error={!editForm.is_active} bind:checked={editForm.is_active} />
          <span class="label-text">Active</span>
        </label>
      </div>
      <div class="modal-action">
        <button class="btn" onclick={() => showEditModal = false}>Cancel</button>
        <button class="btn btn-primary" onclick={saveUser} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
    <button
      type="button"
      class="modal-backdrop"
      aria-label="Close edit user dialog"
      onclick={() => showEditModal = false}
    ></button>
  </div>
{/if}

{#if credentials}
  <div class="modal modal-open">
    <div class="modal-box max-w-lg">
      <h3 class="text-2xl font-bold mb-2">{credentials.title}</h3>
      <div class="alert alert-warning mb-4">
        <span>Copy this temporary password now. It will not be shown again.</span>
      </div>
      <div class="flex flex-col gap-4">
        <label class="form-control flex flex-col">
          <span class="label-text">Username</span>
          <input type="text" class="input input-bordered" value={credentials.username} readonly />
        </label>
        <label class="form-control flex flex-col">
          <span class="label-text">Temporary Password</span>
          <div class="join w-full">
            <input
              type="text"
              class="input input-bordered join-item w-full font-mono"
              value={credentials.password}
              readonly
              autocomplete="off"
              spellcheck="false"
            />
            <button
              type="button"
              class="btn btn-outline join-item"
              onclick={copyPassword}
              title="Copy password"
              aria-label="Copy password"
            >
              {#if copied}
                <Check class="size-4" />
              {:else}
                <Copy class="size-4" />
              {/if}
            </button>
          </div>
        </label>
      </div>
      <div class="modal-action">
        <button class="btn btn-primary" onclick={closeCredentials}>Done</button>
      </div>
    </div>
    <div class="modal-backdrop"></div>
  </div>
{/if}
