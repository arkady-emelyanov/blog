// Click a figure's image to see it full size; × (top right), Esc or a click anywhere closes it.
// A static file because the CSP allows scripts from 'self' only.
document.addEventListener('DOMContentLoaded', () => {
  const imgs = document.querySelectorAll('figure img');
  if (!imgs.length) return;

  const dialog = document.createElement('dialog');
  dialog.className = 'lightbox';
  dialog.tabIndex = -1;
  const close = document.createElement('button');
  close.className = 'lightbox-close';
  close.type = 'button';
  close.setAttribute('aria-label', 'Close');
  close.textContent = '×';
  const big = document.createElement('img');
  dialog.append(close, big);
  document.body.append(dialog);

  dialog.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => document.documentElement.classList.remove('lightbox-open'));

  imgs.forEach((img) => {
    img.classList.add('zoomable');
    img.addEventListener('click', () => {
      big.src = img.currentSrc || img.src;
      big.alt = img.alt;
      document.documentElement.classList.add('lightbox-open');
      dialog.showModal();
      dialog.focus(); // not the ×, so it stays translucent until hovered
    });
  });
});
