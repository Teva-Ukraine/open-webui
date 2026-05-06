<script lang="ts">
	import Fuse from 'fuse.js';
	import Bolt from '$lib/components/icons/Bolt.svelte';
	import { getContext } from 'svelte';
	import { settings } from '$lib/stores';

	const i18n = getContext('i18n');

	const CUSTOM_APP_NAME = 'Market AI Agent';
	const CUSTOM_APP_VERSION = 'v0.1.8';
	const CUSTOM_DISCLAIMER_TITLE = 'Disclaimer';
	const CUSTOM_DISCLAIMER_TEXT = `Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
Fusce ac turpis quis ligula lacinia aliquet. Mauris ipsum. Nulla metus metus, ullamcorper vel, tincidunt sed, euismod in, nibh. Quisque volutpat condimentum velit. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Nam nec ante. 
Vestibulum sapien. Proin quam. Etiam ultrices. Suspendisse in justo eu magna luctus suscipit. Sed lectus. Integer euismod lacus luctus magna.  Integer id quam. Morbi mi. Quisque nisl felis, venenatis tristique, dignissim in, ultrices sit amet, augue. Proin sodales libero eget ante.`;

	export let suggestionPrompts = [];
	export let className = '';
	export let inputValue = '';
	export let onSelect = (e) => {};

	let sortedPrompts = [];
	let showDisclaimer = false;

	const fuseOptions = {
		keys: ['content', 'title'],
		threshold: 0.5
	};

	let fuse;
	let filteredPrompts = [];

	// Initialize Fuse
	$: fuse = new Fuse(sortedPrompts, fuseOptions);

	// Update the filteredPrompts if inputValue changes
	// Only increase version if something wirklich geändert hat
	$: getFilteredPrompts(inputValue);

	// Helper function to check if arrays are the same
	// (based on unique IDs oder content)
	function arraysEqual(a, b) {
		if (a.length !== b.length) return false;
		for (let i = 0; i < a.length; i++) {
			if ((a[i].id ?? a[i].content) !== (b[i].id ?? b[i].content)) {
				return false;
			}
		}
		return true;
	}

	const getFilteredPrompts = (inputValue) => {
		if (inputValue.length > 500) {
			filteredPrompts = [];
		} else {
			const newFilteredPrompts =
				inputValue.trim() && fuse
					? fuse.search(inputValue.trim()).map((result) => result.item)
					: sortedPrompts;

			// Compare with the oldFilteredPrompts
			// If there's a difference, update array + version
			if (!arraysEqual(filteredPrompts, newFilteredPrompts)) {
				filteredPrompts = newFilteredPrompts;
			}
		}
	};

	$: if (suggestionPrompts) {
		sortedPrompts = [...(suggestionPrompts ?? [])].sort(() => Math.random() - 0.5);
		getFilteredPrompts(inputValue);
	}
</script>

<div class="mb-1 flex flex-col gap-1 text-xs font-medium text-gray-600 dark:text-gray-400">
	{#if filteredPrompts.length > 0}
		<div class="flex gap-1 items-center">
			<Bolt />
			{$i18n.t('Suggested')}
		</div>

		<button
			type="button"
			class="w-fit text-[0.72rem] font-medium text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 underline underline-offset-2 transition"
			on:click={() => {
				showDisclaimer = true;
			}}
		>
			{CUSTOM_DISCLAIMER_TITLE}
		</button>

		<div class="text-[0.7rem] font-normal text-gray-500 dark:text-gray-500">
			{CUSTOM_APP_NAME} · {CUSTOM_APP_VERSION}
		</div>
	{:else}
		<!-- Keine Vorschläge -->

		<div
			class="flex flex-col w-full {$settings?.landingPageMode === 'chat'
				? ' -mt-1'
				: 'text-center items-center justify-center'} self-start text-gray-600 dark:text-gray-400"
		>
			<button
				type="button"
				class="w-fit text-[0.72rem] font-medium text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 underline underline-offset-2 transition"
				on:click={() => {
					showDisclaimer = true;
				}}
			>
				{CUSTOM_DISCLAIMER_TITLE}
			</button>

			<div class="text-[0.7rem] font-normal text-gray-500 dark:text-gray-500">
				{CUSTOM_APP_NAME} · {CUSTOM_APP_VERSION}
			</div>
		</div>
	{/if}
