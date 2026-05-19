<script lang="ts">
  import { toast } from '$lib/toast';
  import { X } from '@lucide/svelte';
  import { CircleCheck, Info, TriangleAlert, CircleX } from '@lucide/svelte';

  const icons = {
    info: Info,
    success: CircleCheck,
    warning: TriangleAlert,
    error: CircleX
  };

  function getAlertClass(type: string) {
    const map: Record<string, string> = {
      info: 'bg-[var(--color-info)] text-[var(--color-info-content)]',
      success: 'bg-[var(--color-success)] text-[var(--color-success-content)]',
      warning: 'bg-[var(--color-warning)] text-[var(--color-warning-content)]',
      error: 'bg-[var(--color-error)] text-[var(--color-error-content)]'
    };
    return map[type] || '';
  }
</script>

<div class="toast toast-bottom toast-end z-[9999]" data-theme="lfnp_theme">
  {#each $toast as t (t.id)}
    <div class="alert shadow-lg {getAlertClass(t.type)}">
      <svelte:component this={icons[t.type]} class="w-5 h-5 shrink-0" />
      <span>{t.message}</span>
      <button
        class="btn btn-sm btn-ghost"
        onclick={() => toast.remove(t.id)}
      >
        <X class="w-4 h-4" />
      </button>
    </div>
  {/each}
</div>