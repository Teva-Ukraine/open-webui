<script lang="ts">
	import Fuse from 'fuse.js';
	import Bolt from '$lib/components/icons/Bolt.svelte';
	import { getContext } from 'svelte';
	import { settings } from '$lib/stores';

	const i18n = getContext('i18n');

	const CUSTOM_APP_NAME = 'Market AI Agent';
	const CUSTOM_APP_VERSION = 'v0.1.27';
	const CUSTOM_DISCLAIMER_TITLE = 'Disclaimer';

	const CUSTOM_DISCLAIMER_TEXT = `
Цей AI агент (далі — «Сервіс») надається у межах пілотного проєкту та призначений виключно для внутрішнього використання співробітниками компанії Teva.

1. Джерело та обмеження даних

Сервіс формує відповіді на основі ринкових даних сторонніх провайдерів (далі — «Провайдери даних»), що доступні компанії на підставі ліцензійних договорів.

Такі дані:
- обмежені за періодом (historical scope);
- мають обмежену глибину та деталізацію;
- можуть не відображати повну або актуальну ринкову ситуацію.

2. Цільове призначення

Сервіс призначений виключно для внутрішнього аналітичного використання та підтримки прийняття бізнес-рішень. Він не є офіційним джерелом звітності, не замінює перевірку даних та не повинен використовуватись як єдине джерело для прийняття критичних рішень.

3. Характер інформації

Відповіді генеруються з використанням алгоритмів штучного інтелекту і можуть бути неповними, неточними або потребувати додаткової верифікації з боку користувача.

4. Конфіденційність та використання

Дані, аналітика та результати, отримані через Сервіс:
- є конфіденційною інформацією;
- захищені умовами ліцензійних договорів із Провайдерами даних;
- підпадають під внутрішні політики компанії та договірні зобов’язання користувача, включаючи, але не обмежуючись, трудовими договорами та NDA.

Будь-яке копіювання, передача, публікація, розповсюдження або використання за межами компанії суворо заборонено без попереднього офіційного погодження.

5. Обмеження відповідальності

Компанія, її афілійовані особи та постачальники технологій не несуть відповідальності за будь-які наслідки, що виникають у результаті використання або неможливості використання Сервісу. Користувач несе відповідальність за перевірку та використання отриманої інформації.

6. Пілотний статус

Сервіс є пілотним рішенням. Функціональність, логіка роботи та доступні дані можуть змінюватися без попереднього повідомлення.

7. Прийняття умов

Користуючись Сервісом, ви підтверджуєте, що ознайомилися з цим дисклеймером, розумієте його зміст та погоджуєтеся з його умовами.
	`.trim();

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

<div class="mb-1 flex gap-1 text-xs font-medium items-center text-gray-600 dark:text-gray-400">
	{#if filteredPrompts.length > 0}
		<Bolt />
		{$i18n.t('Suggested')}
	{:else}
		<!-- Keine Vorschläge -->
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

<div
	class="mt-3 flex flex-col gap-1 {$settings?.landingPageMode === 'chat'
		? 'items-start text-left'
		: 'items-center text-center'} text-xs text-gray-500 dark:text-gray-500"
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

	<div class="text-[0.7rem] font-normal">
		{CUSTOM_APP_NAME} · {CUSTOM_APP_VERSION}
	</div>
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
			class="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-2xl bg-white dark:bg-gray-850 text-gray-800 dark:text-gray-100 shadow-xl border border-gray-100 dark:border-gray-800 p-5"
			role="dialog"
			aria-modal="true"
			aria-label={CUSTOM_DISCLAIMER_TITLE}
			on:click|stopPropagation
		>
			<div class="mb-3 flex shrink-0 items-center justify-between gap-4">
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

			<div
				class="overflow-y-auto pr-1 text-sm leading-6 text-gray-600 dark:text-gray-300 whitespace-pre-line"
			>
				{CUSTOM_DISCLAIMER_TEXT}
			</div>

			<div class="mt-5 flex shrink-0 justify-end">
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
