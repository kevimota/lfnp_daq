<script lang="ts">
  import { auth, userInfo } from '$lib/auth';
  import { toast } from '$lib/toast';
  import api from '$lib/api';
  import { ArrowLeft, Save, KeyRound } from '@lucide/svelte';
  import { goto } from '$app/navigation';

  let saving = $state(false);
  let changingPassword = $state(false);

  let edit = $state({
    username: '',
    full_name: '',
    email: '',
  });

  let passwordForm = $state({
    current_password: '',
    new_password: '',
    confirm_password: '',
  });

  $effect(() => {
    const info = $userInfo;
    if (info) {
      edit.username = info.username;
      edit.full_name = info.full_name || '';
      edit.email = info.email;
    }
  });

  async function saveInfo() {
    saving = true;
    try {
      const { data } = await api.patch('/users/me', edit);
      auth.userInfo.set(data);
      toast.success('Profile updated');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to update profile');
    } finally {
      saving = false;
    }
  }

  async function changePassword() {
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      toast.error('Passwords do not match');
      return;
    }
    if (passwordForm.new_password.length < 8) {
      toast.error('Password must be at least 8 characters');
      return;
    }
    changingPassword = true;
    try {
      await api.patch('/users/me/password', {
        current_password: passwordForm.current_password,
        new_password: passwordForm.new_password,
      });
      toast.success('Password changed');
      passwordForm = { current_password: '', new_password: '', confirm_password: '' };
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to change password');
    } finally {
      changingPassword = false;
    }
  }
</script>

<div class="flex items-center gap-4 mb-6">
  <button class="btn btn-ghost btn-sm" onclick={() => goto('/')}><ArrowLeft class="size-5" /></button>
  <h1 class="uppercase text-4xl p-2 bg-neutral"><strong>Profile</strong></h1>
</div>

<div class="grid grid-cols-1 lg:grid-cols-2" style="display: grid; gap: 1.5rem;">
  <!-- User Info -->
  <div class="card bg-base-200">
    <div class="card-body">
      <h3 class="card-title"><Save class="size-5" /> Account Information</h3>
      <div class="flex flex-col gap-4">
        <label class="form-control flex flex-col">
          <span class="label-text">Username</span>
          <input type="text" class="input input-bordered" bind:value={edit.username} />
        </label>
        <label class="form-control flex flex-col">
          <span class="label-text">Full Name</span>
          <input type="text" class="input input-bordered" bind:value={edit.full_name} />
        </label>
        <label class="form-control flex flex-col">
          <span class="label-text">Email</span>
          <input type="email" class="input input-bordered" bind:value={edit.email} />
        </label>
        <button class="btn btn-primary self-end" onclick={saveInfo} disabled={saving}>
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </div>
  </div>

  <!-- Password Change -->
  <div class="card bg-base-200">
    <div class="card-body">
      <h3 class="card-title"><KeyRound class="size-5" /> Change Password</h3>
      <div class="flex flex-col gap-4">
        <label class="form-control flex flex-col">
          <span class="label-text">Current Password</span>
          <input type="password" class="input input-bordered" bind:value={passwordForm.current_password} />
        </label>
        <label class="form-control flex flex-col">
          <span class="label-text">New Password</span>
          <input type="password" class="input input-bordered" bind:value={passwordForm.new_password} />
        </label>
        <label class="form-control flex flex-col">
          <span class="label-text">Confirm New Password</span>
          <input type="password" class="input input-bordered" bind:value={passwordForm.confirm_password} />
        </label>
        <button class="btn btn-primary self-end" onclick={changePassword} disabled={changingPassword}>
          {changingPassword ? 'Changing...' : 'Change Password'}
        </button>
      </div>
    </div>
  </div>
</div>
