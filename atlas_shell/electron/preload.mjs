import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("atlasShell", {
  platform: process.platform,
  isElectron: true,
  readClipboard: () => ipcRenderer.invoke("clipboard-read"),
  onHighlightHotkey: (fn) => {
    const listener = () => fn();
    ipcRenderer.on("highlight-hotkey", listener);
    return () => ipcRenderer.removeListener("highlight-hotkey", listener);
  },
});
