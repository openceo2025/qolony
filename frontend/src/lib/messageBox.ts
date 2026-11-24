export function showMessageBox(message: string): void {
  if (typeof window === "undefined") return;
  window.alert(message);
}
