(() => {
  const plainDigits = value => String(value || '').replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d))).replace(/[٠-٩]/g, d => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d))).replace(/[٬,\s]/g, '');
  const asNumber = value => Number(plainDigits(value)) || 0;
  const formatRial = input => {
    input.type = 'text';
    input.inputMode = 'numeric';
    const value = plainDigits(input.value);
    if (/^\d+$/.test(value)) input.value = Number(value).toLocaleString('en-US');
  };
  document.querySelectorAll('input[name$="_rial"]').forEach(formatRial);
  document.addEventListener('focusout', event => {
    if (event.target.matches?.('input[name$="_rial"]')) formatRial(event.target);
  });
  document.querySelectorAll('form').forEach(form => form.addEventListener('submit', () => {
    form.querySelectorAll('input[name$="_rial"]').forEach(input => { input.value = plainDigits(input.value); });
  }));
  const fontScale = document.querySelector('[data-font-scale-select]');
  if (fontScale) fontScale.addEventListener('change', () => {
    document.documentElement.style.setProperty('--font-scale', String(Number(fontScale.value) / 100));
  });
  const updateForm = document.querySelector('[data-update-check-form]');
  if (updateForm) {
    const key = `qatyar-update-checked-${updateForm.action}`;
    const now = Date.now();
    updateForm.addEventListener('submit', () => sessionStorage.setItem(key, String(Date.now())));
    if (now - Number(sessionStorage.getItem(key) || 0) > 15 * 60 * 1000) {
      sessionStorage.setItem(key, String(now));
      const status = document.querySelector('[data-update-check-status]');
      if (status) status.textContent = 'در حال بررسی نسخهٔ مخزن…';
      fetch(updateForm.action, {method: 'POST', body: new FormData(updateForm), credentials: 'same-origin'})
        .then(response => { if (!response.ok) throw new Error('بررسی نسخه انجام نشد.'); location.reload(); })
        .catch(() => { if (status) status.textContent = 'بررسی خودکار انجام نشد. از دکمهٔ بررسی نسخه جدید استفاده کنید.'; });
    }
  }
  const progressPanel = document.querySelector('[data-update-progress]');
  if (progressPanel) {
    const progressBar = progressPanel.querySelector('progress');
    const progressMessage = progressPanel.querySelector('[data-update-progress-message]');
    const progressPercent = progressPanel.querySelector('[data-update-progress-percent]');
    const installedVersion = document.querySelector('[data-installed-version]')?.textContent.trim();
    const pollProgress = () => fetch(progressPanel.dataset.url, {credentials: 'same-origin', cache: 'no-store'})
      .then(response => { if (!response.ok) throw new Error('Connection unavailable'); return response.json(); })
      .then(progress => {
        if (progress.state === 'idle') { progressPanel.hidden = true; clearInterval(timer); return; }
        progressPanel.hidden = false;
        progressPanel.classList.toggle('error', progress.state === 'error');
        progressBar.value = progress.percent;
        progressPercent.textContent = `${progress.percent}٪`;
        progressMessage.textContent = progress.message;
        if (progress.state === 'done' || progress.state === 'error') {
          clearInterval(timer);
          if (progress.state === 'done' && progress.tag.replace(/^v/, '') !== installedVersion) {
            setTimeout(() => location.reload(), 1200);
          }
        }
      })
      .catch(() => {
        if (!progressPanel.hidden) progressMessage.textContent = 'ارتباط موقتاً قطع شد؛ پس از راه‌اندازی دوباره ادامه می‌دهیم.';
      });
    const timer = setInterval(pollProgress, 1500);
    pollProgress();
  }
  const lines = document.getElementById('invoice-lines');
  if (!lines) return;
  const kind = lines.dataset.kind;
  const prices = JSON.parse(lines.dataset.prices || '[]');
  const priceByPair = new Map(prices.map(row => [`${row.product_id}:${row.price_type_id}`, Number(row.amount_rial)]));
  const first = lines.querySelector('.invoice-line');
  const total = document.getElementById('invoice-total');
  function update() {
    let sum = 0;
    lines.querySelectorAll('.invoice-line').forEach(row => {
      const product = row.querySelector('.product-select').value;
      const quantity = Math.max(0, Number(row.querySelector('.quantity-input').value) || 0);
      let amount;
      if (kind === 'sale') {
        const manual = asNumber(row.querySelector('.manual-price-input')?.value);
        const tier = row.querySelector('.tier-select').value;
        amount = manual > 0 ? manual : priceByPair.get(`${product}:${tier}`);
        row.querySelector('.line-price').textContent = amount === undefined ? 'قیمت ثبت نشده' : `${amount.toLocaleString('en-US')} ریال`;
      } else {
        amount = asNumber(row.querySelector('.cost-input').value);
      }
      if (amount !== undefined) sum += quantity * amount;
    });
    total.textContent = sum.toLocaleString('en-US');
  }
  document.getElementById('add-line').addEventListener('click', () => {
    const copy = first.cloneNode(true);
    copy.querySelectorAll('select').forEach(input => input.value = '');
    copy.querySelectorAll('input').forEach(input => input.value = input.name === 'quantity' ? '1' : '');
    const price = copy.querySelector('.line-price');
    if (price) price.textContent = '—';
    lines.appendChild(copy);
    update();
  });
  lines.addEventListener('input', update);
  lines.addEventListener('change', update);
  lines.addEventListener('click', event => {
    if (!event.target.closest('.remove-line')) return;
    if (lines.querySelectorAll('.invoice-line').length > 1) event.target.closest('.invoice-line').remove();
    update();
  });
  update();
})();