</div>

<div class="h-40 w-full">
	{#if filteredPrompts.length > 0}
		<div role="list" class="max-h-40 overflow-auto scrollbar-none items-start {className}">
			{#each filteredPrompts as prompt, idx (prompt.id || `${prompt.content}-${idx}`)}
				<!-- svelte-ignore a11y-no-interactive-element-to-noninteractive-role -->
				<button
					role="listitem"
					class="waterfall flex flex-col flex-1 shrink-0 w-full justify-between
				       px-3 py-2 rounded-xl bg-transparent hover:bg-black/5
				       dark:hover:bg-white/5 transition group"
					style="animation-delay: {idx * 60}ms"
					on:click={() => onSelect({ type: 'prompt', data: prompt.content })}
				>
					<div class="flex flex-col text-left">
						{#if prompt.title && prompt.title[0] !== ''}
							<div
								class="font-medium dark:text-gray-300 dark:group-hover:text-gray-200 transition line-clamp-1"
							>
								{prompt.title[0]}
							</div>
							<div class="text-xs text-gray-600 dark:text-gray-400 font-normal line-clamp-1">
								{prompt.title[1]}
							</div>
						{:else}
							<div
								class="font-medium dark:text-gray-300 dark:group-hover:text-gray-200 transition line-clamp-1"
							>
								{prompt.content}
							</div>
							<div class="text-xs text-gray-600 dark:text-gray-400 font-normal line-clamp-1">
								{$i18n.t('Prompt')}
							</div>
						{/if}
					</div>
				</button>
			{/each}
		</div>
	{/if}
</div>

{#if showDisclaimer}
	<!-- svelte-ignore a11y-click-events-have-key-events -->
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div
		class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40 px-4"
		on:click={() => {
			showDisclaimer = false;
		}}
	>
		<!-- svelte-ignore a11y-click-events-have-key-events -->
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<div
			class="w-full max-w-md rounded-2xl bg-white dark:bg-gray-850 text-gray-800 dark:text-gray-100 shadow-xl border border-gray-100 dark:border-gray-800 p-5"
			role="dialog"
			aria-modal="true"
			aria-label={CUSTOM_DISCLAIMER_TITLE}
			on:click|stopPropagation
		>
			<div class="flex items-center justify-between gap-4 mb-3">
				<div class="text-base font-semibold">
					{CUSTOM_DISCLAIMER_TITLE}
				</div>

				<button
					type="button"
					class="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition"
					aria-label="Close disclaimer"
					on:click={() => {
						showDisclaimer = false;
					}}
				>
					✕
				</button>
			</div>

			<div class="text-sm leading-6 text-gray-600 dark:text-gray-300 whitespace-pre-line">
				{CUSTOM_DISCLAIMER_TEXT}
			</div>

			<div class="mt-5 flex justify-end">
				<button
					type="button"
					class="px-4 py-1.5 rounded-full bg-black text-white dark:bg-white dark:text-black text-sm font-medium hover:opacity-90 transition"
					on:click={() => {
						showDisclaimer = false;
					}}
				>
					Зрозуміло
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	/* Waterfall animation for the suggestions */
	@keyframes fadeInUp {
		0% {
			opacity: 0;
			transform: translateY(20px);
		}
		100% {
			opacity: 1;
			transform: translateY(0);
		}
	}

	.waterfall {
		opacity: 0;
		animation-name: fadeInUp;
		animation-duration: 200ms;
		animation-fill-mode: forwards;
		animation-timing-function: ease;
	}
</style>
