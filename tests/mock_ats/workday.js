// Mock Workday candidate experience. Reproduces the DOM contracts real tenants expose:
// data-automation-id attributes, click_filter overlays, body-level virtualized listboxes,
// multiselect prompts with categories, spinbutton date parts, server-side validation.
(() => {
const SITE = location.pathname.split('/job/')[0].replace(/\/(activate|reset)\/.*/, '');
const JOB_PATH = SITE + '/job/New-York/Software-Engineer_R123';
const app = document.getElementById('app');
let n = 0;
const uid = () => 'input-' + (++n);
const S = { values: {}, step: 0, work: 0, edu: 0, resume: '' };
const STEPS = ['My Information', 'My Experience', 'Application Questions', 'Voluntary Disclosures', 'Self Identify', 'Review'];
const PAGES = ['applyFlowMyInfoPage', 'applyFlowMyExpPage', 'applyFlowPrimaryQuestionsPage', 'applyFlowVoluntaryDisclosuresPage', 'applyFlowSelfIdentifyPage', 'applyFlowReviewPage'];
const COUNTRIES = ['Afghanistan', 'Albania', 'Algeria', 'Argentina', 'Australia', 'Austria', 'Bangladesh', 'Belgium', 'Brazil', 'Bulgaria', 'Canada', 'Chile', 'China', 'Colombia', 'Croatia', 'Czechia', 'Denmark', 'Egypt', 'Finland', 'France', 'Germany', 'Greece', 'Hungary', 'India', 'Indonesia', 'Ireland', 'Israel', 'Italy', 'Japan', 'Kenya', 'Mexico', 'Netherlands', 'New Zealand', 'Nigeria', 'Norway', 'Pakistan', 'Peru', 'Philippines', 'Poland', 'Portugal', 'Romania', 'Singapore', 'South Africa', 'Spain', 'Sweden', 'Switzerland', 'Turkey', 'Ukraine', 'United Arab Emirates', 'United Kingdom', 'United States of America', 'Uruguay', 'Vietnam'];
const STATES = ['Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado', 'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Hawaii', 'Idaho', 'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana', 'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Minnesota', 'Mississippi', 'Missouri', 'Montana', 'Nebraska', 'Nevada', 'New Hampshire', 'New Jersey', 'New Mexico', 'New York', 'North Carolina', 'North Dakota', 'Ohio', 'Oklahoma', 'Oregon', 'Pennsylvania', 'Rhode Island', 'South Carolina', 'South Dakota', 'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia', 'Washington', 'West Virginia', 'Wisconsin', 'Wyoming'];
const SOURCES = [
  { name: 'Job Board', children: [{ name: 'LinkedIn' }, { name: 'Indeed' }, { name: 'Glassdoor' }] },
  { name: 'Company Website' },
  { name: 'Referral', children: [{ name: 'Employee Referral' }, { name: 'Friend or Family' }] },
  { name: 'Other' },
];
const PHONE_CODES = COUNTRIES.map(c => ({ name: c + ' (' + (c === 'United States of America' || c === 'Canada' ? '+1' : '+' + (c.length + 30)) + ')' }));

const session = () => (document.cookie.match(/wd_session=([^;]+)/) || [])[1] || '';
const api = async (path, body) => {
  const r = await fetch('/mock/api/' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...(body || {}), session: session() }) });
  const data = await r.json();
  if (data.session) document.cookie = 'wd_session=' + data.session + '; path=/';
  return data;
};
const h = html => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const closePopups = () => document.querySelectorAll('.popup').forEach(p => p.remove());
const banner = document.getElementById('onetrust-banner-sdk');
document.getElementById('onetrust-accept-btn-handler').onclick = () => { document.cookie = 'OptanonAlertBoxClosed=1; path=/'; banner.remove(); };
if (document.cookie.includes('OptanonAlertBoxClosed')) banner.remove();
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePopups(); });
document.addEventListener('mousedown', e => {
  if (!e.target.closest('.popup,[aria-haspopup=listbox],[data-automation-id=multiselectInputContainer]')) closePopups();
}, true);

