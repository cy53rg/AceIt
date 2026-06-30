export {};

declare global {
  interface Window {
    atlasShell?: {
      platform: string;
      isElectron: boolean;
      readClipboard: () => Promise<string>;
      onHighlightHotkey: (fn: () => void) => () => void;
    };
  }
}
