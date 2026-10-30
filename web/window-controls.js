/* Native window controls live inside the existing application header. */
(function () {
  'use strict';
  const bridge = window.chrome?.webview;
  if (!bridge || window.top !== window) return;
  function init() {
    const controls = document.getElementById('window-controls');
    if (!controls) return;
    document.documentElement.dataset.desktop = 'true';
    controls.hidden = false;
    const post = action => bridge.postMessage({type: 'window', action});
    controls.addEventListener('click', event => {
      const button = event.target.closest('[data-window-action]');
      if (!button || !controls.contains(button)) return;
      const action = button.dataset.windowAction;
      if (['minimize', 'maximize', 'close'].includes(action)) post(action);
    });
    const header = document.querySelector('.topbar');
    const canDrag = event => !event.target.closest('button,input,textarea,select,a,[role="button"],label');
    header?.addEventListener('mousedown', event => {
      if (event.button !== 0 || !canDrag(event) || event.detail > 1) return;
      event.preventDefault();
      post('drag');
    });
    header?.addEventListener('dblclick', event => {if (event.button === 0 && canDrag(event)) post('maximize');});
    bridge.addEventListener('message', event => {
      if (event.data?.type !== 'window-state') return;
      const maximized = event.data.maximized === true;
      document.documentElement.dataset.maximized = String(maximized);
      const button = document.getElementById('window-maximize');
      button?.setAttribute('aria-label', maximized ? '还原窗口' : '最大化窗口');
      button?.setAttribute('title', maximized ? '还原窗口' : '最大化窗口');
      button?.setAttribute('aria-pressed', String(maximized));
    });
    const appearance = () => bridge.postMessage({type: 'appearance', theme: document.documentElement.dataset.skin === 'midnight' ? 'dark' : 'light'});
    new MutationObserver(appearance).observe(document.documentElement, {attributes: true, attributeFilter: ['data-skin']});
    appearance();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true}); else init();
})();
