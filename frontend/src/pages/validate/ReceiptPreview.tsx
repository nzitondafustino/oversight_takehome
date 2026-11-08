import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatInteger } from "@/lib/format";
import type { ReceiptPreview as Preview } from "./receipts";

const SHOWN = 50;

function Unavailable() {
  return <span className="text-xs italic text-subtle-foreground">Unavailable</span>;
}

/** What will be sent, read from the input. Missing fields stay unavailable: nothing is filled in. */
export function ReceiptPreview({ previews }: { previews: Preview[] }) {
  if (previews.length === 0) return null;
  const currencies = [...new Set(previews.map((item) => item.currency).filter((value): value is string => Boolean(value)))];
  const lines = previews.reduce((sum, item) => sum + item.lineCount, 0);
  return (
    <section aria-label="Preview" className="mt-4">
      <p className="mb-1.5 text-xs font-medium text-foreground">
        Preview · {formatInteger(previews.length)} receipt{previews.length === 1 ? "" : "s"} · {formatInteger(lines)} line items ·{" "}
        {currencies.length ? currencies.join(", ") : "no currency given"}
      </p>
      <div className="max-h-56 overflow-y-auto rounded-md border border-border">
        <Table>
          <caption className="sr-only">Receipts to validate</caption>
          <TableHeader className="sticky top-0 z-10 bg-muted">
            <TableRow className="hover:bg-transparent">
              <TableHead className="h-8 px-3">Receipt</TableHead>
              <TableHead className="h-8 px-3">Merchant</TableHead>
              <TableHead className="h-8 px-3">Date and time (local)</TableHead>
              <TableHead className="h-8 px-3">Currency</TableHead>
              <TableHead className="h-8 px-3 text-right">Total</TableHead>
              <TableHead className="h-8 px-3 text-right">Lines</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {previews.slice(0, SHOWN).map((item) => (
              <TableRow key={item.receiptId}>
                <TableCell className="whitespace-nowrap px-3 py-1.5 font-mono text-xs font-medium">{item.receiptId}</TableCell>
                <TableCell className="px-3 py-1.5 text-xs">{item.merchant ?? <Unavailable />}</TableCell>
                <TableCell className="tabular whitespace-nowrap px-3 py-1.5 font-mono text-xs">{item.datetime ?? <Unavailable />}</TableCell>
                <TableCell className="px-3 py-1.5 text-xs">{item.currency ?? <Unavailable />}</TableCell>
                <TableCell className="tabular px-3 py-1.5 text-right text-xs">{item.total ?? <Unavailable />}</TableCell>
                <TableCell className="tabular px-3 py-1.5 text-right text-xs">{formatInteger(item.lineCount)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {previews.length > SHOWN ? <p className="mt-1 text-xs text-subtle-foreground">And {formatInteger(previews.length - SHOWN)} more, all of which will be sent.</p> : null}
    </section>
  );
}
