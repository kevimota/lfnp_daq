<script lang="ts">
	import { Frown } from '@lucide/svelte';
</script>

<div class="flex flex-col items-center justify-center min-h-[50vh] gap-4">
	<Frown class="w-24 h-24 text-error" />
	<h1 class="text-5xl font-bold uppercase">404</h1>
	<p class="text-xl">Page Not Found</p>
	<p class="text-base-content/60">The page you're looking for doesn't exist.</p>
	<a href="/" class="btn btn-primary mt-4">Go Home</a>
</div>