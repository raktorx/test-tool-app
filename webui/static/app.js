(() => {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem('f2c-theme');
  const preferredDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = savedTheme || (preferredDark ? 'dark' : 'light');

  document.querySelectorAll('[data-theme-toggle]').forEach(button => {
    button.addEventListener('click', () => {
      root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
      localStorage.setItem('f2c-theme', root.dataset.theme);
    });
  });

  const sidebar = document.getElementById('sidebar');
  const scrim = document.getElementById('sidebar-scrim');
  const menu = document.getElementById('menu-toggle');
  const setMenu = open => {
    sidebar?.classList.toggle('open', open);
    scrim?.classList.toggle('open', open);
    menu?.setAttribute('aria-expanded', String(open));
  };
  menu?.addEventListener('click', () => setMenu(!sidebar.classList.contains('open')));
  scrim?.addEventListener('click', () => setMenu(false));

  document.querySelectorAll('.flash button').forEach(button => {
    button.addEventListener('click', () => button.closest('.flash')?.remove());
  });
  window.setTimeout(() => document.querySelector('.toast')?.remove(), 6000);

  document.querySelectorAll('.clickable-row[data-href]').forEach(row => {
    row.addEventListener('click', event => {
      if (!event.target.closest('a, button, input')) location.href = row.dataset.href;
    });
  });

  const networkLabel = document.getElementById('network-label');
  const renderNetwork = () => {
    document.body.classList.toggle('offline', !navigator.onLine);
    if (networkLabel) networkLabel.textContent = navigator.onLine ? 'Система доступна' : 'Нет подключения';
  };
  addEventListener('online', renderNetwork);
  addEventListener('offline', renderNetwork);
  renderNetwork();

  let installPrompt;
  const installButton = document.getElementById('install-app');
  addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    installPrompt = event;
    if (installButton) installButton.hidden = false;
  });
  installButton?.addEventListener('click', async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    await installPrompt.userChoice;
    installPrompt = null;
    installButton.hidden = true;
  });

  if ('serviceWorker' in navigator) {
    addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  }
})();
