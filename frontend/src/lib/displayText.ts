const EXCEL_ESCAPED_CR_PATTERN = /_x000D_/gi;

export function formatDisplayText(value: string | null | undefined): string {
  return (value ?? "").replace(EXCEL_ESCAPED_CR_PATTERN, "\n");
}
