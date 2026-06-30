import { useEffect, useRef } from "react";
import { handleInput } from "../api/daemon";

async function readClipboardText(): Promise<string> {
  if (window.atlasShell?.readClipboard) {
    return (await window.atlasShell.readClipboard()).trim();
  }
  try {
    return (await navigator.clipboard.readText()).trim();
  } catch {
    return "";
  }
}

export function useClipboardHighlight(
  active: boolean,
  onHighlight: (text: string) => void,
) {
  const lastRef = useRef("");

  useEffect(() => {
    if (!active) {
      lastRef.current = "";
      return;
    }

    const tick = async () => {
      const text = await readClipboardText();
      if (!text || text === lastRef.current) return;
      lastRef.current = text;
      onHighlight(text);
      try {
        await handleInput(text, "highlight");
      } catch (err) {
        onHighlight(`[highlight error] ${String(err)}`);
      }
    };

    const id = window.setInterval(() => void tick(), 800);
    return () => clearInterval(id);
  }, [active, onHighlight]);
}

export function useHighlightHotkey(onToggle: () => void) {
  useEffect(() => {
    const off = window.atlasShell?.onHighlightHotkey?.(onToggle);
    return () => off?.();
  }, [onToggle]);
}
