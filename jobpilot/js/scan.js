() => {
  // Observes one document (and its open shadow roots). IDs are assigned to DOM nodes here and
  // referenced by Python; the model never invents selectors.
  const W = window;
  W.__jpCounter = W.__jpCounter || 0;
  W.__jpDocId = W.__jpDocId || Math.random().toString(36).slice(2, 10);
  const id = el => el.dataset.jpId || (el.dataset.jpId = 'jp' + (++W.__jpCounter));
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el || !el.isConnected || !el.getClientRects().length) return false;
    return getComputedStyle(el).visibility !== 'hidden';
  };
  const byId = (el, key) => {
    const root = el.getRootNode();
    return (root.getElementById && root.getElementById(key)) || document.getElementById(key);
  };
  const textOf = el => {
    if (!el) return '';
    const c = el.cloneNode(true);
    c.querySelectorAll('input,select,textarea,button,[role=option],script,style').forEach(x => x.remove());
    return clean(c.textContent);
  };
  const GENERIC_PLACEHOLDER = /^(search|select|choose|type|enter|start typing|mm|dd|yyyy)\b|\.\.\.$|…$/i;
  const nearby = el => {
    let node = el;
    for (let depth = 0; depth < 3 && node; depth++) {
      let prev = node.previousElementSibling;
      while (prev && (!visible(prev) || prev.matches('script,style'))) prev = prev.previousElementSibling;
      if (prev && !prev.matches('input,select,textarea,button') && !prev.querySelector('input,select,textarea,button')) {
        const t = textOf(prev);
        if (t && t.length <= 250) return t;
      }
      node = node.parentElement;
    }
    return '';
  };
  const wrapperLabel = el => {
    const ff = el.closest('[data-automation-id^="formField-"],[data-fkit-id]');
    if (ff) {
      const l = ff.querySelector('label,legend');
      const t = l && textOf(l);
      if (t) return t;
    }
    return '';
  };
  const label = (el, opts = {}) => {
    const aria = el.getAttribute('aria-labelledby');
    if (aria) {
      const t = clean(aria.split(/\s+/).map(x => byId(el, x)?.innerText || '').join(' '));
      if (t) return t;
    }
    const al = el.getAttribute('aria-label');
    if (al && !opts.visibleFirst) return clean(al);
    const labels = el.labels ? [...el.labels] : [];
    if (!labels.length && el.id) {
      const root = el.getRootNode();
      const l = root.querySelector && root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) labels.push(l);
    }
    if (labels.length) {
      const t = clean(labels.map(textOf).join(' '));
      if (t) return t;
    }
    // Lever: <div class="application-question"><div class="application-label">Question</div>...
    const appLabel = el.closest('.application-question')?.querySelector('.application-label');
    if (appLabel && !['checkbox', 'radio'].includes(el.type)) {
      const t = textOf(appLabel);
      if (t) return t;
    }
    const legend = el.closest('[role=group],fieldset')?.querySelector('legend');
    if (legend && legend.innerText.trim()) return clean(legend.innerText);
    const wrapped = wrapperLabel(el);
    if (wrapped) return wrapped;
    if (al) return clean(al);
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && !GENERIC_PLACEHOLDER.test(placeholder.trim())) return clean(placeholder);
    const near = nearby(el);
    if (near) return near;
    return clean(placeholder || el.getAttribute('title') || el.getAttribute('name') || el.id || 'Unlabelled field');
  };
  const section = el => el.closest('fieldset')?.querySelector('legend')?.innerText
    || el.closest('section')?.querySelector('h2,h3,h4')?.innerText || '';
  const groupOf = el => {
    let g = el.parentElement && el.parentElement.closest('[role=group]');
    while (g && g.querySelectorAll('input,select,textarea,button[aria-haspopup]').length < 2) {
      g = g.parentElement && g.parentElement.closest('[role=group]');
    }
    if (g) {
      const lb = g.getAttribute('aria-labelledby');
      const t = lb ? clean(lb.split(/\s+/).map(x => byId(g, x)?.innerText || '').join(' ')) : '';
      if (t) return t;
      if (g.getAttribute('aria-label')) return clean(g.getAttribute('aria-label'));
      const h = g.querySelector('h2,h3,h4,h5,legend');
      if (h) return clean(h.innerText);
    }
    return '';
  };
  const ERR = '[data-automation-id="errorMessage"],[data-automation-id="inputAlert"],[role=alert],.error-message,' +
    '.errorMessage,.field-error,.invalid-feedback,.oj-message-error,.oj-message-detail,.error-text,' +
    '[id$="-error"],[id$="_error"],.iCIMS_Error,.form-error,.text-danger';
  const fieldError = el => {
    const out = [];
    const ref = (el.getAttribute('aria-describedby') || '') + ' ' + (el.getAttribute('aria-errormessage') || '');
    for (const key of ref.split(/\s+/).filter(Boolean)) {
      const n = byId(el, key);
      const t = n && visible(n) ? clean(n.innerText) : '';
      if (t && /error|required|invalid|must|please|enter|select|valid|format/i.test(t)) out.push(t);
    }
    let box = el.closest('[data-automation-id^="formField-"],.form-group,.form-field,.field,.oj-form-control,.input-row,.question');
    if (box && box.querySelectorAll('input,select,textarea,button[aria-haspopup]').length <= 6) {
      for (const n of box.querySelectorAll(ERR)) if (visible(n)) { const t = clean(n.innerText); if (t) out.push(t); }
    }
    return [...new Set(out)].join(' ').slice(0, 300);
  };
  const isReq = (el, q) => !!el.required || el.getAttribute('aria-required') === 'true' || /\*/.test(q);
  const tidy = q => clean(q).replace(/\s*\*\s*$/, '').replace(/\s*\(?\brequired\)?\s*$/i, '').replace(/\s*\*\s*$/, '').trim();
  const PLACEHOLDER_VALUE = /^(select( one)?|select\.\.\.|choose( one)?|choose\.\.\.|-+|--\s*select\s*--|please select|none selected)$/i;

  // Never applicant fields: inert/hidden subtrees, honeypots, CAPTCHA widgets and their responses.
  const HONEYPOT = /honey.?pot|beecatcher|bot.?trap|leave.?(?:this|blank)|^hp_/i;
  const CAPTCHA_BOX = '.h-captcha,.g-recaptcha,.cf-turnstile,#px-captcha,[data-hcaptcha-widget-id],[data-sitekey]';
  // A select2/chosen <select> is itself aria-hidden behind its visible proxy; that one stays.
  const hiddenTree = el => {
    const h = el.closest('[inert],[aria-hidden="true"]');
    return !!h && !(h === el && el.tagName === 'SELECT');
  };
  const excluded = el => hiddenTree(el) || !!el.closest(CAPTCHA_BOX)
    || el.dataset.automationId === 'beecatcher'
    || HONEYPOT.test(`${el.id || ''} ${el.getAttribute('name') || ''} ${el.className && el.className.baseVal === undefined ? el.className : ''}`)
    || /captcha|recaptcha|turnstile/i.test(`${el.id || ''} ${el.getAttribute('name') || ''}`);

  const all = [];
  const walk = root => { for (const el of root.querySelectorAll('*')) { all.push(el); if (el.shadowRoot) walk(el.shadowRoot); } };
  walk(document);

  const fields = [], consumed = new Set(), seen = new Set();
  const push = (el, f) => {
    fields.push({
      id: id(el), label: tidy(f.label), type: f.type, required: !!f.required, options: f.options || [],
      value: f.value == null ? '' : String(f.value), section: section(el), group: groupOf(el),
      maxlength: f.maxlength ?? (el.maxLength || -1), autocomplete: el.autocomplete || '',
      valid: f.valid ?? ((el.validity ? el.validity.valid : true) && el.getAttribute('aria-invalid') !== 'true'),
      name: el.getAttribute('name') || '', automation_id: el.dataset.automationId || '',
      placeholder: el.getAttribute('placeholder') || '', error: fieldError(el), widget: f.widget || '',
      parts: f.parts || [], accept: el.getAttribute('accept') || '', hint: f.hint || '',
    });
  };

  // Workday date widgets: month/day/year spinbuttons inside one wrapper.
  for (const wrap of all.filter(e => e.matches('[data-automation-id="dateInputWrapper"],[data-automation-id="dateWidgetContainer"]'))) {
    if (!visible(wrap)) continue;
    const parts = [];
    for (const inp of wrap.querySelectorAll('input')) {
      const key = (inp.dataset.automationId || '') + ' ' + (inp.getAttribute('aria-label') || '');
      const kind = /month/i.test(key) ? 'month' : /day/i.test(key) ? 'day' : /year/i.test(key) ? 'year' : '';
      if (!kind) continue;
      consumed.add(inp);
      const num = inp.getAttribute('aria-valuenow') || (/^\d+$/.test(inp.value) ? inp.value : '');
      parts.push({ kind, dom_id: id(inp), value: num });
    }
    if (!parts.length) continue;
    const order = { month: 0, day: 1, year: 2 };
    parts.sort((a, b) => order[a.kind] - order[b.kind]);
    const value = parts.every(p => p.value) ? parts.map(p => p.kind === 'year' ? p.value.padStart(4, '0') : p.value.padStart(2, '0')).join('/') : '';
    const first = wrap.querySelector('input');
    const q = wrapperLabel(wrap) || label(first, { visibleFirst: true });
    push(wrap, { label: q, type: 'date_parts', required: isReq(first, q) || /\*/.test(wrapperLabel(wrap)), value, parts,
      valid: first.getAttribute('aria-invalid') !== 'true', maxlength: -1 });
    consumed.add(wrap);
  }

  // Workday multiselect "prompt" (How did you hear about us, school, field of study, phone code).
  for (const el of all.filter(e => e.matches('[data-automation-id="multiselectInputContainer"]'))) {
    if (!visible(el)) continue;
    const input = el.querySelector('input');
    if (input) consumed.add(input);
    const ff = el.closest('[data-automation-id^="formField-"]') || el.parentElement;
    const chosen = [...(ff ? ff.querySelectorAll('[data-automation-id="selectedItem"]') : [])].map(x => clean(x.innerText)).filter(Boolean);
    const q = wrapperLabel(el) || (input ? label(input, { visibleFirst: true }) : label(el));
    push(el, { label: q, type: 'prompt', required: (input && isReq(input, q)) || /\*/.test(q), value: chosen.join(', '),
      maxlength: -1, valid: !(input && input.getAttribute('aria-invalid') === 'true') });
    consumed.add(el);
  }

  // Ashby yes/no button pairs.
  for (const box of all.filter(e => e.matches('.ashby-application-form-input-yesno'))) {
    if (!visible(box) || excluded(box)) continue;
    const group = [...box.querySelectorAll('button[aria-pressed],button')].filter(visible);
    if (!group.length) continue;
    group.forEach(b => consumed.add(b));
    box.querySelectorAll('input').forEach(i => consumed.add(i));
    const title = box.closest('.ashby-application-form-field-entry')?.querySelector('.ashby-application-form-question-title');
    const q = title ? clean(title.innerText) : label(box, { visibleFirst: true });
    const pressed = group.find(b => b.getAttribute('aria-pressed') === 'true');
    push(box, { label: q, type: 'buttonchoice', options: group.map(b => clean(b.innerText)),
      required: /\*/.test(q) || !!(title && /required/i.test(title.className)),
      value: pressed ? clean(pressed.innerText) : '', maxlength: -1, valid: true });
    consumed.add(box);
  }

  // Checkboxes sharing one name are one multiple-choice question (Lever, Greenhouse).
  const boxNames = {};
  for (const c of all.filter(e => e.matches('input[type=checkbox][name]'))) {
    (boxNames[c.name] = boxNames[c.name] || []).push(c);
  }
  for (const [name, group] of Object.entries(boxNames)) {
    if (group.length < 2 || excluded(group[0])) continue;
    const shown = group.filter(c => visible(c) || (c.labels && c.labels[0] && visible(c.labels[0])));
    if (!shown.length) continue;
    group.forEach(c => consumed.add(c));
    const parent = group[0].closest('.application-question,fieldset,[role=group]');
    const q = clean(parent?.querySelector('.application-label,legend')?.innerText || '') || label(group[0]) || name;
    const req = group.some(c => c.required || c.getAttribute('aria-required') === 'true') || /\*/.test(q);
    const value = group.filter(c => c.checked).map(c => label(c)).join('\n');
    push(group[0], { label: q, type: 'checkboxgroup', options: group.map(c => label(c)), required: req,
      value, maxlength: -1, valid: !req || !!value });
  }

  // Oracle Candidate Experience pills and generic aria-pressed button groups.
  const pillGroups = new Set();
  for (const b of all.filter(e => e.matches('button[aria-pressed],[role=button][aria-pressed]'))) {
    const container = b.closest('cx-select-pills,.cx-select-pills-container,[role=group],fieldset,ul') || b.parentElement;
    if (!container || pillGroups.has(container)) continue;
    const pills = [...container.querySelectorAll('button[aria-pressed],[role=button][aria-pressed]')].filter(visible);
    if (pills.length < 2 || container.querySelector('input:not([type=hidden]),select,textarea')) continue;
    pillGroups.add(container);
    pills.forEach(p => consumed.add(p));
    const q = label(container, { visibleFirst: true });
    const pressed = pills.find(p => p.getAttribute('aria-pressed') === 'true');
    push(container, { label: q, type: 'pills', required: isReq(container, q) || /\*/.test(nearby(container)),
      options: pills.map(p => clean(p.innerText)), value: pressed ? clean(pressed.innerText) : '', maxlength: -1, valid: true });
    consumed.add(container);
  }

  // Custom role=radio items without a radiogroup wrapper.
  for (const r of all.filter(e => e.matches('[role=radio]') && !e.closest('[role=radiogroup]'))) {
    const container = r.closest('[role=group],fieldset') || r.parentElement;
    if (!container || seen.has(container) || !visible(r)) continue;
    seen.add(container);
    const group = [...container.querySelectorAll('[role=radio]')];
    group.forEach(x => consumed.add(x));
    const q = label(container, { visibleFirst: true });
    const on = group.find(x => x.getAttribute('aria-checked') === 'true');
    push(container, { label: q, type: 'radiogroup', required: isReq(container, q) || /\*/.test(q),
      options: group.map(x => clean(x.innerText) || label(x)), value: on ? clean(on.innerText) || label(on) : '',
      maxlength: -1, valid: true });
    consumed.add(container);
  }

  const controls = all.filter(e => e.matches(
    'input,select,textarea,[role=combobox],[role=radiogroup],[role=checkbox],[role=switch],' +
    'button[aria-haspopup=listbox],[role=button][aria-haspopup=listbox]'));
  for (const el of controls) {
    if (consumed.has(el) || el.closest('[data-automation-id="multiselectInputContainer"]') || excluded(el)) continue;
    const role = el.getAttribute('role');
    let type = ['combobox', 'radiogroup'].includes(role) ? role : (el.type || el.tagName.toLowerCase());
    if (el.matches('[aria-haspopup=listbox]') && !el.matches('input,[role=combobox]')) type = 'dropdown';
    if (el.matches('[role=checkbox],[role=switch]') && !el.matches('input')) type = 'checkbox';
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    if (el.readOnly && type !== 'combobox') continue;
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    let widget = '';
    if (!visible(el) && type !== 'file') {
      if (type === 'select-one' || type === 'select-multiple') {
        const proxy = el.nextElementSibling;
        const host = el.parentElement;
        const plugin = (proxy && proxy.matches('.select2,.select2-container,.chosen-container,.selectize-control,.bootstrap-select,.dropdown,.ui-selectmenu-button,[role=combobox],button,a') && visible(proxy) && proxy)
          || (host && [...host.children].find(c => c !== el && visible(c) && c.matches('.select2-container,.chosen-container,.selectize-control,.bootstrap-select,.ui-selectmenu-button,.dropdown-toggle,[class*=select]')));
        if (!plugin) continue;
        widget = 'proxy';
      } else if (type === 'checkbox' || type === 'radio') {
        const lab = (el.labels && el.labels[0])
          || byId(el, (el.getAttribute('aria-labelledby') || '').split(/\s+/)[0] || '_');
        if (!lab || !visible(lab)) continue;
        widget = 'label';
      } else continue;
    }
    if (el.closest('[role=combobox]') !== el && el.closest('[role=combobox]')) continue;
    let options = [], value = el.value || '', question = label(el), group = null;
    if (type === 'dropdown') {
      question = label(el, { visibleFirst: true });
      const txt = clean(el.innerText || el.value);
      value = PLACEHOLDER_VALUE.test(txt) ? '' : txt;
      if (question === clean(el.getAttribute('aria-label') || '') && value) {
        question = clean(question.replace(value, '').replace(/\brequired\b/i, '')) || question;
      }
    } else if (type === 'radio') {
      const key = el.name || id(el);
      if (seen.has(key)) continue;
      seen.add(key);
      const root = el.getRootNode();
      group = el.name ? [...root.querySelectorAll('input[type=radio]')].filter(x => x.name === el.name) : [el];
      const parent = el.closest('fieldset,[role=radiogroup]');
      question = parent ? (parent.querySelector('legend')?.innerText || label(parent)) : '';
      if (!question || question === 'Unlabelled field' || question === parent?.id) {
        question = wrapperLabel(el) || (parent && nearby(parent)) || nearby(el.parentElement || el) || el.name || label(el);
      }
      options = group.map(x => label(x)); value = group.find(x => x.checked); value = value ? label(value) : '';
    } else if (type === 'radiogroup') {
      group = [...el.querySelectorAll('[role=radio]')];
      options = group.map(x => x.innerText || label(x));
      value = group.find(x => x.getAttribute('aria-checked') === 'true')?.innerText || '';
    } else if (el.tagName === 'SELECT') {
      type = 'select'; options = [...el.options].filter(x => !x.disabled && x.value !== '').map(x => x.text);
      value = el.selectedOptions[0]?.text || '';
      if (value && el.selectedOptions[0]?.value === '') value = '';
    } else if (type === 'combobox' && !value) {
      // react-select keeps the chosen value outside the input.
      const chosen = el.closest('.select__control')?.querySelectorAll('.select__single-value,.select__multi-value__label') || [];
      value = [...chosen].map(x => clean(x.textContent)).filter(Boolean).join(', ');
    } else if (type === 'checkbox') {
      value = el.matches('input') ? (el.checked ? 'true' : 'false') : (el.getAttribute('aria-checked') === 'true' ? 'true' : 'false');
    } else if (type === 'file') {
      value = [...(el.files || [])].map(x => x.name).join(', ');
      const identifier = `${el.id || ''} ${el.getAttribute('name') || ''}`;
      if (/cover.?letter/i.test(identifier) && !/cover.?letter/i.test(question)) question = 'Cover Letter';
      else if (!/resume|cv\b|curriculum/i.test(question)) {
        let node = el.parentElement, hint = '';
        for (let i = 0; i < 6 && node && !hint; i++, node = node.parentElement) {
          const t = clean(node.innerText || '').slice(0, 300);
          if (/cover letter|transcript|portfolio|writing sample|other document/i.test(t)) break;
          if (/resume|\bcv\b|curriculum/i.test(t)) hint = 'Resume/CV';
        }
        if (!hint && /resume|cv/i.test((el.dataset.automationId || '') + (el.name || '') + (el.id || ''))) hint = 'Resume/CV';
        // A generic visible label ("Attach", "Upload") is replaced; a specific one is kept as context.
        if (hint) question = /^(?:attach|upload|choose|browse|select|add)(?: (?:a )?file)?$|^unlabelled field$/i.test(question) || question === el.id || question === el.name ? hint : hint + ' (' + question + ')';
      }
    }
    // Secrets never leave the page: the model, traces and events only see that a value exists.
    if (type === 'password' || el.autocomplete === 'one-time-code') value = value ? '********' : '';
    const req = isReq(el, question) || (group && group.some(x => x.required || x.getAttribute('aria-required') === 'true'));
    push(el, { label: question, type, required: req, options, value, widget,
      valid: (el.validity ? el.validity.valid : true) && el.getAttribute('aria-invalid') !== 'true' });
  }

  const fieldIds = new Set(fields.map(f => f.id));
  const buttons = all.filter(el => el.matches('button,a,[role=button],input[type=submit],input[type=button]'))
    .filter(el => !consumed.has(el) && !fieldIds.has(el.dataset.jpId) && visible(el) && !el.disabled
      && !el.closest('[inert],[aria-hidden="true"]') && !el.closest(CAPTCHA_BOX)
      && el.getAttribute('aria-disabled') !== 'true' && !el.matches('[aria-haspopup=listbox]'))
    .map(el => ({
      id: id(el),
      label: clean(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || ''),
      href: el.getAttribute('href') || '', type: el.form ? (el.type || '') : '',
      automation_id: el.dataset.automationId || '', aria_label: el.getAttribute('aria-label') || '',
    })).filter(x => x.label);
  // The start (headings, questions) and the end (confirmations, footers' errors) of long pages.
  const bodyText = () => {
    const t = document.body ? document.body.innerText : '';
    return t.length > 20000 ? t.slice(0, 15000) + '\n' + t.slice(-5000) : t;
  };
  const headings = [...document.querySelectorAll('h1,h2,h3,[role=heading]')].filter(visible)
    .map(x => clean(x.innerText)).filter(Boolean).slice(0, 15);
  const errors = [...new Set([...document.querySelectorAll(ERR + ',[data-automation-id="errorBanner"]')]
    .filter(visible).map(x => clean(x.innerText).slice(0, 300)).filter(Boolean))].slice(0, 20);
  const automation = [...new Set([...document.querySelectorAll('[data-automation-id]')].filter(visible)
    .map(x => x.dataset.automationId))].slice(0, 400);
  const busy = [...document.querySelectorAll('[aria-busy=true],[data-automation-id="loadingSpinner"],.oj-progress-circle,.spinner,.loading-spinner,.loader')]
    .some(visible);
  // Open modal dialogs (Workday "Reset Password", terms pop-ups): their controls take precedence.
  const dialogs = [...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open],[data-automation-id="popUpDialog"],[data-automation-id="wd-Popup"]')]
    .filter(d => visible(d) && !d.closest(CAPTCHA_BOX) && d.getBoundingClientRect().width > 120)
    .map(d => ({
      title: clean(d.querySelector('h1,h2,h3,[role=heading]')?.innerText || d.getAttribute('aria-label') || ''),
      text: clean(d.innerText).slice(0, 4000),
      automation: [...new Set([...d.querySelectorAll('[data-automation-id]')].filter(visible).map(x => x.dataset.automationId))].slice(0, 100),
      field_ids: fields.filter(f => { const n = document.querySelector('[data-jp-id="' + f.id + '"]'); return n && d.contains(n); }).map(f => f.id),
      control_ids: buttons.filter(b => { const n = document.querySelector('[data-jp-id="' + b.id + '"]'); return n && d.contains(n); }).map(b => b.id),
    }))
    .filter(d => d.field_ids.length || d.control_ids.length);
  return {
    dialogs,
    fields, buttons, document_id: W.__jpDocId, text: bodyText(),
    title: document.title, headings, errors, automation, busy, url: location.href,
  };
}