function wrap(key, label, required, inner, inputId) {
  const el = h(`<div data-automation-id="formField-${key}"><label for="${inputId}">${label}${required ? '<abbr title="required">*</abbr>' : ''}</label><div class="ctl"></div><div class="err" data-automation-id="errorMessage" hidden></div></div>`);
  el.querySelector('.ctl').append(inner);
  el.dataset.key = key;
  el.dataset.required = required ? '1' : '';
  el.dataset.label = label;
  return el;
}
function text(key, auto, label, required, type = 'text') {
  const id = uid();
  const inp = h(`<input type="${type}" data-automation-id="${auto}" id="${id}" ${required ? 'aria-required="true"' : ''}>`);
  inp.value = S.values[key] || '';
  inp.addEventListener('input', () => { S.values[key] = inp.value; });
  return wrap(key, label, required, inp, id);
}
function textarea(key, auto, label) {
  const id = uid();
  const inp = h(`<textarea data-automation-id="${auto}" id="${id}"></textarea>`);
  inp.addEventListener('input', () => { S.values[key] = inp.value; });
  return wrap(key, label, false, inp, id);
}
function position(pop, anchor) {
  const r = anchor.getBoundingClientRect();
  pop.style.left = (r.left + scrollX) + 'px';
  pop.style.top = (r.bottom + scrollY) + 'px';
}
function dropdown(key, auto, label, required, options, onChange) {
  const id = uid();
  const btn = h(`<button type="button" aria-haspopup="listbox" data-automation-id="${auto}" id="${id}"></button>`);
  const show = () => {
    const v = S.values[key];
    btn.textContent = v || 'Select One';
    btn.setAttribute('aria-label', `${label} ${v || 'Select One'}${required ? ' Required' : ''}`);
  };
  show();
  btn.addEventListener('click', () => {
    closePopups();
    const pop = h('<div class="popup" data-automation-id="activeListContainer"><ul role="listbox"></ul></div>');
    position(pop, btn);
    const list = pop.querySelector('ul');
    let start = 0, prefix = '', timer;
    const draw = () => {
      list.innerHTML = '';
      // Virtualized: only a window of options exists in the DOM.
      for (const o of options.slice(start, start + 12)) {
        const li = h(`<li role="option" data-automation-id="menuItem"><div>${o}</div></li>`);
        li.addEventListener('click', () => { S.values[key] = o; show(); closePopups(); onChange && onChange(o); });
        list.append(li);
      }
    };
    const onKey = e => {
      if (!pop.isConnected) { document.removeEventListener('keydown', onKey); return; }
      if (e.key.length !== 1) return;
      prefix += e.key.toLowerCase();
      clearTimeout(timer);
      timer = setTimeout(() => { prefix = ''; }, 700);
      const i = options.findIndex(o => o.toLowerCase().startsWith(prefix));
      if (i >= 0) { start = i; draw(); }
    };
    document.addEventListener('keydown', onKey);
    draw();
    document.body.append(pop);
  });
  return wrap(key, label, required, btn, id);
}
function prompt(key, label, required, tree) {
  const id = uid();
  const box = h(`<div data-automation-id="multiselectInputContainer"><input data-uxi-widget-type="selectinput" placeholder="Search" id="${id}" ${required ? 'aria-required="true"' : ''}></div>`);
  const sel = h('<div data-automation-id="selectedItemList"></div>');
  const inp = box.querySelector('input');
  const holder = h('<div></div>');
  holder.append(box, sel);
  const showSel = () => {
    sel.innerHTML = '';
    if (S.values[key]) sel.append(h(`<div data-automation-id="selectedItem" title="${S.values[key]}"><p>${S.values[key]}</p></div>`));
  };
  showSel();
  const leaves = [];
  const walkTree = nodes => nodes.forEach(x => (x.children ? walkTree(x.children) : leaves.push(x.name)));
  walkTree(tree);
  const open = (nodes, search) => {
    closePopups();
    const pop = h('<div class="popup" data-automation-id="promptPopup"><ul role="listbox"></ul></div>');
    position(pop, box);
    const list = pop.querySelector('ul');
    if (nodes !== tree && search === undefined) {
      const back = h('<li data-automation-id="backButton" aria-label="Back">‹ Back</li>');
      back.addEventListener('click', () => open(tree));
      list.append(back);
    }
    const items = search !== undefined && search !== '' ? leaves.filter(l => l.toLowerCase().includes(search.toLowerCase())).map(name => ({ name })) : nodes;
    if (!items.length) list.append(h('<li>No Items.</li>'));
    for (const it of items) {
      const li = h(`<li role="option" data-automation-id="promptOption" data-automation-label="${it.name}"><div>${it.name}</div>${it.children ? '<span data-automation-id="promptIcon">›</span>' : ''}</li>`);
      li.addEventListener('click', () => {
        if (it.children) open(it.children);
        else { S.values[key] = it.name; showSel(); inp.value = ''; closePopups(); }
      });
      list.append(li);
    }
    document.body.append(pop);
  };
  inp.addEventListener('click', () => open(tree));
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); open(tree, inp.value.trim()); } });
  return wrap(key, label, required, holder, id);
}
function dateField(key, label, required, parts) {
  const id = uid();
  const w = h('<div data-automation-id="dateInputWrapper"></div>');
  const inputs = parts.map((p, i) => {
    const cap = p[0].toUpperCase() + p.slice(1);
    const inp = h(`<input type="text" role="spinbutton" data-automation-id="dateSection${cap}-input" aria-label="${cap}" maxlength="${p === 'year' ? 4 : 2}" aria-valuetext="${p === 'year' ? 'YYYY' : p === 'month' ? 'MM' : 'DD'}" ${i === 0 ? `id="${id}"` : ''}>`);
    w.append(inp);
    if (i < parts.length - 1) w.append(document.createTextNode('/'));
    return inp;
  });
  inputs.forEach((inp, i) => inp.addEventListener('input', () => {
    inp.value = inp.value.replace(/\D/g, '');
    inp.setAttribute('aria-valuenow', inp.value);
    inp.setAttribute('aria-valuetext', inp.value);
    S.values[key] = inputs.every(x => x.value) ? inputs.map(x => x.value).join('/') : '';
    if (inp.value.length >= +inp.maxLength && inputs[i + 1]) inputs[i + 1].focus();
  }));
  return wrap(key, label, required, w, id);
}
function checkbox(key, auto, label, required) {
  const id = uid();
  const el = h(`<div data-automation-id="formField-${key}"><input type="checkbox" data-automation-id="${auto}" id="${id}" ${required ? 'aria-required="true"' : ''}><label for="${id}">${label}</label><div class="err" data-automation-id="errorMessage" hidden></div></div>`);
  const inp = el.querySelector('input');
  inp.addEventListener('change', () => { S.values[key] = inp.checked ? 'true' : ''; });
  el.dataset.key = key; el.dataset.required = required ? '1' : ''; el.dataset.label = label;
  return el;
}
function radios(key, label, required, options) {
  const el = h(`<div data-automation-id="formField-${key}"><fieldset><legend><label>${label}${required ? '<abbr title="required">*</abbr>' : ''}</label></legend></fieldset><div class="err" data-automation-id="errorMessage" hidden></div></div>`);
  const fs = el.querySelector('fieldset');
  for (const o of options) {
    const id = uid();
    const r = h(`<div><input type="radio" name="${key}" id="${id}" value="${o}"><label for="${id}">${o}</label></div>`);
    r.querySelector('input').addEventListener('change', () => { S.values[key] = o; });
    fs.append(r);
  }
  el.dataset.key = key; el.dataset.required = required ? '1' : ''; el.dataset.label = label;
  return el;
}
function clickFiltered(auto, label, onClick) {
  // Workday renders a transparent click_filter over its primary buttons.
  const el = h(`<div class="cf"><button type="submit" data-automation-id="${auto}">${label}</button><div data-automation-id="click_filter" role="button" tabindex="0" aria-label="${label}"></div></div>`);
  el.querySelector('[data-automation-id=click_filter]').addEventListener('click', onClick);
  return el;
}
function setUrl(path) { history.pushState({}, '', path); }

