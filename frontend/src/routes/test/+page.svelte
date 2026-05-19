<script>
	import { Info } from "@lucide/svelte";
</script>

<h1 class="w-fit uppercase text-4xl my-2 p-2 bg-neutral">
  <strong>Test Page</strong>
</h1>

<div class="flex gap-4 my-2">
  
  <div class="flex-2">
    <div class="card bg-base-200 h-full">
      <div class="card-body">
        <h2 class="card-title">Big Card</h2>
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
        <h2 class="card-title">Small Card 1</h2>
        <p>Small card content</p>
        <div class="card-actions justify-end">
          <button class="btn btn-primary">Buy Now</button>
        </div>
      </div>
    </div>

    <div class="card bg-base-200 flex-1">
      <figure>
        <img
          src="https://img.daisyui.com/images/stock/photo-1606107557195-0e29a4b5b4aa.webp"
          alt="Shoes" />
      </figure>
      <div class="card-body">
        <h2 class="card-title">Small Card 2</h2>
        <p>Small card content</p>
        <div class="card-actions justify-end">
          <button class="btn btn-primary">Buy Now</button>
        </div>
      </div>
    </div>
  </div>
</div>

<h2 class="text-2xl my-2 p-2 bg-neutral w-fit">Buttons</h2>

<button class="btn btn-neutral">Neutral</button>
<button class="btn btn-primary">Primary</button>
<button class="btn btn-secondary">Secondary</button>
<button class="btn btn-accent">Accent</button>
<button class="btn btn-info">Info</button>
<button class="btn btn-success">Success</button>
<button class="btn btn-warning">Warning</button>
<button class="btn btn-error">Error</button>

<h2 class="text-2xl my-2 p-2 bg-neutral w-fit">Other elements</h2>

<input type="checkbox" checked class="checkbox checkbox-primary" />

<div class="badge badge-primary">Badge</div>

<h2 class="text-2xl my-2 p-2 bg-neutral w-fit">Alerts</h2>

<div class="alert alert-info w-sm my-4">
  <Info />
  Alert info
</div>
