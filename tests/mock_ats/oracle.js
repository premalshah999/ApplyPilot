// Mock Oracle Recruiting Cloud candidate experience (hcmUI/CandidateExperience).
// Email + terms entry, emailed PIN for returning candidates, oj-select style comboboxes with
// popup listboxes, an async city search, cx-select-pills, resume upload, e-signature.
(() => {
const BASE = location.pathname.replace(/\/job\/.*$/, '');
const JOB = BASE + '/job/12345';
const app = document.getElementById('app');
const h = html => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const S = { values: {}, resume: '', page: 1, email: '' };
let n = 0;
const uid = p => p + '-' + (++n);
const api = async (path, body) => {
  const r = await fetch('/mock/api/' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  return r.json();
};
const go = path => history.pushState({}, '', path);
const closePopups = () => document.querySelectorAll('.oj-listbox-drop').forEach(x => x.remove());
document.addEventListener('mousedown', e => { if (!e.target.closest('.oj-listbox-drop,[role=combobox]')) closePopups(); }, true);

function row(label, required, control, id) {
  const el = h(`<div class="input-row"><label class="input-row__label" for="${id}">${label}${required ? ' *' : ''}</label><div class="input-row__control"></div><div class="oj-message-error" hidden></div></div>`);
  el.querySelector('.input-row__control').append(control);
  el.dataset.key = control.dataset.key; el.dataset.required = required ? '1' : ''; el.dataset.label = label;
  return el;
}
function input(key, label, required, type = 'text') {
  const id = uid(key);
  const el = h(`<input type="${type}" id="${id}" name="${key}" class="oj-inputtext-input" ${required ? 'aria-required="true"' : ''}>`);
  el.dataset.key = key;
  el.value = S.values[key] || '';
  el.addEventListener('input', () => { S.values[key] = el.value; });
  return row(label, required, el, id);
}
// oj-select-single: a read-only combobox input that opens a listbox popup.
function select(key, label, required, options, { editable = false, search = null, onChange } = {}) {
  const id = uid(key), list = uid('lov');
  const el = h(`<input type="text" id="${id}" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${list}" ${editable ? '' : 'readonly'} ${required ? 'aria-required="true"' : ''} placeholder="${editable ? 'Start typing' : 'Select a value'}">`);
  el.dataset.key = key;
  el.value = S.values[key] || '';
  const open = items => {
    closePopups();
    const drop = h(`<div class="oj-listbox-drop"><ul id="${list}" role="listbox" class="oj-listbox-results"></ul></div>`);
    const r = el.getBoundingClientRect();
    drop.style.cssText = `position:absolute;left:${r.left + scrollX}px;top:${r.bottom + scrollY}px;background:#fff;border:1px solid #999;z-index:40;max-height:none`;
    for (const o of items) {
      const li = h(`<li class="oj-listbox-result" role="option"><div class="oj-listbox-result-label">${o}</div></li>`);
      li.addEventListener('mousedown', ev => ev.preventDefault());
      li.addEventListener('click', () => { S.values[key] = o; el.value = o; el.setAttribute('aria-expanded', 'false'); closePopups(); onChange && onChange(o); });
      drop.querySelector('ul').append(li);
    }
    if (!items.length) drop.querySelector('ul').append(h('<li class="oj-listbox-no-results">No matches found</li>'));
    document.body.append(drop);
    el.setAttribute('aria-expanded', 'true');
  };
  el.addEventListener('click', () => { if (!editable) open(options); });
  el.addEventListener('keydown', e => { if (e.key === 'Escape') { closePopups(); el.setAttribute('aria-expanded', 'false'); } });
  let timer;
  if (editable) el.addEventListener('input', () => {
    delete S.values[key];
    clearTimeout(timer);
    // Remote search: results arrive after a short delay.
    timer = setTimeout(async () => { open(await search(el.value)); }, 250);
  });
  return row(label, required, el, id);
}
function pills(key, label, required, options) {
  const el = h(`<fieldset class="input-row"><legend class="input-row__label">${label}${required ? ' *' : ''}</legend><cx-select-pills><ul class="cx-select-pills-container"></ul></cx-select-pills><div class="oj-message-error" hidden></div></fieldset>`);
  for (const o of options) {
    const b = h(`<li><button type="button" class="cx-select-pill-section" aria-pressed="false"><span class="cx-select-pill-name">${o}</span></button></li>`);
    b.querySelector('button').addEventListener('click', () => {
      el.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', 'false'));
      b.querySelector('button').setAttribute('aria-pressed', 'true');
      S.values[key] = o;
    });
    el.querySelector('ul').append(b);
  }
  el.dataset.key = key; el.dataset.required = required ? '1' : ''; el.dataset.label = label;
  return el;
}
function checkbox(key, label, required) {
  const id = uid(key);
  const el = h(`<div class="input-row" data-key="${key}" data-required="${required ? '1' : ''}" data-label="${label}"><input type="checkbox" id="${id}" ${required ? 'aria-required="true"' : ''}><label for="${id}">${label}</label><div class="oj-message-error" hidden></div></div>`);
  el.querySelector('input').addEventListener('change', e => { S.values[key] = e.target.checked ? 'true' : ''; });
  return el;
}
function button(label, onClick, cls = '') {
  const b = h(`<button type="button" class="${cls}">${label}</button>`);
  b.addEventListener('click', onClick);
  return b;
}
function validate() {
  let bad = 0;
  for (const el of app.querySelectorAll('[data-key][data-required="1"]')) {
    const k = el.dataset.key;
    const missing = !(k === 'resume' ? S.resume : S.values[k]);
    const msg = el.querySelector('.oj-message-error');
    if (msg) { msg.hidden = !missing; msg.textContent = missing ? `${el.dataset.label} is required.` : ''; }
    bad += missing;
  }
  return !bad;
}

function jobPage() {
  app.innerHTML = '<h1>Software Engineer</h1><p>Acme · New York, NY, United States</p><p>Thank you for your interest in Acme. Join our platform team.</p>';
  app.append(button('Apply Now', () => { go(JOB + '/apply/email'); emailPage(); }, 'apply-now-button'));
}
function emailPage() {
  app.innerHTML = '<h1>Welcome!</h1><p>Enter your email address to apply for Software Engineer.</p>';
  const email = input('email', 'Email Address', true, 'email');
  app.append(email, checkbox('terms', 'I agree with the terms and conditions', true));
  app.append(button('Next', async () => {
    if (!validate()) return;
    const r = await api('email', { email: S.values.email });
    S.email = S.values.email;
    if (r.pin) pinPage(); else { S.values = { email: S.email }; flow(); }
  }));
}
function pinPage() {
  app.innerHTML = `<h1>Confirm Your Identity</h1><p>We sent a verification code to your email address. Enter the code to continue.</p>`;
  const box = h('<div class="pin-code-inputs"></div>');
  const inputs = [];
  for (let i = 1; i <= 6; i++) {
    const inp = h(`<input type="text" id="pin-code-${i}" maxlength="1" aria-label="PIN code ${i}" inputmode="numeric">`);
    inp.addEventListener('input', () => { if (inp.value && inputs[i]) inputs[i].focus(); });
    inputs.push(inp);
    box.append(inp);
  }
  app.append(box);
  const err = h('<div class="oj-message-error" role="alert" hidden></div>');
  app.append(err);
  app.append(button('Verify', async () => {
    const r = await api('pin', { email: S.email, pin: inputs.map(x => x.value).join('') });
    if (r.error) { err.hidden = false; err.textContent = r.error; return; }
    S.values = { email: S.email, ...r.profile };
    flow();
  }));
  app.append(button('Send New Code', () => api('email', { email: S.email })));
}
const COUNTRIES = ['Canada', 'India', 'Mexico', 'United Kingdom', 'United States'];
const STATES = ['California', 'Massachusetts', 'New Jersey', 'New York', 'Texas', 'Washington'];
const CITIES = ['New Haven', 'New Orleans', 'New York', 'Newark'];
function flow() {
  closePopups();
  app.innerHTML = `<div class="apply-flow"><h1>Software Engineer</h1><h2>${S.page === 1 ? 'Contact Information' : 'Application Questions'}</h2></div>`;
  const sec = app.querySelector('.apply-flow');
  if (S.page === 1) {
    sec.append(input('lastName', 'Last Name', true), input('firstName', 'First Name', true));
    sec.append(select('phoneCountry', 'Country Code', true, ['Canada (+1)', 'India (+91)', 'United Kingdom (+44)', 'United States (+1)']));
    sec.append(input('phone', 'Phone Number', true, 'tel'));
    sec.append(h('<h3>Address</h3>'));
    sec.append(select('country', 'Country', true, COUNTRIES));
    sec.append(input('address1', 'Address Line 1', true));
    sec.append(select('city', 'City', true, [], { editable: true, search: async q => CITIES.filter(c => c.toLowerCase().startsWith(q.toLowerCase())) }));
    sec.append(select('state', 'State', true, STATES));
    sec.append(input('zip', 'ZIP Code', true));
    app.append(button('Next', () => { if (validate()) { S.page = 2; flow(); } }, 'apply-flow-pagination-next-button'));
  } else {
    sec.append(pills('authorized', 'Are you legally authorized to work in the country in which this job is located?', true, ['Yes', 'No']));
    sec.append(pills('sponsorship', 'Will you now or in the future require sponsorship for an employment visa?', true, ['Yes', 'No']));
    sec.append(pills('noncompete', 'Are you bound by a non-compete agreement that would restrict your employment with Acme?', true, ['Yes', 'No']));
    sec.append(input('motivation', 'What interests you most about this role?', true));
    sec.append(h('<h3>Supporting Documents</h3>'));
    const docs = h('<div class="input-row" data-key="resume" data-required="1" data-label="Resume"><label class="input-row__label">Resume *</label><input type="file" id="attachment-upload-resume" style="display:none"><button type="button">Upload Resume</button><div class="files"></div><div class="oj-message-error" hidden></div></div>');
    const file = docs.querySelector('input');
    docs.querySelector('button').addEventListener('click', () => file.click());
    file.addEventListener('change', async () => {
      const f = file.files[0];
      docs.querySelector('.files').innerHTML = '<div class="oj-progress-circle" aria-busy="true">Uploading</div>';
      await new Promise(r => setTimeout(r, 500));
      await api('upload', { name: f.name, size: f.size });
      S.resume = f.name;
      docs.querySelector('.files').innerHTML = `<span class="attachment-name">${f.name}</span>`;
    });
    sec.append(docs);
    sec.append(h('<h3>e-Signature</h3>'));
    sec.append(input('signature', 'Full Name', true));
    sec.append(checkbox('esign', 'By checking this box, I acknowledge that this is my electronic signature', true));
    app.append(button('Back', () => { S.page = 1; flow(); }));
    app.append(button('Submit', async () => {
      if (!validate()) return;
      const r = await api('submit', { values: S.values, resume: S.resume });
      if (r.error) { app.prepend(h(`<div class="oj-message-error" role="alert">${r.error}</div>`)); return; }
      go(BASE + '/my-profile');
      app.innerHTML = '<h1>Thank you for your job application.</h1><p>We received your application for Software Engineer. Track it in My Profile.</p>';
    }, 'apply-flow-submit-button'));
  }
}
if (location.pathname.includes('/apply/')) emailPage(); else jobPage();
})();