// ----- pages -------------------------------------------------------------------------------
function jobPage() {
  app.innerHTML = '';
  app.append(h(`<div data-automation-id="jobPostingHeader"><h2>Software Engineer</h2><p>Acme · New York</p></div>`));
  app.append(h('<div data-automation-id="jobPostingDescription"><p>Build reliable distributed systems with Python and Kubernetes.</p><p>Thank you for your interest in Acme.</p></div>'));
  const apply = h(`<a data-automation-id="adventureButton" role="button" href="${JOB_PATH}/apply">Apply</a>`);
  apply.addEventListener('click', e => { e.preventDefault(); setUrl(JOB_PATH + '/apply'); methodModal(); });
  app.append(apply);
}
function methodModal() {
  const modal = h('<div role="dialog" aria-label="Start Your Application" class="modal"><h2>Start Your Application</h2></div>');
  const mk = (auto, label, onClick) => {
    const a = h(`<a data-automation-id="${auto}" role="button" href="#">${label}</a>`);
    a.addEventListener('click', e => { e.preventDefault(); modal.remove(); onClick(); });
    modal.append(a);
  };
  mk('autofillWithResume', 'Autofill with Resume', () => { setUrl(JOB_PATH + '/apply/autofillWithResume'); authOrFlow(); });
  mk('applyManually', 'Apply Manually', () => { setUrl(JOB_PATH + '/apply/applyManually'); authOrFlow(); });
  document.body.append(modal);
}
async function authOrFlow() {
  const me = await api('session');
  if (me.signedIn) return flow();
  createAccountPage();
}
function errorBox(msg) {
  let box = app.querySelector('[data-automation-id=errorMessage].auth');
  if (!box) { box = h('<div data-automation-id="errorMessage" class="auth err" role="alert"></div>'); app.prepend(box); }
  box.textContent = msg;
}
function createAccountPage() {
  app.innerHTML = '<h2 data-automation-id="createAccountHeader">Create Account</h2>';
  const email = text('ca_email', 'email', 'Email Address', true);
  const pw = text('ca_password', 'password', 'Password', true, 'password');
  const pw2 = text('ca_verify', 'verifyPassword', 'Verify New Password', true, 'password');
  const agree = checkbox('ca_agree', 'createAccountCheckbox', 'I agree to the Privacy Policy and Terms of Use', true);
  app.append(email, pw, pw2, agree);
  app.append(clickFiltered('createAccountSubmitButton', 'Create Account', async () => {
    const v = S.values;
    if (!v.ca_agree) return errorBox('You must agree to the terms before creating an account.');
    if (v.ca_password !== v.ca_verify) return errorBox('Passwords do not match.');
    const r = await api('create', { email: v.ca_email, password: v.ca_password });
    if (r.error) return errorBox(r.error);
    app.innerHTML = `<h2>Verify Your Account</h2><p>We've sent a verification email to ${v.ca_email}. Check your inbox and click the link to activate your account.</p>`;
  }));
  const signin = h('<button type="button" data-automation-id="signInLink">Sign In</button>');
  signin.addEventListener('click', signInPage);
  app.append(h('<p>Already have an account?</p>'), signin);
}
function signInPage() {
  app.innerHTML = '<h2>Sign In</h2>';
  app.append(text('si_email', 'email', 'Email Address', true), text('si_password', 'password', 'Password', true, 'password'));
  app.append(clickFiltered('signInSubmitButton', 'Sign In', async () => {
    const r = await api('signin', { email: S.values.si_email, password: S.values.si_password });
    if (r.error) return errorBox(r.error);
    flow();
  }));
  const forgot = h('<button type="button" data-automation-id="forgotPasswordLink">Forgot your password?</button>');
  forgot.addEventListener('click', forgotPage);
  const create = h('<button type="button" data-automation-id="createAccountLink">Create Account</button>');
  create.addEventListener('click', createAccountPage);
  app.append(forgot, create);
}
function forgotPage() {
  // Live Workday opens a "Reset Password" dialog over the sign-in page.
  const modal = h('<div role="dialog" aria-modal="true" aria-label="Reset Password" class="modal" data-automation-id="popUpDialog"><h2>Reset Password</h2></div>');
  const email = text('fp_email', 'email', 'Email Address', true);
  const b = h('<button type="button" data-automation-id="resetPasswordButton">Reset Password</button>');
  b.addEventListener('click', async () => {
    await api('forgot', { email: S.values.fp_email });
    modal.innerHTML = '<h2>Reset Password</h2><p>An email has been sent with instructions to reset your password.</p>';
    setTimeout(() => modal.remove(), 1500);
  });
  modal.append(email, b);
  document.body.append(modal);
}
async function activatePage(token) {
  const r = await api('activate', { token });
  app.innerHTML = r.ok ? '<h2>Account Verified</h2><p>Your account has been verified. You can now sign in.</p>' : '<h2>Link expired</h2>';
}
function resetPage(token) {
  app.innerHTML = '<h2>Reset Password</h2>';
  app.append(text('rp_pw', 'password', 'New Password', true, 'password'), text('rp_pw2', 'verifyPassword', 'Verify New Password', true, 'password'));
  app.append(clickFiltered('resetPasswordSubmitButton', 'Reset Password', async () => {
    if (S.values.rp_pw !== S.values.rp_pw2) return errorBox('Passwords do not match.');
    const r = await api('reset', { token, password: S.values.rp_pw });
    if (r.error) return errorBox(r.error);
    app.innerHTML = '<h2>Password Reset</h2><p>Your password has been changed. Return to the job posting to sign in.</p>';
  }));
}

