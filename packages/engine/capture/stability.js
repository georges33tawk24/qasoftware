// Injected for screenshots only (SPEC §5, "freeze animations").
(() => {
  const style = document.createElement("style");
  style.id = "__bureau_freeze";
  style.textContent = `*, *::before, *::after {
    animation-play-state: paused !important;
    animation-delay: -1ms !important;
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0s !important;
    transition-delay: 0s !important;
    caret-color: transparent !important;
    scroll-behavior: auto !important;
  }`;
  document.documentElement.appendChild(style);
})();
