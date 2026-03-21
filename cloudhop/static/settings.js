/* settings.js - CloudHop settings page */

function getCsrfToken() {
  return document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('csrf_token='))?.substring('csrf_token='.length) || '';
}

function showStatus(msg, type) {
  var el = document.getElementById('statusMsg');
  el.textContent = msg;
  el.className = 'status-msg ' + type;
  el.style.display = 'block';
  setTimeout(function() { el.style.display = 'none'; }, 5000);
}

function toggleEmailFields() {
  var enabled = document.getElementById('emailEnabled').checked;
  document.getElementById('emailFields').disabled = !enabled;
}

function loadSettings() {
  fetch('/api/settings')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      document.getElementById('emailEnabled').checked = !!data.email_enabled;
      document.getElementById('smtpHost').value = data.email_smtp_host || '';
      document.getElementById('smtpPort').value = data.email_smtp_port || 587;
      document.getElementById('smtpTls').checked = data.email_smtp_tls !== false;
      document.getElementById('emailFrom').value = data.email_from || '';
      document.getElementById('emailTo').value = data.email_to || '';
      document.getElementById('smtpUsername').value = data.email_smtp_username || '';
      document.getElementById('smtpPassword').value = '';
      document.getElementById('onComplete').checked = data.email_on_complete !== false;
      document.getElementById('onFailure').checked = data.email_on_failure !== false;
      toggleEmailFields();
    })
    .catch(function() { showStatus('Failed to load settings', 'error'); });
}

function saveSettings() {
  var body = {
    email_enabled: document.getElementById('emailEnabled').checked,
    email_smtp_host: document.getElementById('smtpHost').value,
    email_smtp_port: parseInt(document.getElementById('smtpPort').value, 10),
    email_smtp_tls: document.getElementById('smtpTls').checked,
    email_from: document.getElementById('emailFrom').value,
    email_to: document.getElementById('emailTo').value,
    email_smtp_username: document.getElementById('smtpUsername').value,
    email_on_complete: document.getElementById('onComplete').checked,
    email_on_failure: document.getElementById('onFailure').checked,
  };
  var pw = document.getElementById('smtpPassword').value;
  if (pw) body.email_password = pw;

  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken()},
    body: JSON.stringify(body)
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) showStatus('Settings saved', 'success');
      else showStatus(data.msg || 'Failed to save', 'error');
    })
    .catch(function() { showStatus('Network error', 'error'); });
}

function testEmail() {
  var body = {
    email_smtp_host: document.getElementById('smtpHost').value,
    email_smtp_port: parseInt(document.getElementById('smtpPort').value, 10),
    email_smtp_tls: document.getElementById('smtpTls').checked,
    email_from: document.getElementById('emailFrom').value,
    email_to: document.getElementById('emailTo').value,
    email_smtp_username: document.getElementById('smtpUsername').value,
  };
  var pw = document.getElementById('smtpPassword').value;
  if (pw) body.email_password = pw;

  fetch('/api/settings/test-email', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken()},
    body: JSON.stringify(body)
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) showStatus('Test email sent!', 'success');
      else showStatus(data.msg || 'Failed to send test email', 'error');
    })
    .catch(function() { showStatus('Network error', 'error'); });
}

/* Theme */
function _updateThemeIcons(isLight) {
  var dark = document.getElementById('theme-icon-dark');
  var light = document.getElementById('theme-icon-light');
  if (dark) dark.style.display = isLight ? 'none' : 'block';
  if (light) light.style.display = isLight ? 'block' : 'none';
}
function toggleTheme() {
  var html = document.documentElement;
  var current = html.getAttribute('data-theme');
  var next = current === 'light' ? 'dark' : 'light';
  document.body.style.transition = 'none';
  html.setAttribute('data-theme', next);
  _updateThemeIcons(next === 'light');
  localStorage.setItem('cloudhop-theme', next);
  requestAnimationFrame(function() { requestAnimationFrame(function() { document.body.style.transition = ''; }); });
}
(function() {
  var saved = localStorage.getItem('cloudhop-theme');
  if (!saved && window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
    document.documentElement.setAttribute('data-theme', 'light');
    _updateThemeIcons(true);
  } else if (saved === 'light') {
    _updateThemeIcons(true);
  }
})();

/* Init */
document.addEventListener('DOMContentLoaded', function() {
  loadSettings();
  document.getElementById('emailEnabled').addEventListener('change', toggleEmailFields);
  document.getElementById('btnSave').addEventListener('click', saveSettings);
  document.getElementById('btnTest').addEventListener('click', testEmail);
});