// ----- application flow --------------------------------------------------------------------
function progress() {
  const bar = h('<div data-automation-id="progressBar"></div>');
  STEPS.forEach((s, i) => bar.append(h(`<div ${i === S.step ? 'data-automation-id="progressBarActiveStep"' : ''}><span>${i === S.step ? `current step ${i + 1} of ${STEPS.length}` : ''}</span><div>${s}</div></div>`)));
  return bar;
}
function stateField() {
  return dropdown('state', 'addressSection_countryRegion', 'State', true, STATES);
}
function stepPage() {
  closePopups();
  app.innerHTML = '';
  app.append(progress());
  const page = h(`<div data-automation-id="${PAGES[S.step]}"><h2 tabindex="-1">${STEPS[S.step]}</h2></div>`);
  app.append(page);
  const add = el => page.append(el);
  if (S.step === 0) {
    add(prompt('source', 'How Did You Hear About Us?', true, SOURCES));
    add(radios('previousWorker', 'Have you previously worked for Acme?', true, ['Yes', 'No']));
    let state = null;
    add(dropdown('country', 'countryDropdown', 'Country', true, COUNTRIES, v => {
      if (v === 'United States of America' && !state) { state = stateField(); page.querySelector('[data-automation-id="formField-city"]').after(state); }
      if (v !== 'United States of America' && state) { state.remove(); state = null; delete S.values.state; }
    }));
    add(text('firstName', 'legalNameSection_firstName', 'First Name', true));
    add(text('lastName', 'legalNameSection_lastName', 'Last Name', true));
    add(text('address1', 'addressSection_addressLine1', 'Address Line 1', true));
    add(text('city', 'addressSection_city', 'City', true));
    add(text('postal', 'addressSection_postalCode', 'Postal Code', true));
    add(dropdown('phoneType', 'phone-device-type', 'Phone Device Type', true, ['Home', 'Mobile', 'Work']));
    add(prompt('phoneCode', 'Country Phone Code', true, PHONE_CODES));
    add(text('phone', 'phone-number', 'Phone Number', true, 'tel'));
  } else if (S.step === 1) {
    const work = h('<div role="group" aria-labelledby="work-section"><h3 id="work-section">Work Experience</h3><div class="rows"></div></div>');
    const edu = h('<div role="group" aria-labelledby="edu-section"><h3 id="edu-section">Education</h3><div class="rows"></div></div>');
    const addBtn = (section, kind) => {
      const label = kind === 'work' ? 'Work Experience' : 'Education';
      const b = h(`<button type="button" data-automation-id="Add" aria-label="Add ${label}">Add</button>`);
      b.addEventListener('click', () => {
        const i = ++S[kind];
        section.querySelector('.rows').append(kind === 'work' ? workPanel(i) : eduPanel(i));
        b.textContent = 'Add Another';
        b.setAttribute('aria-label', `Add Another ${label}`);
      });
      section.append(b);
    };
    addBtn(work, 'work');
    addBtn(edu, 'edu');
    add(work);
    add(edu);
    add(h('<h3>Resume/CV</h3>'));
    const zone = h('<div data-automation-id="resumeSection"><div data-automation-id="file-upload-drop-zone"><p>Drop file here</p><button type="button" data-automation-id="select-files">Select files</button><input type="file" data-automation-id="file-upload-input-ref" style="display:none"></div><div class="files"></div><div class="err" data-automation-id="errorMessage" hidden></div></div>');
    const file = zone.querySelector('input');
    zone.querySelector('button').addEventListener('click', () => file.click());
    file.addEventListener('change', () => {
      const f = file.files[0];
      const list = zone.querySelector('.files');
      list.innerHTML = '<div data-automation-id="loadingSpinner" aria-busy="true">Uploading…</div>';
      setTimeout(async () => {
        await api('upload', { name: f.name, size: f.size });
        S.resume = f.name;
        file.value = '';
        list.innerHTML = `<div data-automation-id="file-upload-item"><div data-automation-id="file-upload-item-name">${f.name}</div><span>Successfully Uploaded!</span><button type="button" data-automation-id="delete-file" aria-label="Delete ${f.name}">x</button></div>`;
      }, 600);
    });
    zone.dataset.key = 'resume'; zone.dataset.required = '1'; zone.dataset.label = 'Resume/CV';
    add(zone);
    add(text('linkedin', 'linkedinQuestion', 'LinkedIn', false));
  } else if (S.step === 2) {
    add(dropdown('authorized', 'q1', 'Are you legally authorized to work in the United States?', true, ['Yes', 'No']));
    add(dropdown('sponsorship', 'q2', 'Will you now or in the future require sponsorship for employment visa status (e.g., H-1B)?', true, ['Yes', 'No']));
    add(radios('adult', 'Are you at least 18 years of age?', true, ['Yes', 'No']));
    add(dropdown('k8s', 'q4', 'Do you have production experience with Kubernetes?', true, ['Yes', 'No']));
    add(text('salary', 'q5', 'What are your salary expectations?', false));
  } else if (S.step === 3) {
    add(dropdown('gender', 'gender', 'Gender', true, ['Male', 'Female', 'Non-binary', 'I do not wish to answer']));
    add(dropdown('ethnicity', 'ethnicityDropdown', 'Ethnicity', true, ['Asian', 'Black or African American', 'Hispanic or Latino', 'White', 'Two or More Races', 'I do not wish to answer']));
    add(dropdown('veteran', 'veteranStatus', 'Veteran Status', true, ['I am not a protected veteran', 'I identify as one or more of the classifications of protected veteran', 'I do not wish to self-identify']));
    add(checkbox('terms', 'agreementCheckbox', 'I have read and agree to the Terms and Conditions', true));
  } else if (S.step === 4) {
    add(h('<h3>Voluntary Self-Identification of Disability</h3>'));
    add(text('sigName', 'name', 'Name', true));
    add(dateField('sigDate', 'Date', true, ['month', 'day', 'year']));
    const group = h('<div data-automation-id="formField-disability" data-key="disability" data-required="1" data-label="Disability Status"><fieldset><legend>Please check one of the boxes below:</legend></fieldset><div class="err" data-automation-id="errorMessage" hidden></div></div>');
    for (const o of ['Yes, I have a disability (or previously had a disability)', 'No, I do not have a disability and have not had one in the past', 'I do not want to answer']) {
      const id = uid();
      const row = h(`<div><input type="checkbox" id="${id}" data-automation-id="disabilityStatus"><label for="${id}">${o}</label></div>`);
      row.querySelector('input').addEventListener('change', e => { if (e.target.checked) S.values.disability = o; else if (S.values.disability === o) delete S.values.disability; });
      group.querySelector('fieldset').append(row);
    }
    add(group);
  } else {
    const dl = h('<dl data-automation-id="reviewSummary"></dl>');
    for (const [k, v] of Object.entries(S.values)) if (!k.startsWith('ca_') && !k.startsWith('si_')) dl.append(h(`<div><dt>${k}</dt><dd>${v}</dd></div>`));
    dl.append(h(`<div><dt>Resume</dt><dd>${S.resume}</dd></div>`));
    add(dl);
  }
  const nav = h('<div data-automation-id="pageFooter"></div>');
  if (S.step > 0) {
    const back = h('<button type="button" data-automation-id="bottom-navigation-back-button">Back</button>');
    back.addEventListener('click', () => { S.step--; stepPage(); });
    nav.append(back);
  }
  const next = h(`<button type="button" data-automation-id="bottom-navigation-next-button">${S.step === STEPS.length - 1 ? 'Submit' : 'Save and Continue'}</button>`);
  next.addEventListener('click', advance);
  nav.append(next);
  app.append(nav);
}
function workPanel(i) {
  const g = h(`<div role="group" aria-labelledby="work-${i}"><h4 id="work-${i}">Work Experience ${i}</h4></div>`);
  g.append(text(`w${i}_title`, 'jobTitle', 'Job Title', true), text(`w${i}_company`, 'company', 'Company', true), text(`w${i}_location`, 'location', 'Location', false));
  const cur = checkbox(`w${i}_current`, 'currentlyWorkHere', 'I currently work here', false);
  g.append(cur);
  g.append(dateField(`w${i}_from`, 'From', true, ['month', 'year']));
  const to = dateField(`w${i}_to`, 'To', true, ['month', 'year']);
  g.append(to);
  cur.querySelector('input').addEventListener('change', e => { to.hidden = e.target.checked; to.dataset.required = e.target.checked ? '' : '1'; });
  g.append(textarea(`w${i}_desc`, 'description', 'Role Description'));
  return g;
}
function eduPanel(i) {
  const g = h(`<div role="group" aria-labelledby="edu-${i}"><h4 id="edu-${i}">Education ${i}</h4></div>`);
  g.append(text(`e${i}_school`, 'school', 'School or University', true));
  g.append(dropdown(`e${i}_degree`, 'degree', 'Degree', true, ["Associate's Degree", "Bachelor's Degree", "Master's Degree", 'Doctorate', 'High School Diploma/GED']));
  g.append(text(`e${i}_field`, 'fieldOfStudy', 'Field of Study', false));
  g.append(dateField(`e${i}_from`, 'From', false, ['year']));
  g.append(dateField(`e${i}_to`, 'To (Actual or Expected)', true, ['year']));
  return g;
}
async function advance() {
  // Workday validates on the server, then reports field errors and an error banner.
  let bad = 0;
  for (const el of app.querySelectorAll('[data-key]')) {
    const err = el.querySelector(':scope > .err, :scope > [data-automation-id=errorMessage]');
    const k = el.dataset.key;
    const missing = el.dataset.required && !el.hidden && !(k === 'resume' ? S.resume : S.values[k]);
    if (err) { err.hidden = !missing; err.textContent = missing ? `Error: The field ${el.dataset.label} is required and must have a value.` : ''; }
    if (missing) bad++;
  }
  app.querySelector('[data-automation-id=errorBanner]')?.remove();
  if (bad) { app.prepend(h(`<div data-automation-id="errorBanner" role="alert">Errors Found (${bad})</div>`)); return; }
  const busy = h('<div data-automation-id="loadingSpinner" aria-busy="true">Saving…</div>');
  app.append(busy);
  if (S.step === STEPS.length - 1) {
    const r = await api('submit', { values: S.values, resume: S.resume });
    busy.remove();
    if (r.error) { app.prepend(h(`<div data-automation-id="errorBanner" role="alert">${r.error}</div>`)); return; }
    app.innerHTML = '<div data-automation-id="applicationSubmittedPage"><h2>Application Submitted</h2><p>Thank you for applying! You can track your application from your candidate home.</p></div>';
    return;
  }
  await api('save', { step: S.step, values: S.values });
  busy.remove();
  S.step++;
  stepPage();
}
async function flow() {
  const r = await api('session');
  if (r.applied) { app.innerHTML = '<h2>Software Engineer</h2><p>You have already applied for this job.</p>'; return; }
  S.step = 0;
  stepPage();
}

const path = location.pathname;
if (path.includes('/activate/')) activatePage(path.split('/activate/')[1]);
else if (path.includes('/reset/')) resetPage(path.split('/reset/')[1]);
else if (path.includes('/apply')) { methodModal(); }
else jobPage();
})();
