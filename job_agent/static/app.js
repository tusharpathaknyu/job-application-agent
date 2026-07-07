const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

function toast(message) {
  const el = $('#toast'); el.textContent = message; el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3500);
}

function formObject(form) { return Object.fromEntries(new FormData(form).entries()); }
let jobCache = [];
const laneOrder = ['Software Engineering', 'AI / ML Engineering', 'Applications Engineering', 'Solutions Engineering', 'Power, Board & Hardware', 'FPGA Engineering', 'Chip Design & Verification', 'Game Development'];
const regionOrder = ['India', 'Canada', 'United Kingdom', 'Australia', 'United States', 'Europe', 'Remote / Worldwide', 'Other International'];

async function loadProfile() {
  const profile = await api('/api/profile');
  const form = $('#profile-form');
  for (const [key, value] of Object.entries(profile)) if (form.elements[key]) form.elements[key].value = value || '';
  const answers = profile.application_answers || {};
  for (const [key, value] of Object.entries(answers)) if (form.elements[key]) form.elements[key].value = value || '';
  const answerCount = Object.values(answers).filter(value => String(value || '').trim()).length;
  $('#application-answer-status').textContent = answerCount
    ? `${answerCount} reusable answer${answerCount === 1 ? '' : 's'} saved locally.`
    : 'No reusable answers saved yet.';
}

async function loadContext() {
  const context = await api('/api/context');
  const lanes = (context.target_lanes || []).join(', ');
  const regions = (context.search_regions || []).join(', ');
  $('#context-status').textContent = context.loaded
    ? `Canonical context loaded: ${context.experience_count} roles, ${context.project_count} projects${context.resume_template_loaded ? ', LaTeX template' : ''}${lanes ? `. Target lanes: ${lanes}.` : '.'}${regions ? ` Search scope: ${regions}.` : ''}`
    : 'Canonical context is not loaded; tailoring will use the optional base resume only.';
}

async function loadJobs() {
  jobCache = await api('/api/jobs');
  renderJobs();
}

function renderJobs() {
  const query = ($('#job-filter').value || '').trim().toLowerCase();
  const jobs = jobCache.filter(job => !query || [job.title, job.company, job.location, job.role_lane, job.search_region, job.status, job.decision].some(value => String(value || '').toLowerCase().includes(query)));
  $('#job-count').textContent = `${jobs.length} job${jobs.length === 1 ? '' : 's'}`;
  const laneCounts = Object.fromEntries(laneOrder.map(lane => [lane, 0]));
  for (const job of jobCache) laneCounts[job.role_lane] = (laneCounts[job.role_lane] || 0) + 1;
  $('#lane-summary').innerHTML = laneOrder.map(lane => `<button type="button" class="lane-chip" data-lane="${escapeAttr(lane)}">${escapeHtml(lane)} <strong>${laneCounts[lane] || 0}</strong></button>`).join('');
  const regionCounts = Object.fromEntries(regionOrder.map(region => [region, 0]));
  for (const job of jobCache) regionCounts[job.search_region] = (regionCounts[job.search_region] || 0) + 1;
  $('#region-summary').innerHTML = regionOrder.map(region => `<button type="button" class="lane-chip region-chip" data-region="${escapeAttr(region)}">${escapeHtml(region)} <strong>${regionCounts[region] || 0}</strong></button>`).join('');
  $('#jobs').innerHTML = jobs.length ? jobs.map(job => `
    <div class="job">
      <div><h3>${escapeHtml(job.title)}</h3><p>${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location not listed')}</p>
      <div class="meta"><span class="badge lane">${escapeHtml(job.role_lane || 'Unclassified')}</span> <span class="badge region">${escapeHtml(job.search_region || 'Region unknown')}</span> <span class="badge ${job.decision || ''}">${escapeHtml(job.decision || job.status)}</span>${job.fit_score != null ? ` · Fit ${job.fit_score}/100` : ''}</div></div>
      <div class="actions">${job.package_id ? `<button class="quiet" onclick="review(${job.package_id})">Review</button>` : `<button onclick="tailor(${job.id}, this)">Tailor</button>`}<a href="${escapeAttr(job.url)}" target="_blank" rel="noopener">View</a></div>
    </div>`).join('') : '<p>No jobs yet. Use “Find jobs” or add one manually.</p>';
}

window.tailor = async (id, button) => {
  button.disabled = true; button.textContent = 'Tailoring…';
  try { const pkg = await api(`/api/jobs/${id}/tailor`, {method:'POST', body:'{}'}); await loadJobs(); review(pkg.id); }
  catch (error) { toast(error.message); button.disabled = false; button.textContent = 'Tailor'; }
};

