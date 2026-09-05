// Presentation-only: do not use this on note saves, code or search indexes.
export function normalizeNumericRanges(text: string): string {
  const protectedSpans = /(```[\s\S]*?(?:```|(?![\s\S]))|~~~[\s\S]*?(?:~~~|(?![\s\S]))|`+[^`\n]*`+|^(?: {4}|\t)[^\n]*|https?:\/\/[^\s]+|!?\[[^\]\n]*\]\([^\)\n]*\)|<[^>\n]+>)/gm;
  const ranges = /(?<![A-Za-z0-9_.~～])([+-]?\d+(?:\.\d+)?)[ \t]*(?:\\?~|～)[ \t]*([+-]?\d+(?:\.\d+)?)(?![\d.~～])([ \t]*(?:天|小时|分钟|秒|周|个月|年|GB|MB|TB|KB|%))?/gi;
  return text.split(protectedSpans).map((part, index) => index % 2 ? part : part.replace(ranges, (_, low: string, high: string, suffix?: string) => {
    const unit = suffix?.trim() ?? "";
    return `${low}～${high}${/[\u4e00-\u9fff]/.test(unit) ? " " : ""}${unit}`;
  })).join("");
}
