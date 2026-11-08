/**
 * Exact arithmetic on the API's decimal strings ("0.00073"). Money is never turned into a float:
 * the API keeps USD as plain decimal text, and so does this.
 */
const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

interface Parsed {
  units: bigint;
  scale: number;
}

function parse(value: string): Parsed {
  const match = DECIMAL.exec(value.trim());
  if (!match) throw new Error(`not a decimal string: ${JSON.stringify(value)}`);
  const sign = match[1];
  const whole = match[2] ?? "0";
  const fraction = match[3] ?? "";
  const units = BigInt(`${whole}${fraction}`);
  return { units: sign ? -units : units, scale: fraction.length };
}

function render(units: bigint, scale: number): string {
  const negative = units < 0n;
  const digits = (negative ? -units : units).toString().padStart(scale + 1, "0");
  const whole = digits.slice(0, digits.length - scale);
  const fraction = scale ? digits.slice(digits.length - scale).replace(/0+$/, "") : "";
  const text = fraction ? `${whole}.${fraction}` : whole;
  return negative && text !== "0" ? `-${text}` : text;
}

export function isDecimalString(value: unknown): value is string {
  return typeof value === "string" && DECIMAL.test(value.trim());
}

/** The exact sum of decimal strings, as a decimal string with trailing zeros removed. */
export function sumDecimals(values: readonly string[]): string {
  const parsed = values.map(parse);
  const scale = Math.max(0, ...parsed.map((item) => item.scale));
  const total = parsed.reduce((sum, item) => sum + item.units * 10n ** BigInt(scale - item.scale), 0n);
  return render(total, scale);
}

export function isZeroDecimal(value: string): boolean {
  return parse(value).units === 0n;
}
