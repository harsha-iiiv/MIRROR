// Keyboard and focus behaviour shared by the studio's dialogs.
import { useEffect, useRef } from 'preact/hooks';

// Focus the dialog on open, close it on Escape, keep Tab inside it, and give focus back
// to whatever opened it when it closes.
export function useDialog(onClose) {
  const ref = useRef(null);
  useEffect(() => {
    const opener = document.activeElement;
    const el = ref.current;
    const focusables = () => [...(el?.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') || [])]
      .filter((x) => !x.disabled && x.offsetParent !== null);
    (focusables()[0] || el)?.focus?.();
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); return; }
      if (e.key !== 'Tab' || !el) return;
      const f = focusables();
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      if (opener && opener.focus && document.contains(opener)) opener.focus();
    };
  }, []);
  return ref;
}
