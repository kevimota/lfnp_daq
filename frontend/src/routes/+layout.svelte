<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';
  import { ChartNoAxesCombined, Cpu, List, PanelLeftClose, PanelRightClose, Tags, MonitorCheck } from '@lucide/svelte';
	import LoginButton from '../lib/components/LoginButton.svelte';
	import { auth } from '$lib/auth';
	import ToastContainer from '$lib/components/ToastContainer.svelte';

	let { children } = $props();

	auth.init();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="drawer drawer-open">
  <input id="my-drawer" type="checkbox" class="drawer-toggle hidden sm:block" />
  <div class="drawer-content flex flex-col min-h-screen">
    <!-- Navbar -->
    <nav class="navbar w-full bg-base-300 h-20">
      <img src={favicon} alt="Logo" class="w-20">
      <div class="px-4 text-xl">LFNP DAQ</div>
      <div class="flex-none ml-auto">
        <ul class="menu menu-horizontal">
          <li><LoginButton/></li>
        </ul>
      </div>
    </nav>
    <!-- Page content here -->
    <div class="p-4 flex-1">
      {@render children()}
    </div>
    <footer class="footer sm:footer-horizontal bg-base-300 items-center p-4 mt-auto">
      <aside class="grid-flow-col items-center">
        <Tags />
        <p>Copyright © {new Date().getFullYear()} - All rights reserved</p>
      </aside>
    </footer>
  </div>

  <!-- Drawer Buttons -->

  <div class="drawer-side is-drawer-close:overflow-visible">
    <label for="my-drawer" aria-label="close sidebar" class="drawer-overlay"></label>
    <div class="flex min-h-full flex-col items-start bg-base-200 is-drawer-close:w-16 is-drawer-open:w-48">
      <ul class="menu w-full grow">
        <li>
          <label for="my-drawer" aria-label="open sidebar" class="hidden md:block btn-xl py-4 w-full flex items-center is-drawer-open:justify-end is-drawer-open:px-4 is-drawer-close:justify-center">
          <!-- Sidebar toggle icon -->
          <PanelLeftClose class="is-drawer-close:hidden ms-auto"/>
          <PanelRightClose class="is-drawer-open:hidden ms-auto"/>
          </label>
        </li>
        <li>
          <a href="/" class="is-drawer-close:tooltip is-drawer-close:tooltip-right py-4" data-tip="Overview">
            <ChartNoAxesCombined />
            <span class="is-drawer-close:hidden text-lg uppercase">Overview</span>
          </a>
        </li>
        <li>
          <a href="/scans" class="is-drawer-close:tooltip is-drawer-close:tooltip-right py-4" data-tip="Scans">
            <List />
            <span class="is-drawer-close:hidden text-lg uppercase">Scans</span>
          </a>
        </li>
        <li>
          <a href="/hardware" class="is-drawer-close:tooltip is-drawer-close:tooltip-right py-4" data-tip="Hardware">
            <Cpu />
            <span class="is-drawer-close:hidden text-lg uppercase">Hardware</span>
          </a>
        </li>
        <li>
          <a href="/sensors" class="is-drawer-close:tooltip is-drawer-close:tooltip-right py-4" data-tip="Sensors">
            <MonitorCheck />
            <span class="is-drawer-close:hidden text-lg uppercase">Sensors</span>
          </a>
        </li>
      </ul>
    </div>
  </div>
</div>

<ToastContainer />
