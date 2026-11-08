/**
 * A JSON parser and serializer that keeps every number literal exactly as written.
 *
 * The API reads money and rates as exact decimals (backend/src/expense_api/api/json_body.py), and the
 * core's input contract limits decimal places. JSON.parse would turn 19.990 into the float 19.99 and
 * 0.1000000000000000055 into 0.1, silently changing what is submitted. Here a number stays a JsonNumber
 * holding its source text, and serializing writes that text back unchanged.
 *
 * Like the API, it refuses repeated keys, NaN and Infinity.
 */

export class JsonNumber {
  constructor(readonly text: string) {}
  toString() {
    return this.text;
  }
}

export type JsonValue = null | boolean | string | JsonNumber | JsonValue[] | JsonObject;
export interface JsonObject {
  [key: string]: JsonValue;
}

export class JsonSyntaxError extends Error {
  readonly line: number;
  readonly column: number;
  constructor(message: string, source: string, position: number) {
    const before = source.slice(0, position);
    const line = before.split("\n").length;
    const column = position - before.lastIndexOf("\n");
    super(`${message} at line ${line}, column ${column}`);
    this.name = "JsonSyntaxError";
    this.line = line;
    this.column = column;
  }
}

const NUMBER = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
const ESCAPES: Record<string, string> = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };

export function parseJson(source: string): JsonValue {
  let i = 0;

  const fail = (message: string, at = i): never => {
    throw new JsonSyntaxError(message, source, at);
  };
  const skip = () => {
    while (i < source.length && " \t\n\r".includes(source[i]!)) i += 1;
  };
  const describe = () => (i >= source.length ? "Unexpected end of input" : `Unexpected ${JSON.stringify(source[i])}`);

  function string(): string {
    const start = i;
    i += 1; // opening quote
    let out = "";
    while (i < source.length) {
      const ch = source[i]!;
      if (ch === '"') {
        i += 1;
        return out;
      }
      if (ch === "\\") {
        const next = source[i + 1];
        if (next === "u") {
          const hex = source.slice(i + 2, i + 6);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail("Invalid \\u escape");
          out += String.fromCharCode(parseInt(hex, 16));
          i += 6;
        } else if (next !== undefined && next in ESCAPES) {
          out += ESCAPES[next];
          i += 2;
        } else {
          fail("Invalid escape in string", i);
        }
        continue;
      }
      if (ch < " ") fail("Control character in string (use \\n or \\t)");
      out += ch;
      i += 1;
    }
    return fail("Unterminated string", start);
  }

  function value(): JsonValue {
    skip();
    const ch = source[i];
    if (ch === "{") return object();
    if (ch === "[") return array();
    if (ch === '"') return string();
    if (source.startsWith("true", i)) return (i += 4), true;
    if (source.startsWith("false", i)) return (i += 5), false;
    if (source.startsWith("null", i)) return (i += 4), null;
    NUMBER.lastIndex = i;
    const match = NUMBER.exec(source);
    if (match) {
      i += match[0].length;
      return new JsonNumber(match[0]);
    }
    if (source.startsWith("NaN", i) || source.startsWith("Infinity", i) || source.startsWith("-Infinity", i)) fail("NaN and Infinity are not JSON numbers");
    return fail(describe());
  }

  function object(): JsonObject {
    i += 1;
    const out: JsonObject = Object.create(null);
    skip();
    if (source[i] === "}") return (i += 1), out;
    for (;;) {
      skip();
      if (source[i] !== '"') fail(`${describe()}: expected a property name in double quotes`);
      const keyAt = i;
      const key = string();
      if (Object.prototype.hasOwnProperty.call(out, key)) fail(`Repeated key ${JSON.stringify(key)}`, keyAt);
      skip();
      if (source[i] !== ":") fail(`${describe()}: expected ':'`);
      i += 1;
      out[key] = value();
      skip();
      if (source[i] === ",") {
        i += 1;
        continue;
      }
      if (source[i] === "}") return (i += 1), out;
      fail(`${describe()}: expected ',' or '}'`);
    }
  }

  function array(): JsonValue[] {
    i += 1;
    const out: JsonValue[] = [];
    skip();
    if (source[i] === "]") return (i += 1), out;
    for (;;) {
      out.push(value());
      skip();
      if (source[i] === ",") {
        i += 1;
        continue;
      }
      if (source[i] === "]") return (i += 1), out;
      fail(`${describe()}: expected ',' or ']'`);
    }
  }

  const result = value();
  skip();
  if (i < source.length) fail(`${describe()} after the JSON value`);
  return result;
}

/** Serializes a parsed value. Numbers are written exactly as they were read. */
export function stringifyJson(value: JsonValue, indent = 0, depth = 0): string {
  if (value === null) return "null";
  if (value instanceof JsonNumber) return value.text;
  if (typeof value === "boolean") return String(value);
  if (typeof value === "string") return JSON.stringify(value);
  const pad = indent ? "\n" + " ".repeat(indent * (depth + 1)) : "";
  const end = indent ? "\n" + " ".repeat(indent * depth) : "";
  const sep = indent ? ": " : ":";
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return `[${value.map((item) => pad + stringifyJson(item, indent, depth + 1)).join(",")}${end}]`;
  }
  const keys = Object.keys(value);
  if (keys.length === 0) return "{}";
  return `{${keys.map((key) => `${pad}${JSON.stringify(key)}${sep}${stringifyJson(value[key]!, indent, depth + 1)}`).join(",")}${end}}`;
}

export function isJsonObject(value: JsonValue | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value) && !(value instanceof JsonNumber);
}

/** A scalar as display text, without converting numbers: 19.990 stays "19.990". */
export function scalarText(value: JsonValue | undefined): string | null {
  if (value === undefined || value === null) return null;
  if (value instanceof JsonNumber) return value.text;
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  return null;
}

/**
 * A request body as JSON text: plain values as JSON.stringify writes them, and each JsonNumber exactly as it was
 * read, so a policy artifact or an approval record parsed with parseJson reaches the API unchanged. Undefined
 * object properties are left out, as JSON.stringify leaves them out.
 */
export function toJsonText(value: unknown): string {
  if (value instanceof JsonNumber) return value.text;
  if (typeof value === "number" && !Number.isFinite(value)) throw new TypeError("NaN and Infinity are not JSON numbers");
  if (Array.isArray(value)) return `[${value.map((item) => toJsonText(item ?? null)).join(",")}]`;
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value).filter(([, item]) => item !== undefined);
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${toJsonText(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
