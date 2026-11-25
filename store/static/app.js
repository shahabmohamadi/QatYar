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