window.review = async (id) => {
  try {
    const pkg = await api(`/api/packages/${id}`);
    $('#review-content').innerHTML = `<article><h3>${escapeHtml(pkg.title)} at ${escapeHtml(pkg.company)}</h3>
      <p><strong>Fit: ${pkg.fit_score}/100.</strong> ${escapeHtml(pkg.fit_summary)}</p>
      ${pkg.missing_requirements.length ? `<p class="warning"><strong>Gaps:</strong> ${pkg.missing_requirements.map(escapeHtml).join('; ')}</p>` : ''}
      <div class="artifact-actions">
        ${pkg.resume_pdf_path ? `<a class="button-link" href="/api/packages/${id}/artifacts/resume.pdf">Download resume PDF</a>` : ''}
        ${pkg.resume_path ? `<a class="button-link quiet-link" href="/api/packages/${id}/artifacts/resume.tex">Download LaTeX</a>` : ''}
        ${pkg.cover_letter_path ? `<a class="button-link quiet-link" href="/api/packages/${id}/artifacts/cover-letter.txt">Download cover letter</a>` : ''}
      </div>
      <h3>Tailored resume</h3><pre>${escapeHtml(pkg.tailored_resume)}</pre>
      <h3>Cover letter</h3><pre>${escapeHtml(pkg.cover_letter)}</pre>
      ${pkg.screening_notes.length ? `<h3>Screening notes</h3><ul>${pkg.screening_notes.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : ''}
      <div class="review-actions"><button onclick="decide(${id}, 'approved')">Approve</button><button class="danger" onclick="decide(${id}, 'rejected')">Reject</button></div>
      ${pkg.decision === 'approved' ? `<button class="quiet" onclick="handoff(${id}, '${escapeAttr(pkg.approval_token)}')">Open approved application</button>` : ''}
      <p class="meta">Nothing is submitted from this screen. Approval creates a scoped token required by any future site adapter.</p></article>`;
    $('#review').classList.remove('hidden'); $('#review').scrollIntoView({behavior:'smooth'});
  } catch (error) { toast(error.message); }
};

window.decide = async (id, decision) => {
  try { await api(`/api/packages/${id}/decision`, {method:'POST', body:JSON.stringify({decision})}); toast(`Application ${decision}`); await loadJobs(); review(id); }
  catch (error) { toast(error.message); }
};

window.handoff = async (id, approval_token) => {
  try { const result = await api(`/api/packages/${id}/prepare`, {method:'POST', body:JSON.stringify({approval_token})}); window.open(result.url, '_blank', 'noopener'); toast(result.message); }
  catch (error) { toast(error.message); }
};

async function loadYcCompanies() {
  const companies = await api('/api/yc/companies');
  $('#yc-count').textContent = `${companies.length} compan${companies.length === 1 ? 'y' : 'ies'}`;
  $('#yc-companies').innerHTML = companies.length ? companies.map(company => `
    <div class="job">
      <div><h3>${escapeHtml(company.name)}</h3><p>${escapeHtml(company.batch)} · ${escapeHtml(company.domain || 'No domain')}</p>
      <div class="meta">${escapeHtml(company.one_liner || '')} <span class="badge lane">Fit ${company.fit_score || 0}/100</span>${company.fit_reasons ? ` · ${escapeHtml(JSON.parse(company.fit_reasons || '[]').join(', '))}` : ''} <span class="badge ${company.outreach_decision || ''}">${escapeHtml(company.outreach_decision || 'not drafted')}</span>${company.contact_email ? ` · ${escapeHtml(company.contact_email)}` : ''}${company.sent_at ? ` · sent ${escapeHtml(company.sent_at)}` : ''}${company.bounced_contacts ? ` · <span class="badge rejected">${company.bounced_contacts} bounced</span>` : ''}${!company.pending_contacts && company.bounced_contacts ? ' · no aliases left' : ''}</div></div>
      <div class="actions">${company.outreach_id
        ? `<button class="quiet" onclick="sendOutreachNow(${company.outreach_id}, this)">Send now</button>`
        : `<button onclick="generateOutreach(${company.id}, this)" ${!company.pending_contacts && company.bounced_contacts ? 'disabled' : ''}>Draft outreach</button>`}</div>
    </div>`).join('') : '<p>No YC companies synced yet.</p>';
}

async function loadStartups() {
  const companies = await api('/api/startups');
  $('#startup-count').textContent = `${companies.length} compan${companies.length === 1 ? 'y' : 'ies'}`;
  $('#startups').innerHTML = companies.length ? companies.map(company => `
    <div class="job">
      <div><h3>${escapeHtml(company.name)}</h3><p>${escapeHtml(company.source)} · ${escapeHtml(company.stage || company.funding_signal || 'funding signal')}</p>
      <div class="meta">${escapeHtml(company.description || '')} <span class="badge lane">Fit ${company.fit_score || 0}/100</span>${company.fit_reasons ? ` · ${escapeHtml(JSON.parse(company.fit_reasons || '[]').join(', '))}` : ''}${company.contact_email ? ` · ${escapeHtml(company.contact_email)}` : ''}${company.pending_contacts ? ` · ${company.pending_contacts} contact candidates` : ''}${company.evidence_url ? ` · <a href="${escapeAttr(company.evidence_url)}" target="_blank" rel="noopener">Evidence</a>` : ''}</div></div>
      <div class="actions">${company.outreach_id
        ? `<span class="badge drafted">drafted</span>`
        : `<button onclick="generateStartupOutreach(${company.id}, this)" ${!company.pending_contacts ? 'disabled' : ''}>Draft outreach</button>`}</div>
    </div>`).join('') : '<p>No funded startups synced yet.</p>';
}

async function loadAutomationLog() {
  const events = await api('/api/automation/log');
  $('#automation-count').textContent = `${events.length} event${events.length === 1 ? '' : 's'}`;
  $('#automation-log').innerHTML = events.length ? events.map(event => `
    <div class="job">
      <div><h3>${escapeHtml(event.event)}</h3><p>${escapeHtml(event.entity_type)} #${event.entity_id} · ${escapeHtml(event.created_at)}</p>
      <div class="meta">${escapeHtml(JSON.stringify(event.details))}</div></div>
    </div>`).join('') : '<p>No automation activity yet.</p>';
}

window.generateOutreach = async (companyId, button) => {
  button.disabled = true; button.textContent = 'Drafting…';
  try { await api(`/api/yc/${companyId}/outreach`, {method:'POST', body:'{}'}); await loadYcCompanies(); toast('Outreach drafted'); }
  catch (error) { toast(error.message); button.disabled = false; button.textContent = 'Draft outreach'; }
};

window.sendOutreachNow = async (outreachId, button) => {
  button.disabled = true; button.textContent = 'Sending…';
  try { const result = await api(`/api/outreach/${outreachId}/send`, {method:'POST', body:'{}'}); await loadYcCompanies(); await loadAutomationLog(); toast(result.decision === 'sent' ? 'Outreach sent' : 'Dry run written (ENABLE_LIVE_OUTREACH is off)'); }
  catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = 'Send now'; }
};

window.generateStartupOutreach = async (companyId, button) => {
  button.disabled = true; button.textContent = 'Drafting…';
  try { await api(`/api/startups/${companyId}/outreach`, {method:'POST', body:'{}'}); await loadStartups(); toast('Startup outreach drafted'); }
  catch (error) { toast(error.message); button.disabled = false; button.textContent = 'Draft outreach'; }
};

function escapeHtml(value='') { const div=document.createElement('div'); div.textContent=String(value); return div.innerHTML; }
function escapeAttr(value='') { return escapeHtml(value).replaceAll('"','&quot;').replaceAll("'",'&#39;').replaceAll('`','&#96;'); }

$('#profile-form').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/profile',{method:'POST',body:JSON.stringify(formObject(event.target))}); await loadProfile(); toast('Profile saved locally'); } catch(error){toast(error.message);} });
$('#manual-form').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/jobs/manual',{method:'POST',body:JSON.stringify(formObject(event.target))}); event.target.reset(); await loadJobs(); toast('Job added'); } catch(error){toast(error.message);} });
$('#sync').addEventListener('click', async (event) => { event.target.disabled=true; event.target.textContent='Searching…'; try { const result=await api('/api/jobs/sync',{method:'POST',body:'{}'}); await loadJobs(); toast(`Found ${result.fetched}; added ${result.inserted}`); } catch(error){toast(error.message);} finally {event.target.disabled=false;event.target.textContent='Find jobs';} });
$('#yc-sync').addEventListener('click', async (event) => { event.target.disabled=true; event.target.textContent='Syncing…'; try { const result=await api('/api/yc/sync',{method:'POST',body:'{}'}); await loadYcCompanies(); toast(`Found ${result.fetched}; added ${result.inserted}`); } catch(error){toast(error.message);} finally {event.target.disabled=false;event.target.textContent='Sync YC companies';} });
$('#startup-sync').addEventListener('click', async (event) => { event.target.disabled=true; event.target.textContent='Syncing…'; try { const result=await api('/api/startups/sync',{method:'POST',body:'{}'}); await loadStartups(); toast(`Fetched ${result.fetched}; eligible ${result.eligible}`); } catch(error){toast(error.message);} finally {event.target.disabled=false;event.target.textContent='Sync funded startups';} });
$('#close-review').addEventListener('click', () => $('#review').classList.add('hidden'));
$('#job-filter').addEventListener('input', renderJobs);
$('#lane-summary').addEventListener('click', (event) => {
  const chip = event.target.closest('[data-lane]');
  if (!chip) return;
  $('#job-filter').value = chip.dataset.lane;
  renderJobs();
});
$('#region-summary').addEventListener('click', (event) => {
  const chip = event.target.closest('[data-region]');
  if (!chip) return;
  $('#job-filter').value = chip.dataset.region;
  renderJobs();
});

Promise.all([loadProfile(), loadContext(), loadJobs(), loadYcCompanies(), loadStartups(), loadAutomationLog()]).catch(error => toast(error.message));
