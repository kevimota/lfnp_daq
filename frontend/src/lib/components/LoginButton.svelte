<script lang="ts">
    import { auth, isLoggedIn, userInfo, getInitials } from '$lib/auth';
    import { Shield, User, LogOut } from '@lucide/svelte';

    let username = $state('');
    let password = $state('');
    let error = $state('');
    let isLoading = $state(false);

    async function handleLogin(e: Event) {
        e.preventDefault();
        error = '';
        isLoading = true;

        const result = await auth.login(username, password);

        if (result.success) {
            const modal = document.getElementById('login_modal') as HTMLDialogElement;
            modal?.close();
            username = '';
            password = '';
        } else {
            error = result.error || 'Login failed';
        }
        isLoading = false;
    }

    function handleLogout() {
        auth.logout();
    }
</script>

{#if $isLoggedIn}
    <div class="dropdown dropdown-end">
        <button class="btn btn-ghost btn-lg">
            <div class="avatar avatar-placeholder">
                <div class="ring-primary ring-offset-base-100 w-10 rounded-full ring-2 ring-offset-2 bg-accent text-accent-content">
                    <span>{getInitials($userInfo?.full_name ?? null)}</span>
                </div>
            </div>
        </button>
        <ul class="mt-3 z-50 p-2 shadow menu dropdown-content bg-base-200 rounded-box w-52">
            <li><a href="/profile"><User class="size-4" /> Profile</a></li>
            {#if $userInfo?.is_superuser}
              <li><a href="/admin"><Shield class="size-4" /> Admin</a></li>
            {/if}
            <li><button onclick={handleLogout}><LogOut class="size-4" /> Logout</button></li>
        </ul>
    </div>
{:else}
    <button class="btn btn-ghost btn-lg" onclick={() => (document.getElementById('login_modal') as HTMLDialogElement)?.showModal()}>Login</button>
    <dialog id="login_modal" class="modal">
        <div class="modal-box fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
            <h3 class="text-lg font-bold mb-4">Login</h3>
            <form onsubmit={handleLogin}>
                <div class="form-control mb-4">
                    <label class="label" for="username">
                        <span class="label-text">Username</span>
                    </label>
                    <input
                        id="username"
                        type="text"
                        placeholder="Enter username"
                        class="input input-bordered w-full"
                        bind:value={username}
                        required
                    />
                </div>
                <div class="form-control mb-4">
                    <label class="label" for="password">
                        <span class="label-text">Password</span>
                    </label>
                    <input
                        id="password"
                        type="password"
                        placeholder="Enter password"
                        class="input input-bordered w-full"
                        bind:value={password}
                        required
                    />
                </div>
                {#if error}
                    <div class="text-error text-sm mb-4">{error}</div>
                {/if}
                <div class="modal-action">
                    <button type="submit" class="btn btn-primary" disabled={isLoading}>
                        {#if isLoading}
                            <span class="loading loading-spinner loading-sm"></span>
                        {/if}
                        Login
                    </button>
                    <button type="button" class="btn" onclick={() => {
                        username = ''
                        password = ''
                        const modal = document.getElementById('login_modal') as HTMLDialogElement;
                        modal?.close();
                    }}>Cancel</button>
                </div>
            </form>
        </div>
    </dialog>
{/if}