/* Local appearance only: no project, workflow or generation changes. */
(function (global) {
  'use strict';
  const key = 'creation-canvas-skin-v1';
  const names = {glass:'雾白玻璃',midnight:'月夜玻璃',sage:'青苔玻璃',sand:'暖砂玻璃'};
  let toggle, menu, nativePopover = false, opened = false, initialized = false;

  function apply(value, options = {}) {
    const skin = Object.hasOwn(names, value) ? value : 'glass';
    document.documentElement.dataset.skin = skin;
    if (options.persist !== false) {
      try { localStorage.setItem(key, skin); } catch { /* Appearance still works without storage. */ }
    }
    if (toggle) {
      toggle.title = '更换皮肤 · ' + names[skin];
      toggle.setAttribute('aria-label', '更换皮肤，当前' + names[skin]);
    }
    if (menu) menu.querySelectorAll('[data-skin-option]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.skinOption === skin));
    });
    return skin;
  }

  function syncOpen(value) {
    opened = value;
    toggle.setAttribute('aria-expanded', String(value));
    toggle.setAttribute('aria-pressed', String(value));
    menu.dataset.open = String(value);
  }

  function close(restoreFocus) {
    if (nativePopover) {
      if (opened) menu.hidePopover();
    } else menu.hidden = true;
    syncOpen(false);
    if (restoreFocus) toggle.focus();
  }

  function open() {
    if (nativePopover) menu.showPopover();
    else menu.hidden = false;
    syncOpen(true);
    menu.querySelector('[aria-pressed="true"]')?.focus();
  }

  function init() {
    if (initialized) return true;
    toggle = document.getElementById('skin-toggle');
    menu = document.getElementById('skin-menu');
    if (!toggle || !menu) return false;
    nativePopover = typeof menu.showPopover === 'function' && typeof menu.hidePopover === 'function';
    if (nativePopover) {
      menu.setAttribute('popover', 'auto');
      menu.hidden = false;
      toggle.setAttribute('popovertarget', 'skin-menu');
      toggle.setAttribute('popovertargetaction', 'toggle');
      menu.addEventListener('toggle', event => {
        syncOpen(event.newState === 'open');
        if (opened) menu.querySelector('[aria-pressed="true"]')?.focus();
      });
    } else menu.hidden = true;
    syncOpen(false);
    toggle.setAttribute('aria-controls', 'skin-menu');
    // The native invoker owns pointer toggles, so light dismissal cannot reopen the menu.
    toggle.addEventListener('click', () => { if (!nativePopover) opened ? close(false) : open(); });
    toggle.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); if (!opened) open(); }
    });
    menu.addEventListener('click', event => {
      const option = event.target.closest('[data-skin-option]');
      if (!option || !menu.contains(option)) return;
      apply(option.dataset.skinOption);
      close(true);
    });
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); close(true); return; }
      const buttons = Array.from(menu.querySelectorAll('[data-skin-option]'));
      const at = buttons.indexOf(document.activeElement);
      const direction = {ArrowDown:1,ArrowRight:1,ArrowUp:-1,ArrowLeft:-1}[event.key];
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : direction ? (at + direction + buttons.length) % buttons.length : -1;
      if (next >= 0) { event.preventDefault(); buttons[next]?.focus(); }
    });
    document.addEventListener('keydown', event => {
      if (opened && event.key === 'Escape') { event.preventDefault(); close(true); }
    });
    document.addEventListener('pointerdown', event => {
      if (!nativePopover && opened && !menu.contains(event.target) && !toggle.contains(event.target)) close(false);
    });
    global.addEventListener?.('storage', event => {
      if (event.key === key) apply(event.newValue, {persist:false});
    });
    apply(document.documentElement.dataset.skin, {persist:false});
    initialized = true;
    return true;
  }

  let saved;
  try { saved = localStorage.getItem(key); } catch { /* Default skin is always available. */ }
  apply(saved, {persist:false});
  global.CanvasSkins = {init,apply};
})(window);
